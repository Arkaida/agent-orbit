"""Observed session/union timing and human-response episodes.

Only genuine collector frames commit time. Display refreshes project a currently
fresh, known state without changing the ledger. Unknown/offline/error intervals
are never backfilled. This is monitoring coverage, not task or model efficiency.
"""
import math
from uuid import uuid4


ELIGIBLE = ('running', 'waiting', 'attention')
WAITING = ('waiting', 'attention')
HUMAN_ISSUES = ('', 'approval', 'question', 'goal')


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _key(row, source=None):
    source = source or row.get('source', 'remote')
    return source, row.get('group_id') or source + ':' + str(row.get('session', row.get('pane_id', '')))


def _visual(row):
    if row.get('available') is False:
        return 'offline'
    state = row.get('state', 'unknown')
    if (row.get('attention_level') == 'error' or
            state in ('error', 'interrupted', 'stopped', 'missing') or
            (row.get('error_kind') or '') not in HUMAN_ISSUES):
        return 'error'
    if state == 'unknown':
        return 'unknown'
    # A warning or a question alongside an actual running task is not idle.
    if state == 'running':
        return 'running'
    if state in ('approval', 'input', 'paused'):
        return 'attention'
    return 'waiting' if state == 'idle' else 'unknown'


def _processes(rows):
    identities = set()
    for row in rows:
        telemetry = row.get('telemetry') or {}
        identity = telemetry.get('identity') if isinstance(telemetry, dict) else None
        pid = row.get('process_id')
        if pid is not None or identity:
            identities.add(('process', str(pid), str(identity or '')))
        else:
            identities.add(('pane', str(row.get('pane_id', ''))))
    return tuple(sorted(identities))


def _health(health):
    if health is None:
        return None
    if isinstance(health, list):
        return {item['id']: item.get('connected') is True for item in health}
    if 'connected' in health:
        return {'*': health.get('connected') is True}
    return {key: value.get('connected') is True if isinstance(value, dict) else value is True
            for key, value in health.items()}


def _settings(settings):
    settings = settings or {}
    minutes = settings.get('wait_reminder_minutes', 5)
    if not _finite(minutes) or not 1 <= minutes <= 120:
        minutes = 5
    return settings.get('wait_reminder_enabled', True) is True, minutes * 60


def _empty(kind='unknown'):
    return dict(kind=kind, episode_id=None, since=None, elapsed_seconds=None,
                observed=False, overdue=False, reminder_suppressed=False,
                idle_seconds=0.0, run_seconds=0.0, idle_run_ratio=None,
                efficiency_percent=None, scope='observed_monitoring')


def _timing(saved, now, project=False):
    idle = saved.get('idle_seconds', 0.0) if saved else 0.0
    running = saved.get('run_seconds', 0.0) if saved else 0.0
    if saved and project and _finite(now) and now >= saved['at']:
        delta = now - saved['at']
        if saved['kind'] == 'running':
            running += delta
        elif saved['kind'] in WAITING:
            idle += delta
    return dict(idle_seconds=round(idle, 2), run_seconds=round(running, 2),
                idle_run_ratio=round(idle / running, 3) if running else None,
                efficiency_percent=round(running / (idle + running) * 100, 2) if idle + running else None,
                scope='observed_monitoring')


def _integrate(saved, kind, now):
    if saved['kind'] in ELIGIBLE and kind in ELIGIBLE and now > saved['at']:
        field = 'run_seconds' if saved['kind'] == 'running' else 'idle_seconds'
        saved[field] += now - saved['at']


def _episode(saved, kind, now_mono, now_wall, reset=False):
    previous = None if reset else saved.get('kind')
    if kind in WAITING:
        if previous != kind:
            saved.update(episode_id=uuid4().hex, since=now_wall, start=now_mono,
                         observed=True, expected_completion=kind == 'waiting' and previous == 'running')
    elif kind == 'running':
        if previous != 'running':
            saved.update(since=now_wall, start=now_mono)
        saved.update(episode_id=None, observed=True, expected_completion=False)
    else:
        saved.update(episode_id=None, since=None, start=None,
                     observed=False, expected_completion=False)
    saved['kind'] = kind


