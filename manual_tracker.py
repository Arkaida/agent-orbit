"""Local display overrides. Commands never send input to a monitored agent."""
from collections import deque
import json
from pathlib import Path


def member_key(source, row):
    return (source + ':' + row['session'] if source == 'remote' else
            row.get('group_id') or row.get('pane_id'))


def can_run(row):
    return (row.get('available') is not False and row.get('state') in ('idle', 'running', 'unknown') and
            not row.get('error_kind') and row.get('attention_level') not in ('error', 'warning'))


class ManualRunningTracker:
    """Hold a mark until cancelled or a genuinely new root result is observed.

    Existing commands are consumed on bridge startup. Each target captures its
    result baseline at the command, independently of heartbeat or display state.
    Missing coverage and a changed root identity establish a new baseline; they
    never masquerade as a reply. Error, human-input and offline states win.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.seen, self.order = set(), deque()
        self.active, self.groups, self.membership = {}, {}, {}
        self.known = []
        self.signature = None
        self._read(startup=True)

    def configure(self, settings):
        self.groups = {'group:' + group['id']: tuple(group['members'])
                       for group in settings.get('session_groups', [])
                       if isinstance(group, dict) and isinstance(group.get('id'), str) and
                       isinstance(group.get('members'), list)}
        self.membership = {member: target for target, members in self.groups.items() for member in members}
        self.active = {target: record for target, record in self.active.items()
                       if (target in self.groups and set(record['members']) == set(self.groups[target])) or
                       (target not in self.groups and not target.startswith('group:') and
                        target not in self.membership)}

    def _remember(self, value):
        self.seen.add(value); self.order.append(value)
        while len(self.order) > 4096:
            self.seen.discard(self.order.popleft())

    def _read(self, startup=False):
        try:
            info = self.path.stat()
            signature = info.st_ino, info.st_mtime_ns, info.st_size
            if signature == self.signature or info.st_size > 262144:
                return False
            value = json.loads(self.path.read_text())
            if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('commands'), list):
                return False
            self.signature = signature
        except (OSError, ValueError, UnicodeError, RecursionError):
            return False
        changed = False
        for command in value['commands'][-128:]:
            if not isinstance(command, dict):
                continue
            cid = command.get('id')
            if not isinstance(cid, str) or not 1 <= len(cid) <= 200 or cid in self.seen:
                continue
            self._remember(cid)
            if not startup:
                changed = self.command(command) or changed
        return changed

    def poll(self, rows):
        self.known = rows
        return self._read()

    @staticmethod
    def _baseline(rows):
        return {(row.get('source', 'remote'), member_key(row.get('source', 'remote'), row), row['result_identity']):
                row.get('result_marker') for row in rows
                if isinstance(row.get('result_identity'), str) and row.get('result_observable') is True}

    def command(self, command):
        target, action, members = command.get('target'), command.get('action'), command.get('members')
        if not isinstance(target, str) or not target or action not in ('running', 'clear'):
            return False
        if action == 'clear':
            return self.active.pop(target, None) is not None
        if (not isinstance(members, list) or not 1 <= len(members) <= 128 or
                any(not isinstance(member, str) or not member for member in members)):
            return False
        expected = self.groups.get(target, (target,))
        if set(members) != set(expected) or target in self.membership:
            return False
        rows = [row for row in self.known if member_key(row.get('source', 'remote'), row) in members]
        if not rows:
            return False
        representatives = sorted(rows, key=lambda row: (not can_run(row), row.get('state') != 'running',
                                                         row.get('source', 'remote'), row['pane_id']))
        representative = representatives[0]
        self.active[target] = dict(id=command['id'], members=tuple(expected), baseline=self._baseline(rows),
                                   representative=(representative.get('source', 'remote'), representative['pane_id']))
        return True

    def observe(self, source, rows):
        """Read result markers only from a genuine collector frame."""
        members = {member_key(source, row) for row in rows}
        self.known = [row for row in self.known if row.get('source', 'remote') != source or
                      member_key(source, row) not in members] + [dict(row, source=source) for row in rows]
        cleared = []
        for target, record in self.active.items():
            for row in rows:
                member = member_key(source, row)
                if member not in record['members'] or row.get('result_observable') is not True:
                    continue
                identity = row.get('result_identity')
                if not isinstance(identity, str):
                    continue
                key = source, member, identity
                marker = row.get('result_marker')
                if key not in record['baseline']:
                    record['baseline'][key] = marker
                elif marker is not None and marker != record['baseline'][key]:
                    cleared.append(target)
                    break
        for target in cleared:
            self.active.pop(target, None)
        return bool(cleared)

    def apply(self, source, rows):
        """Decorate raw rows before activity/completion/response accounting."""
        decorated = [dict(row, manual_running=False, manual_override_id=None) for row in rows]
        for record in self.active.values():
            targets = [row for row in decorated if member_key(source, row) in record['members']]
            for row in targets:
                row.update(manual_running=True, manual_override_id=record['id'])
            # An actual running member already represents the group's work.
            # Otherwise only the one chosen member is forced, avoiding an
            # invented parallel-running conflict in a mutually exclusive group.
            live_running = any(can_run(row) and row.get('state') == 'running' for row in self.known
                               if member_key(row.get('source', 'remote'), row) in record['members'])
            if live_running:
                continue
            for row in targets:
                if (source, row['pane_id']) == record['representative'] and can_run(row):
                    row.update(state='running', status='手动标记运行中', working=row.get('working') or '')
        return decorated

    def summary(self):
        return {target: dict(id=value['id'], members=list(value['members'])) for target, value in self.active.items()}
