"""Aggregate reported root-session usage independently of visual activity.

Only ``observe`` accepts a fresh collector frame. ``aggregate`` may run at UI
refresh speed; it never turns a repaint or a connection backlog into usage.
All cumulative counters originate in complete root logs, never screen bytes.
"""
from collections import deque
import math


TOKEN_FIELDS = ('total_tokens', 'input_tokens', 'output_tokens',
                'cached_input_tokens', 'cache_write_input_tokens')
CORE_FIELDS = ('total_tokens', 'input_tokens', 'output_tokens')


def _counter(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _identity(row):
    telemetry = row.get('telemetry')
    if not isinstance(telemetry, dict) or telemetry.get('scope') != 'root_session':
        return None
    identity = telemetry.get('identity')
    return identity if isinstance(identity, str) and identity else None


def _group(row, source):
    return row.get('group_id') or source + ':' + str(row.get('session', row.get('pane_id', '')))


def _usage_complete(telemetry):
    return (telemetry.get('usage_available') is True and
            telemetry.get('usage_complete') is True and
            all(_counter(telemetry.get(field)) for field in CORE_FIELDS))


def _tools_complete(telemetry):
    return telemetry.get('tools_complete') is True and _counter(telemetry.get('tool_completions'))


class UsageTracker:
    def __init__(self, window_seconds=30, work_window_seconds=60, max_gap_seconds=2):
        self.window = int(window_seconds)
        self.work_window = int(work_window_seconds)
        if self.window <= 0 or self.work_window <= 0:
            raise ValueError('Usage windows must be positive')
        self.max_gap = max_gap_seconds
        self.records = {}
        self.source_epochs = {}
        self.source_samples = {}
        self.source_gaps = {}

    @staticmethod
    def _clear(record, kind):
        record[kind + '_baseline'] = None
        record[kind + '_ready'] = False
        record[kind + '_samples'].clear()

    def break_continuity(self, source):
        for (record_source, _), record in self.records.items():
            if record_source == source:
                self._clear(record, 'usage')
                self._clear(record, 'tools')

    @staticmethod
    def _record():
        return dict(tokens=None, tools=None, usage_baseline=None,
                    tools_baseline=None, usage_ready=False, tools_ready=False,
                    usage_samples=deque(), tools_samples=deque(), observed_at=None)

    def observe(self, source, rows, now, epoch=0, max_gap_seconds=None):
        """Accept one actual, already filtered collector snapshot for a source."""
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            return
        gap = self.max_gap if max_gap_seconds is None else max_gap_seconds
        self.source_gaps[source] = gap
        previous_time = self.source_samples.get(source)
        if (source not in self.source_epochs or self.source_epochs[source] != epoch or
                (previous_time is not None and
                 (now <= previous_time or now - previous_time > gap))):
            self.break_continuity(source)
        self.source_epochs[source] = epoch
        self.source_samples[source] = now
        candidates = {}
        for row in rows:
            identity = _identity(row)
            if identity is not None and row.get('available') is not False and row.get('state') != 'missing':
                candidates.setdefault((source, identity), []).append(row['telemetry'])
        for key, record in self.records.items():
            if key[0] == source and key not in candidates:
                self._clear(record, 'usage')
                self._clear(record, 'tools')
        for key, choices in candidates.items():
            record = self.records.setdefault(key, self._record())
            # A root may be visible in several panes. Prefer a complete and
            # newer cumulative record, once per identity, not once per pane.
            usage = max(choices, key=lambda item: (_usage_complete(item),
                        item.get('total_tokens') if _counter(item.get('total_tokens')) else -1))
            tools = max(choices, key=lambda item: (_tools_complete(item),
                        item.get('tool_completions') if _counter(item.get('tool_completions')) else -1))
            if _usage_complete(usage):
                values = {field: usage.get(field) if _counter(usage.get(field)) else None
                          for field in TOKEN_FIELDS}
                previous = record['usage_baseline']
                reset = previous is not None and any(values[field] < previous[field] for field in CORE_FIELDS)
                if reset:
                    self._clear(record, 'usage')
                elif previous is not None:
                    record['usage_samples'].append((now, values['total_tokens'] - previous['total_tokens'],
                                                   values['output_tokens'] - previous['output_tokens']))
                    record['usage_ready'] = True
                record['tokens'] = values
                record['usage_baseline'] = values
            else:
                # Incomplete rescans retain the last confirmed cumulative
                # counters, but cannot sustain a real-time rate baseline.
                self._clear(record, 'usage')
            if _tools_complete(tools):
                value = tools['tool_completions']
                previous = record['tools_baseline']
                if previous is not None and value < previous:
                    self._clear(record, 'tools')
                elif previous is not None:
                    record['tools_samples'].append((now, value - previous))
                    record['tools_ready'] = True
                record['tools'] = value
                record['tools_baseline'] = value
            else:
                self._clear(record, 'tools')
            record['observed_at'] = now

    def aggregate(self, rows, now, connections=None):
        """Return JSON-safe totals and fixed-window rates for current targets.

        Cumulative coverage includes previously complete cached/offline logs.
        Live coverage requires two continuous, complete collector samples.
        Unknown roots still occupy one coverage slot per monitored group.
        """
        health = ({connection['id']: connection.get('connected') is True for connection in connections}
                  if connections is not None else {})
        def live(row):
            source = row.get('source', 'remote')
            return row.get('available') is not False and health.get(source, connections is None)
        for source in self.source_epochs:
            last = self.source_samples.get(source)
            if (not health.get(source, connections is None) or last is None or
                    now < last or now - last > self.source_gaps.get(source, self.max_gap)):
                self.break_continuity(source)
        groups = {}
        for row in rows:
            source = row.get('source', 'remote')
            groups.setdefault((source, _group(row, source)), []).append(row)
        targets = {}
        known = running = 0
        # Same conservative priority as the visible grouped state. An unknown
        # or missing pane cannot silently turn its group into a known idle row.
        priority = {'missing': 0, 'error': 0, 'interrupted': 0, 'stopped': 0,
                    'approval': 1, 'input': 1, 'paused': 1, 'unknown': 2,
                    'running': 3, 'idle': 4}
        for group_key, members in groups.items():
            online = [row for row in members if live(row)]
            if online:
                def state(row):
                    raw = row.get('state', 'unknown')
                    if raw == 'missing':
                        return raw
                    if row.get('attention_level') == 'error':
                        return 'error'
                    if raw not in priority:
                        return 'unknown'
                    if raw != 'unknown' and row.get('attention_level') == 'warning':
                        return 'approval'
                    return raw
                selected_state = min((state(row) for row in online), key=lambda value: priority[value])
                if selected_state not in ('unknown', 'missing'):
                    known += 1
                    running += selected_state == 'running'
            identified = False
            for row in members:
                identity = _identity(row)
                if identity is not None:
                    identified = True
                    key = (row.get('source', 'remote'), identity)
                    targets.setdefault(key, []).append(row)
            if not identified:
                targets[('unidentified',) + group_key] = members

        covered = tools_covered = live_usage = live_tools = 0
        totals = {field: [] for field in TOKEN_FIELDS}
        consumption = upstream = tool_rate = 0
        for key, members in targets.items():
            record = self.records.get(key)
            if record is None:
                continue
            if record['tokens'] is not None:
                covered += 1
                for field in TOKEN_FIELDS:
                    totals[field].append(record['tokens'][field])
            if record['tools'] is not None:
                tools_covered += 1
            is_live = any(live(row) and row.get('state') != 'missing' for row in members)
            if not is_live:
                self._clear(record, 'usage')
                self._clear(record, 'tools')
                continue
            while record['usage_samples'] and record['usage_samples'][0][0] <= now - self.window:
                record['usage_samples'].popleft()
            while record['tools_samples'] and record['tools_samples'][0][0] <= now - self.work_window:
                record['tools_samples'].popleft()
            if record['usage_ready']:
                live_usage += 1
                consumption += sum(sample[1] for sample in record['usage_samples']) / self.window
                upstream += sum(sample[2] for sample in record['usage_samples']) / self.window
            if record['tools_ready']:
                live_tools += 1
                tool_rate += sum(sample[1] for sample in record['tools_samples']) * 60 / self.work_window
        result = dict(scope='followed_root_sessions', window_seconds=self.window,
                      work_window_seconds=self.work_window, monitored_sessions=len(groups),
                      known_sessions=known, running_sessions=running,
                      utilization_percent=round(running / known * 100, 2) if known else None,
                      usage_sessions=len(targets), usage_covered=covered, tools_covered=tools_covered,
                      live_usage_covered=live_usage, live_tools_covered=live_tools,
                      consumption_per_second=round(consumption, 3) if live_usage else None,
                      upstream_tokens_per_second=round(upstream, 3) if live_usage else None,
                      tools_per_minute=round(tool_rate, 3) if live_tools else None)
        # An unknown cache counter must not silently become zero. Per-column
        # null also avoids presenting a partially covered cache sum as a total.
        result.update({field: sum(values) if values and all(value is not None for value in values) else None
                       for field, values in totals.items()})
        # Current rows retain offline/missing cached roots. Roots no longer
        # selected or replaced by a new identity do not accumulate forever.
        self.records = {key: record for key, record in self.records.items() if key in targets}
        return result