def _response(saved, now, enabled, threshold, kind=None):
    kind = saved['kind'] if kind is None and saved else kind or 'unknown'
    response = _empty(kind)
    same = saved is not None and kind == saved['kind'] and kind in ELIGIBLE
    response.update(_timing(saved, now, project=same))
    if same:
        elapsed = max(0, now - saved['start'])
        response.update(episode_id=saved['episode_id'], since=saved['since'],
                        elapsed_seconds=round(elapsed, 2), observed=saved['observed'],
                        overdue=kind in WAITING and enabled and elapsed >= threshold)
    return response


class SessionResponseTracker:
    PRIORITY = {'error': 0, 'attention': 1, 'unknown': 2,
                'running': 3, 'waiting': 4, 'offline': 5}

    def __init__(self, max_gap_seconds=2):
        self.max_gap = max_gap_seconds
        self.groups = {}  # source + bridge group_id, one monitored session.
        self.sources = {}
        self.blocked = set()
        self.blocked_groups = set()
        self.definitions = {}
        self.response_groups = {}
        self.membership = {}

    def configure(self, settings=None):
        settings = settings or {}
        # A queued frame from another source can arrive before annotate prunes
        # a disabled source's cached rows. Stop considering those rows live now;
        # re-enabling waits for a genuine observe to resume without backfilling.
        for source in ('remote', 'local'):
            if settings.get(source + '_enabled') is False:
                self.blocked.add(source)
        definitions, membership = {}, {}
        for item in settings.get('session_groups', []):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                continue
            members = item.get('members')
            if not isinstance(members, list):
                continue
            members = tuple(dict.fromkeys(member for member in members
                                          if isinstance(member, str) and member and member not in membership))
            if not members or item['id'] in definitions:
                continue
            definition = dict(id=item['id'], name=str(item.get('name') or item['id']), members=members)
            old = self.definitions.get(item['id'])
            if old is not None and set(old['members']) != set(members):
                self.response_groups.pop(item['id'], None)
            definitions[item['id']] = definition
            membership.update((member, item['id']) for member in members)
        self.definitions, self.membership = definitions, membership
        self.response_groups = {key: value for key, value in self.response_groups.items() if key in definitions}

    def _source_live(self, source, now, health=None):
        sample = self.sources.get(source)
        return (source not in self.blocked and sample is not None and _finite(now) and
                0 <= now - sample['at'] <= sample['gap'] and
                (health is None or health.get(source, health.get('*', False))))

    def observe(self, source, rows, now_mono, now_wall, epoch=0, max_gap_seconds=None, selection=None):
        if not _finite(now_mono) or not _finite(now_wall):
            return
        gap = self.max_gap if max_gap_seconds is None else max_gap_seconds
        old_source = self.sources.get(source)
        scope_reset = old_source is not None and old_source['selection'] != selection
        reset = (source in self.blocked or old_source is None or old_source['epoch'] != epoch or
                 now_mono <= old_source['at'] or now_mono - old_source['at'] > gap)
        self.blocked.discard(source)
        self.sources[source] = dict(epoch=epoch, at=now_mono, gap=gap, selection=selection)
        grouped = {}
        for row in rows:
            grouped.setdefault(_key(row, source), []).append(row)
        self.groups = {key: value for key, value in self.groups.items() if key[0] != source or key in grouped}
        for key, members in grouped.items():
            visual = min((_visual(row) for row in members), key=self.PRIORITY.get)
            kind = 'unknown' if visual == 'error' else visual
            signature = _processes(members)
            saved = self.groups.get(key)
            identity_reset = saved is not None and saved['processes'] != signature
            if saved is None or identity_reset or scope_reset:
                saved = dict(kind='unknown', processes=signature, episode_id=None,
                             since=None, start=None, observed=False, at=now_mono,
                             prior_completions=set(), expected_completion=False,
                             idle_seconds=0.0, run_seconds=0.0, continuity_id=uuid4().hex)
                current_reset = True
            else:
                current_reset = reset or key in self.blocked_groups
                if not current_reset:
                    _integrate(saved, kind, now_mono)
                else:
                    saved['continuity_id'] = uuid4().hex
            self.blocked_groups.discard(key)
            completion_ids = {row['completion_id'] for row in members
                              if isinstance(row.get('completion_id'), str) and row['completion_id']}
            _episode(saved, kind, now_mono, now_wall, reset=current_reset)
            if kind == 'waiting' and saved['expected_completion']:
                for row in members:
                    cid, completed = row.get('completion_id'), row.get('completed_at')
                    if (_visual(row) != 'waiting' or not isinstance(cid, str) or not cid or
                            cid in saved['prior_completions'] or not _finite(completed)):
                        continue
                    if 0 <= saved['since'] - completed <= gap:
                        offset = max(0, saved['since'] - completed)
                        saved.update(since=completed, start=saved['start'] - offset,
                                     observed=False, expected_completion=False)
                        break
            if kind != 'waiting':
                saved['prior_completions'] = completion_ids
            saved.update(processes=signature, selection=selection, at=now_mono)
            self.groups[key] = saved
        self._observe_groups(now_mono, now_wall)

    def _group_state(self, definition, now, health=None):
        states, identities, continuity = [], {}, {}
        for member in definition['members']:
            source = member.split(':', 1)[0]
            key = source, member
            saved = self.groups.get(key)
            if saved is not None:
                identities[member] = (saved['processes'], saved['selection'])
                continuity[member] = saved['continuity_id']
            if saved is None or key in self.blocked_groups or not self._source_live(source, now, health):
                states.append('offline')
            else:
                states.append(saved['kind'])
        known = sum(state in ELIGIBLE for state in states)
        running = states.count('running')
        if running:
            kind = 'running'
        elif known == len(definition['members']) and all(state in WAITING for state in states):
            kind = 'attention' if 'attention' in states else 'waiting'
        else:
            kind = 'offline' if 'offline' in states else 'unknown'
        return kind, known, running, identities, continuity

    def _observe_groups(self, now_mono, now_wall):
        for gid, definition in self.definitions.items():
            kind, _, _, identities, continuity = self._group_state(definition, now_mono)
            saved = self.response_groups.get(gid)
            identity_reset = saved is not None and any(
                member in saved['identities'] and saved['identities'][member] != value
                for member, value in identities.items())
            if saved is None or identity_reset:
                saved = dict(kind='unknown', episode_id=None, since=None, start=None,
                             observed=False, at=now_mono, idle_seconds=0.0, run_seconds=0.0,
                             identities={}, continuity={})
                reset = True
            else:
                reset = any(member in saved['continuity'] and saved['continuity'][member] != value
                            for member, value in continuity.items())
                if not reset:
                    _integrate(saved, kind, now_mono)
            _episode(saved, kind, now_mono, now_wall, reset=reset)
            saved['identities'].update(identities)
            saved['continuity'].update(continuity)
            saved['at'] = now_mono
            self.response_groups[gid] = saved

    def annotate(self, rows, now, health=None, settings=None):
        """Project known timing only; no display refresh creates an episode."""
        if settings is not None:
            self.configure(settings)
        health = _health(health)
        enabled, threshold = _settings(settings)
        active = {_key(row) for row in rows}
        self.groups = {key: value for key, value in self.groups.items() if key in active}
        self.blocked_groups.intersection_update(active)
        grouped = {}
        for row in rows:
            grouped.setdefault(_key(row), []).append(row)
        responses = {}
        for key, members in grouped.items():
            source = key[0]
            source_live = self._source_live(source, now, health)
            live = source_live and any(row.get('available') is not False for row in members)
            saved = self.groups.get(key)
            if not live:
                if not source_live:
                    self.blocked.add(source)
                else:
                    self.blocked_groups.add(key)
                response = _response(saved, now, enabled, threshold, kind='offline')
            else:
                response = _response(saved, now, enabled, threshold)
            if key[1] in self.membership:
                response.update(overdue=False, reminder_suppressed=True)
            responses[key] = response
        group_responses = {}
        for gid, definition in self.definitions.items():
            kind, known, running, _, _ = self._group_state(definition, now, health)
            saved = self.response_groups.get(gid)
            if saved is None or kind in ELIGIBLE and kind != saved['kind']:
                kind = 'unknown'
            response = _response(saved, now, enabled, threshold, kind=kind)
            response.update(id=gid, name=definition['name'], members=list(definition['members']),
                            count=len(definition['members']), known_members=known,
                            running_members=running, parallel_conflict=running > 1)
            group_responses[gid] = response
        return [dict(row, response=dict(responses[_key(row)]),
                     response_group=dict(group_responses[self.membership[_key(row)[1]]])
                     if _key(row)[1] in self.membership else None) for row in rows]
