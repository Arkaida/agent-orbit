#!/usr/bin/env python3
"""JSONL feed for the native panel, reusing the terminal monitor's SSH stream."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

from completion_tracker import CompletionTracker
from activity_tracker import ActivityTracker
from usage_tracker import UsageTracker
from error_tracker import ErrorTracker
from monitor import Model, Stream, LocalStream
from configuration import data_dir, defaults, load_settings, read_follow as configured_follow


def read_follow(root):
    return configured_follow(root)


def connection_status(stream, model, config_error, now):
    """Freshness and transport failures are distinct UI states."""
    age = now - stream.last_packet if stream.last_packet is not None else None
    if config_error:
        status, message = 'config_error', config_error
    elif stream.error:
        status, message = 'reconnecting', connection_failure_message(stream)
    elif model.remote_error:
        status, message = 'remote_error', '远端采样异常，等待恢复'
    elif stream.process is None:
        status, message = 'reconnecting', '连接断开，正在重连'
    elif age is None:
        status, message = 'connecting', '正在连接 ' + stream.host
    elif age >= max(2, getattr(stream, 'interval', .5) * 3):
        status, message = 'delayed', '等待远端更新 · {} 秒未收到画面'.format(int(age))
    else:
        status, message = 'live', '已连接 · 实时监控'
    return dict(connection_state=status, connected=status == 'live', message=message, received_age=age)


def connection_failure_message(stream):
    """Show an actionable transport reason without exposing arbitrary SSH output."""
    if stream.host == '本机':
        return '本机监控已退出，正在重试；请检查数据目录'
    error = stream.error.lower()
    if 'could not resolve hostname' in error or 'name or service not known' in error:
        return 'SSH 主机不存在，请在设置中检查主机名'
    if 'no route to host' in error or 'network is unreachable' in error:
        return '无法访问 SSH 主机，请检查主机设置和网络'
    if 'connection refused' in error:
        return 'SSH 连接被拒绝，请检查主机和端口'
    if 'permission denied' in error:
        return 'SSH 认证失败，请检查密钥与主机设置'
    if 'host key verification failed' in error:
        return 'SSH 主机验证失败，请先在终端确认主机身份'
    if 'timed out' in error or 'timeout' in error:
        return 'SSH 连接超时，请检查主机设置和网络'
    return 'SSH 连接断开，正在重连；请检查连接设置'


def aggregate_connection_status(connections, config_error):
    """Keep initialization, failed connections and invalid settings distinct."""
    if config_error:
        return dict(connected=False, connection_state='config_error',
                    message=config_error + '；请打开设置修正')
    live = sum(connection['connected'] for connection in connections)
    if live and live == len(connections):
        status = 'live'
    elif live:
        status = 'partial'
    else:
        states = {connection['connection_state'] for connection in connections}
        status = next((state for state in ('config_error', 'monitor_error', 'remote_error',
                                          'reconnecting', 'delayed') if state in states), 'connecting')
    message = ' · '.join(connection['label'] + '：' + connection['message']
                         for connection in connections) or '正在准备监控来源…'
    return dict(connected=live > 0, connection_state=status, message=message)


def diagnostic_event(root, event, **fields):
    # Only connection metadata is recorded, never terminal text or chat content.
    try:
        folder = root / '.logs'
        folder.mkdir(exist_ok=True)
        path = folder / 'connection.log'
        if path.exists() and path.stat().st_size > 262144:
            path.replace(folder / 'connection.log.1')
        record = dict(time=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), event=event, **fields)
        with path.open('a', encoding='utf-8') as log:
            log.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass  # A log write failure must not interrupt the monitor.


class FeedState:
    """Separate observed snapshots from cached display and connection gaps."""
    def __init__(self):
        self.model = Model()
        self.completions = CompletionTracker()
        self.attention = ErrorTracker()
        self.activity = ActivityTracker()
        self.rows, self.events, self.error_events = [], [], []
        self.notification_events = []
        self.generation = 0
        self.continuous = False
        self.activity.reset()
        self.last_sample = None

    def break_continuity(self):
        if self.continuous:
            self.generation += 1
        self.continuous = False

    def accept(self, frame, selected, connection_id, now_mono, now_wall, max_gap_seconds=2):
        self.model.accept(frame, now_mono)
        if 'panes' not in frame:
            self.break_continuity()
            return
        if self.last_sample is not None and now_mono - self.last_sample > max_gap_seconds:
            self.break_continuity()
        epoch = (connection_id, self.generation, tuple(selected))
        observed = self.activity.update(self.model.visible(selected), now_mono, epoch)
        self.rows, completed = self.completions.update(observed, now_mono, now_wall, epoch=epoch)
        self.events = (self.events + completed)[-100:]
        alerts = self.attention.update(observed, now_wall, epoch=epoch, now_mono=now_mono)
        self.notification_events = (self.notification_events + alerts)[-100:]
        self.error_events = (self.error_events + alerts)[-100:]
        # Keep a bounded delivery queue for one-time sounds, but show only
        # incidents that still match the current authoritative pane state.
        active = {row['pane_id']: row for row in observed}
        self.error_events = [event for event in self.error_events if self.is_current(event, active)]
        self.last_sample = now_mono
        self.continuous = True

    @staticmethod
    def is_current(event, active):
        if event['pane_id'] == '__connection__':
            return True  # Cleared only after the connection is confirmed.
        row = active.get(event['pane_id'])
        if not row:
            return False
        if event.get('kind') == 'unknown':
            return row.get('state') == 'unknown'
        return (row.get('error_kind') == event.get('kind') and
                bool(row.get('error_kind')) and
                (not event.get('signature') or event['signature'] == row.get('error_signature')))

    def connection(self, connected, now_mono, now_wall, reason, waiting=False):
        if not connected:
            self.break_continuity()
        # A late snapshot makes old task states unconfirmed, but is not yet
        # a failed SSH connection. Stream handles the actual transport timeout.
        transport_responded = connected or waiting
        alerts = self.attention.connection(transport_responded, now_mono, now_wall, reason)
        self.notification_events = (self.notification_events + alerts)[-100:]
        self.error_events = (self.error_events + alerts)[-100:]
        if transport_responded:
            self.error_events = [event for event in self.error_events if event['pane_id'] != '__connection__']


def source_payload(name, state, health, selected):
    def pane_id(value):
        return name + ':' + value if name == 'remote' else value
    rows = []
    for row in state.rows:
        compact = {key: value for key, value in row.items()
                   if key not in ('screen', 'fingerprint', 'activity', 'lifecycle_events')}
        compact.update(pane_id=pane_id(row['pane_id']), source=name,
                       source_label='远端' if name == 'remote' else '本机', available=health['connected'],
                       group_id=name + ':' + row['session'] if name == 'remote' else row.get('group_id', row['pane_id']))
        rows.append(compact)
    def events(values):
        return [dict(event, pane_id=pane_id(event['pane_id']), source=name) for event in values]
    return dict(rows=rows, events=events(state.events), error_events=events(state.notification_events),
                active_error_events=events(state.error_events))


def run(args):
    root = Path(__file__).resolve().parent
    runtimes = {}
    usage = UsageTracker()
    next_config, config_error, stopping = 0, '', False
    # Invalid settings must still produce a first frame so the UI can explain
    # the error and recover when the user saves a corrected file. No source is
    # started until load_settings and the follow list have both validated.
    settings = defaults(root)
    selected = []

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            now = time.monotonic()
            if now >= next_config:
                try:
                    settings = load_settings(root)
                    if args.host is not None:
                        settings['host'] = args.host
                        settings['remote_enabled'] = True
                    if args.socket is not None:
                        settings['tmux_socket'] = args.socket
                    selected = read_follow(root)
                    desired = {}
                    if settings['remote_enabled']:
                        desired['remote'] = (settings['host'], settings['tmux_socket'], settings['interval'])
                    if settings['local_enabled']:
                        desired['local'] = (settings['local_codex_home'], settings['local_limit'], settings['interval'])
                    for name in list(runtimes):
                        if name not in desired or runtimes[name]['signature'] != desired[name]:
                            runtimes.pop(name)['stream'].close()
                    for name, signature in desired.items():
                        if name not in runtimes:
                            stream = (Stream(settings['host'], settings['tmux_socket'], (root / 'remote_probe.py').read_text(), settings['interval'], selected)
                                      if name == 'remote' else LocalStream(root, settings['local_codex_home'], settings['interval'], [], settings['local_limit']))
                            runtimes[name] = dict(stream=stream, state=FeedState(), signature=signature, last_health=None)
                        if name == 'remote':
                            runtimes[name]['stream'].subscribe(selected)
                    config_error = ''
                except (OSError, ValueError) as error:
                    config_error = str(error)
                next_config = now + .5
            chunks, connections, discovered = [], [], []
            for name, runtime in runtimes.items():
                stream, state = runtime['stream'], runtime['state']
                focus = selected if name == 'remote' else settings['local_followed']
                if name == 'local' and runtime.get('focus') != tuple(focus):
                    state = runtime['state'] = FeedState()
                    runtime['focus'] = tuple(focus)
                for frame in stream.poll():
                    if name == 'local' and 'panes' in frame:
                        runtime['discovered'] = [dict(thread_id=r.get('thread_id'), session=r['session']) for r in frame['panes']]
                        frame = dict(frame, panes=[r for r in frame['panes'] if not focus or r.get('thread_id') in focus])
                    sampled_at = time.monotonic()
                    sample_gap = max(2, stream.interval * 3)
                    state.accept(frame, focus if name == 'remote' else [], stream.connections,
                                 sampled_at, time.time(), max_gap_seconds=sample_gap)
                    if 'panes' in frame:
                        # Usage receives genuine collector samples only. The
                        # 10 Hz display payload below must never establish a
                        # second baseline or accumulate another usage delta.
                        observed = source_payload(name, state, dict(connected=True), focus)['rows']
                        usage.observe(name, observed, sampled_at,
                                      epoch=(runtime['signature'], stream.connections,
                                             state.generation, tuple(focus)),
                                      max_gap_seconds=sample_gap)
                    if name == 'remote':
                        for row in frame.get('panes', []):
                            if row.get('lifecycle_cursor'):
                                stream.cursors[row['pane_id']] = row['lifecycle_cursor']
                        stream.cursors = dict(list(stream.cursors.items())[-200:])
                health = connection_status(stream, state.model, config_error, time.monotonic())
                if name == 'local':
                    health['message'] = '本机 · ' + ('实时监控' if health['connected'] else health['message'])
                    discovered = runtime.get('discovered', [])
                state.connection(health['connected'], time.monotonic(), time.time(), health['message'], health['connection_state'] == 'delayed')
                key = (health['connection_state'], stream.error, state.model.remote_error, config_error)
                if key != runtime['last_health']:
                    diagnostic_event(data_dir(), 'connection_state', source=name, state=health['connection_state'], reason=stream.error or state.model.remote_error or config_error or health['message'])
                    runtime['last_health'] = key
                chunks.append(source_payload(name, state, health, focus))
                connections.append(dict(id=name, label='远端' if name == 'remote' else '本机', **health))
            health = aggregate_connection_status(connections, config_error)
            payload = {key: [item for chunk in chunks for item in chunk[key]] for key in ('rows', 'events', 'error_events', 'active_error_events')}
            payload.update(followed=selected, host=settings['host'] if settings['remote_enabled'] else '本机',
                           **health,
                           received_frames=sum(r['stream'].total_frames for r in runtimes.values()), emitted_at=time.time(),
                           connections=connections, local_sessions=discovered)
            payload['metrics'] = usage.aggregate(payload['rows'], time.monotonic(), connections)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            time.sleep(.1)
    finally:
        for runtime in runtimes.values():
            runtime['stream'].close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host')
    parser.add_argument('--socket')
    args = parser.parse_args()
    try:
        run(args)
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    except Exception as error:
        diagnostic_event(data_dir(), 'monitor_error', error=type(error).__name__, reason=str(error))
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
