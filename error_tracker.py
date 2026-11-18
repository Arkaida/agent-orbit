"""One alert per observed issue, including brief lifecycle interruptions."""
from uuid import uuid4


class ErrorTracker:
    def __init__(self):
        self.previous = {}
        self.pending = {}
        self.epoch = None
        self.seen_events = set()
        self.recent_screen_alerts = {}
        self.outage_since = None
        self.outage_alerted = False
        self.ever_connected = False

    @staticmethod
    def event(row, now_wall, summary=None, kind=None, level=None):
        return dict(id=uuid4().hex, pane_id=row['pane_id'], session=row['session'],
                    summary=summary or row.get('error_summary', '需要处理'), occurred_at=now_wall,
                    kind=kind or row.get('error_kind', 'fatal'),
                    signature=row.get('error_signature', ''),
                    severity=level or row.get('attention_level') or 'error')

    def update(self, rows, now_wall, epoch=0, now_mono=None):
        now = now_wall if now_mono is None else now_mono
        if epoch != self.epoch:
            self.previous = {}
            self.pending = {}
            self.epoch = epoch
        current, events = {}, []
        for row in rows:
            pane_id = row['pane_id']
            state = row.get('state')
            signature = row.get('error_signature', '')
            fresh_log = False
            for event in row.get('lifecycle_events', []):
                if event['id'] not in self.seen_events:
                    self.seen_events.add(event['id'])
                    recent = self.recent_screen_alerts.get(pane_id)
                    if not (recent and recent[0] == event['kind'] and now - recent[1] < 2):
                        events.append(self.event(row, now_wall, event['summary'], event['kind'], 'error'))
                    fresh_log = True
            previous = self.previous.get(pane_id)
            if state in ('history', 'other'):
                self.pending.pop(pane_id, None)
                continue
            if state == 'unknown':
                signature = 'unrecognized-screen'
                row = dict(row, error_summary='连续无法确认状态，请进入会话检查', error_kind='unknown', attention_level='warning')
            delay = 5 if state == 'unknown' else 1 if row.get('error_kind') in ('stopped', 'missing', 'paused', 'approval', 'question', 'goal') else 0
            changed = previous is not None and signature != previous[0]
            if signature and (changed or (state == 'unknown' and previous is None)):
                pending = self.pending.get(pane_id)
                if pending is None or pending[0] != signature:
                    self.pending[pane_id] = (signature, now)
                if now - self.pending[pane_id][1] >= delay:
                    if not fresh_log and row.get('issue_event_id') not in self.seen_events:
                        events.append(self.event(row, now_wall))
                        self.recent_screen_alerts[pane_id] = (row.get('error_kind'), now)
                    self.pending.pop(pane_id, None)
                else:
                    current[pane_id] = previous or ('', None)
                    continue
            else:
                self.pending.pop(pane_id, None)
            pid = row.get('process_id')
            if previous and previous[1] and pid and previous[1] != pid:
                events.append(self.event(row, now_wall, '会话进程已更换，可能发生退出后重启', 'restarted', 'warning'))
            current[pane_id] = (signature, pid)
        self.previous = current
        self.pending = {key: value for key, value in self.pending.items() if key in current}
        if len(self.seen_events) > 4096:
            self.seen_events = {e['id'] for row in rows for e in row.get('lifecycle_events', [])}
        return events

    def connection(self, connected, now_mono, now_wall, reason):
        if connected:
            self.ever_connected = True
            self.outage_since = None
            self.outage_alerted = False
            return []
        if self.outage_since is None:
            self.outage_since = now_mono
        delay = 3 if self.ever_connected else 10
        if self.outage_alerted or now_mono - self.outage_since < delay:
            return []
        self.outage_alerted = True
        return [self.event(dict(pane_id='__connection__', session='监控连接'), now_wall,
                           reason + '；远端工作状态暂不可确认', 'connection', 'error')]
