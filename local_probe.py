#!/usr/bin/env python3
"""Read-only discovery of open local Codex root sessions on macOS/Linux."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import time

from remote_probe import EMPTY_ISSUE, ResultLogWatcher, UsageLogWatcher, detect_failure, issue
from resource_probe import AsyncProcessResources
from process_snapshot import ProcessSnapshot

LIFECYCLE = frozenset(('task_started', 'turn_started', 'task_complete', 'turn_complete',
                       'turn_aborted', 'turn_failed', 'error', 'stream_error'))


def lifecycle_before(path, end):
    """Find the latest boundary before a bounded tail, using bounded memory."""
    carry = b''
    with path.open('rb') as log:
        while end > 0:
            start = max(0, end - 2097152)
            log.seek(start)
            lines = (log.read(end - start) + carry).split(b'\n')
            carry = lines.pop(0)[-4194304:] if start else b''
            for line in reversed(lines):
                if b'"event_msg"' not in line:
                    continue
                try:
                    item = json.loads(line)
                    if item.get('type') == 'event_msg' and (item.get('payload') or {}).get('type') in LIFECYCLE:
                        return item['payload']
                except (ValueError, TypeError, AttributeError):
                    continue
            end = start
    return None


def apply_lifecycle(saved, payload):
    kind = payload['type']
    saved['last'] = kind
    saved['failure'] = dict(EMPTY_ISSUE)
    if kind in ('turn_aborted', 'turn_failed', 'error', 'stream_error'):
        message = payload.get('message') or str(payload.get('reason') or '')
        saved['failure'] = issue('本轮已中断', 'interrupted') if kind == 'turn_aborted' else detect_failure(['■ ' + message], '', False)
        if kind == 'stream_error':
            saved['failure'] = issue('正在重试 · ' + saved['failure']['error_summary'], 'retrying', 'warning')


class LocalCollector:
    def __init__(self, home, limit=16, runner=subprocess.run):
        self.home = Path(home).expanduser().resolve()
        self.limit, self.runner = limit, runner
        self.files, self.discovery = {}, []
        self.usage = UsageLogWatcher()
        self.results = ResultLogWatcher()
        self.resources = AsyncProcessResources()
        self.processes = ProcessSnapshot()
        self.process_owners = {}
        self.dialogue_generation = 0
        self.next_discovery = 0

    def discover(self):
        result = self.runner(['ps', '-axo', 'pid=,ppid=,stat=,comm='], capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise RuntimeError('无法读取本机进程，请检查系统权限')
        pids = []
        table = {}
        self.process_owners = {}
        for line in result.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4:
                try:
                    table[int(parts[0])] = (int(parts[1]), parts[2], parts[3])
                except ValueError:
                    continue
            if len(parts) == 4 and os.path.basename(parts[3]) in ('codex', 'codex-app-server') and 'Z' not in parts[2]:
                pids.append(parts[0])
        self.processes = ProcessSnapshot(table)
        if not pids:
            return []
        result = self.runner(['lsof', '-n', '-P', '-p', ','.join(pids), '-Fpn'], capture_output=True, text=True, timeout=4)
        if result.returncode not in (0, 1):
            raise RuntimeError('无法读取本机 Codex 会话文件')
        owner, found, children = None, {}, {}
        for line in result.stdout.splitlines():
            if line.startswith('p'):
                owner = int(line[1:])
            if not line.startswith('n') or not line.endswith('.jsonl') or not Path(line[1:]).name.startswith('rollout-'):
                continue
            try:
                path = Path(line[1:]).resolve()
                path.relative_to(self.home)
                with path.open('rb') as log:
                    meta = json.loads(log.readline(1048576))
                payload = meta.get('payload') or {}
                sid = payload.get('id') or payload.get('session_id')
                if meta.get('type') != 'session_meta':
                    continue
                if isinstance(payload.get('source'), dict):
                    parent = payload['source'].get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id')
                    if sid and parent:
                        children[sid] = (parent, path)
                    continue  # Child/guardian sessions never become root cards.
                if isinstance(sid, str) and isinstance(payload.get('source'), str) and payload['source'].strip() and owner:
                    existing = found.get(sid)
                    pids = (existing.get('pids', set()) if existing else set()) | {owner}
                    found[sid] = dict(path=path, pid=owner if len(pids) == 1 else None, pids=pids, metadata=payload)
            except (OSError, ValueError, TypeError):
                continue
        for sid, root in found.items():
            for pid in root['pids']:
                self.process_owners.setdefault(pid, set()).add(sid)
            owned, root['children'] = {sid}, []
            for _ in children:
                added = [(cid, path) for cid, (parent, path) in children.items() if parent in owned and cid not in owned]
                if not added: break
                for cid, path in added:
                    owned.add(cid); root['children'].append(path)
        return sorted(found.items(), key=lambda x: x[1]['path'].stat().st_mtime if x[1]['path'].exists() else 0, reverse=True)[:self.limit]

    def titles(self, ids):
        titles = {}
        for path in sorted(self.home.glob('state*.sqlite'), reverse=True)[:1]:
            try:
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2) as database:
                    slots = ','.join('?' for _ in ids)
                    if slots:
                        titles.update(database.execute('SELECT id, title FROM threads WHERE id IN (' + slots + ')', list(ids)).fetchall())
            except sqlite3.Error:
                pass
        return titles

    def read_log(self, path):
        info = path.stat()
        identity = (info.st_dev, info.st_ino)
        saved = self.files.get(str(path))
        if saved is None or saved['identity'] != identity or info.st_size < saved['offset']:
            self.dialogue_generation += 1
            saved = dict(identity=identity, offset=0, buffer=b'', last='', preview=[], failure=dict(EMPTY_ISSUE),
                         dialogue_text='', dialogue_trimmed=False,
                         dialogue_last_id=None, dialogue_last_text='', dialogue_seen_ids=[],
                         dialogue_identity='assistant-log:{}:{}:{}'.format(identity[0], identity[1], self.dialogue_generation))
            self.files[str(path)] = saved
            # Establish current state from a bounded tail, without replaying alerts.
            saved['offset'] = max(0, info.st_size - 2097152)
            saved['dialogue_trimmed'] = saved['offset'] > 0
        with path.open('rb') as log:
            log.seek(saved['offset'])
            if saved['offset'] and not saved.get('initialized'):
                log.readline()
                prior = lifecycle_before(path, log.tell())
                if prior:
                    apply_lifecycle(saved, prior)
            content = saved['buffer'] + log.read(4194304)
            saved['offset'] = log.tell()
        saved['initialized'] = True
        lines = content.split(b'\n'); saved['buffer'] = lines.pop()[-4194304:]
        for line in lines:
            try:
                item = json.loads(line)
                payload = item.get('payload') or {}
                kind = payload.get('type')
                if item.get('type') == 'event_msg':
                    if kind in LIFECYCLE:
                        apply_lifecycle(saved, payload)
                    if kind in ('task_started', 'turn_started'):
                        saved.update(dialogue_text='', dialogue_trimmed=False,
                                     dialogue_last_id=None, dialogue_last_text='', dialogue_seen_ids=[])
                if item.get('type') == 'response_item' and kind == 'message' and payload.get('role') == 'assistant':
                    text = '\n'.join(x.get('text', '') for x in payload.get('content', []) if isinstance(x, dict) and x.get('type') in ('output_text', 'text'))
                    if text.strip():
                        saved['preview'] = text.strip().splitlines()[-3:]
                        message_id = payload.get('id') or item.get('id')
                        previous = saved['dialogue_last_text']
                        same_message = (message_id is not None and message_id == saved['dialogue_last_id'])
                        # Some log producers repeat a complete message or emit
                        # progressively longer complete bodies without an ID.
                        same_message = same_message or (message_id is None and previous and text.startswith(previous))
                        duplicate = message_id is not None and message_id in saved['dialogue_seen_ids'] and not same_message
                        if not duplicate:
                            body = saved['dialogue_text']
                            if same_message:
                                body = body[:-len(previous)] if len(previous) <= len(body) else ''
                            elif body:
                                body += '\n'
                            body += text
                            saved.update(dialogue_text=body[-16384:],
                                         dialogue_trimmed=saved['dialogue_trimmed'] or len(body) > 16384,
                                         dialogue_last_id=message_id, dialogue_last_text=text[-16384:])
                            if message_id is not None and message_id not in saved['dialogue_seen_ids']:
                                saved['dialogue_seen_ids'] = (saved['dialogue_seen_ids'] + [message_id])[-64:]
            except (ValueError, TypeError, AttributeError):
                continue
        saved['dialogue_caught_up'] = saved['offset'] >= info.st_size and not saved['buffer']
        return saved

    def snapshot(self, selected=()):
        started = time.monotonic()
        if started >= self.next_discovery:
            self.discovery = self.discover()
            self.next_discovery = started + 2
        titles = self.titles([sid for sid, _ in self.discovery])
        rows = []
        for sid, session in self.discovery:
            if selected and sid not in selected:
                continue
            try:
                state = self.read_log(session['path'])
            except OSError:
                continue
            last = state['last']
            status = 'running' if last in ('task_started', 'turn_started', 'stream_error') else 'idle' if last in ('task_complete', 'turn_complete') else 'interrupted' if last == 'turn_aborted' else 'error' if last in ('turn_failed', 'error') else 'unknown'
            child_states = []
            for path in session.get('children', []):
                try: child_states.append(self.read_log(path)['last'])
                except OSError: child_states.append('')
            running_children = sum(kind in ('task_started', 'turn_started', 'stream_error') for kind in child_states)
            if running_children and status == 'idle':
                status = 'running'
            title = (titles.get(sid) or Path(session['metadata'].get('cwd', '')).name or sid[:8]).strip()[:100]
            label = {'running': '运行中', 'idle': '等待输入', 'interrupted': '本轮已中断', 'error': '运行报错', 'unknown': '本机状态待核实'}[status]
            rows.append(dict(pane_id='local:' + sid, session=title, group_id='local:' + sid,
                             thread_id=sid, source='local', source_label='本机', agent='codex',
                             process_id=session['pid'], state=status, status=label,
                             background_agents=running_children, background_agents_total=len(child_states),
                             background_agents_observable=all(child_states) if child_states else True,
                             cwd=session['metadata'].get('cwd', ''), window='本机', pane=sid[:8],
                             preview=state['preview'], activity='\n'.join(state['preview']), working='', screen='',
                             dialogue_text=state['dialogue_text'], dialogue_source='assistant_log',
                             dialogue_identity=state['dialogue_identity'], dialogue_available=True,
                             dialogue_complete=state['dialogue_caught_up'] and not state['dialogue_trimmed'],
                             dialogue_sample_id=str(time.monotonic_ns()),
                             fingerprint=hashlib.sha256((last + '\n'.join(state['preview'])).encode()).hexdigest(),
                             lifecycle_cursor=dict(path=str(session['path']), device=state['identity'][0], inode=state['identity'][1], offset=state['offset']),
                             telemetry=self.usage.sample(session['path'], 'codex'),
                             **self.results.sample(session['path'], 'codex'),
                             **state['failure']))
        samples = self.resources.sample_all([row['process_id'] for row in rows], self.processes)
        for row in rows:
            value = samples.get(row['process_id'])
            if value is not None:
                value = dict(value)
                shared = len(self.process_owners.get(row['process_id'], ()))
                process_name = os.path.basename(self.processes.get(row['process_id'], (0, '', ''))[2])
                shared_host = shared > 1 or process_name == 'codex-app-server'
                value.update(attribution='shared' if shared_host else 'exclusive' if shared else 'unattributed',
                             attribution_source='open_rollout_fd')
                if shared_host:
                    value.update(scope='shared_process', shared_sessions=shared)
            row['resources'] = value
        return dict(panes=rows, sampled_at=time.time(), collection_ms=round((time.monotonic() - started) * 1000))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', default=os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    parser.add_argument('--limit', type=int, default=16)
    parser.add_argument('--interval', type=float, default=.5)
    parser.add_argument('--selected', default='[]')
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    collector, selected = LocalCollector(args.home, args.limit), json.loads(args.selected)
    pending = b''
    while True:
        started = time.monotonic()
        try:
            frame = collector.snapshot(selected)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            frame = dict(error=str(error), sampled_at=time.time())
        print(json.dumps(frame, ensure_ascii=False), flush=True)
        if not args.stream:
            return
        deadline = started + max(.25, args.interval)
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))
            if ready:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    return
                pending += data
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    selected = json.loads(line).get('selected', [])
            if time.monotonic() >= deadline:
                break


if __name__ == '__main__':
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
