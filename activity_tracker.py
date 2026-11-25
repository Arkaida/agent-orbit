"""Measure observed output and root-log activity, excluding timers and connection gaps."""
from collections import deque
from difflib import SequenceMatcher
import math
import re
import uuid

CLAUDE_GENERATED = re.compile(r'↓\s*(\d+(?:\.\d+)?)\s*([km]?)\s+tokens\b', re.I)

OUTPUT_CHROME = re.compile(r'^\s*(?:\+\s*(?:Show details\b|\d+\s+(?:more\s+)?lines?\b)|(?:⎿\s*)?Tip:\s+Use /btw\b)', re.I)
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class DialogueTracker:
    """Count only observed assistant text; resource and mixed activity are separate."""
    def __init__(self):
        self.streams = {}
        self.nonce = uuid.uuid4().hex
        self.generation = 0

    def reset(self):
        self.streams.clear()

    @staticmethod
    def key(row):
        cursor = row.get('lifecycle_cursor') or {}
        log = (cursor.get('path'), cursor.get('device'), cursor.get('inode')) if cursor.get('path') else None
        # A shared app-server PID can own multiple independent local logs.
        identity = repr(row.get('result_identity')) if row.get('result_identity') is not None else log
        owner = row.get('process_id') or row.get('pane_id')
        return (row.get('source', ''), owner, row.get('dialogue_source', ''),
                row.get('dialogue_identity', ''), identity)

    @staticmethod
    def text(row):
        value = row.get('dialogue_text', '')
        if not isinstance(value, str):
            return ''
        # Wrapping and ANSI styling do not constitute new conversation output.
        return ''.join(c for c in ANSI.sub('', value) if not c.isspace() and c >= ' ' and c != '\x7f')[-16384:]

    @staticmethod
    def observation_order(row):
        token = str(row.get('dialogue_sample_id', ''))
        return (1, int(token), '') if token.isascii() and token.isdigit() else (0, 0, token)

    @staticmethod
    def appended(previous, current):
        if current.startswith(previous):
            return len(current) - len(previous)
        if not current or previous.endswith(current):
            return 0
        # Linear suffix/prefix overlap handles terminal scroll without quadratic
        # diffs. A wholly replaced screen is an uncertain redraw, hence baseline.
        combined = current + '\0' + previous
        prefix = [0] * len(combined)
        for i in range(1, len(combined)):
            match = prefix[i - 1]
            while match and combined[i] != combined[match]:
                match = prefix[match - 1]
            if combined[i] == combined[match]:
                match += 1
            prefix[i] = match
        overlap = prefix[-1] if prefix else 0
        return len(current) - overlap if overlap else 0

    def update(self, rows, now, max_gap_seconds=2):
        max_gap = (max_gap_seconds if isinstance(max_gap_seconds, (int, float))
                   and not isinstance(max_gap_seconds, bool) and math.isfinite(max_gap_seconds)
                   and max_gap_seconds > 0 else 2)
        grouped = {}
        for row in rows:
            grouped.setdefault(self.key(row), []).append(row)
        current, snapshots = {}, {}
        for key, aliases in grouped.items():
            # One producer observation per physical stream, even with tmux aliases.
            row = max(aliases, key=lambda item: (bool(item.get('dialogue_available')),
                       item.get('state') == 'running' and not item.get('error_kind'),
                       self.observation_order(item), len(self.text(item))))
            available = row.get('dialogue_available') is True
            active = row.get('state') == 'running' and not row.get('error_kind')
            token = row.get('dialogue_sample_id', now)
            text = self.text(row) if available else ''
            saved = self.streams.get(key)
            fresh = saved is None or token != saved['token']
            if saved is None or (fresh and (now - saved['observed'] > max_gap or
                                           available != saved['available'] or
                                           (active and not saved['active']))):
                self.generation += 1
                saved = dict(epoch='{}:{}'.format(self.nonce, self.generation), total=0,
                             sample_id=0, text=text, active=active, available=available,
                             complete=False, token=None, observed=now)
                fresh = True
            if fresh:
                if available and active and saved['available'] and saved['active'] and saved['sample_id']:
                    saved['total'] += self.appended(saved['text'], text)
                saved.update(text=text, active=active, available=available,
                             complete=available and row.get('dialogue_complete') is True,
                             token=token, observed=now, sample_id=saved['sample_id'] + 1)
            # Replayed frames cannot renew freshness or change the count.
            snapshots[key] = dict(epoch=saved['epoch'], sample_id=saved['sample_id'],
                                  total_chars=saved['total'], available=saved['available'],
                                  complete=saved['complete'], source=row.get('dialogue_source', ''),
                                  sample_age_seconds=round(max(0, now - saved['observed']), 3))
            current[key] = saved
        self.streams = current
        return snapshots


class ActivityTracker:
    def __init__(self, window_seconds=10):
        self.window = window_seconds
        self.panes = {}
        self.epoch = None
        self.next_activity_epoch = 0
        self.dialogue = DialogueTracker()

    def reset(self):
        self.panes.clear()
        self.epoch = None
        self.dialogue.reset()

    def update(self, rows, now, epoch=0, max_gap_seconds=2):
        if epoch != self.epoch:
            self.reset()
            self.epoch = epoch
        rows = list(rows)
        dialogue = self.dialogue.update(rows, now, max_gap_seconds=max_gap_seconds)
        current, decorated = {}, []
        for row in rows:
            key = row['pane_id']
            text = row.get('activity') or '\n'.join(row.get('preview', []))
            text = '\n'.join(line for line in text.splitlines() if not OUTPUT_CHROME.match(line))[-1200:]
            active = row.get('state') == 'running' and not row.get('error_kind')
            pid = row.get('process_id')
            background_bytes = row.get('background_output_observed_bytes', row.get('background_output_bytes', 0))
            background_revision = row.get('background_revision', 0)
            background_progress = row.get('background_progress', '')
            cursor = row.get('lifecycle_cursor') or {}
            log_key = (cursor.get('path'), cursor.get('device'), cursor.get('inode')) if cursor.get('path') else None
            log_offset = cursor.get('offset', 0)
            token_match = CLAUDE_GENERATED.search(row.get('working') or '') if row.get('agent') == 'claude' else None
            generated = round(float(token_match.group(1)) * {'': 1, 'k': 1000, 'm': 1000000}[token_match.group(2).lower()]) if token_match else None
            saved = self.panes.get(key)
            if saved is None or saved['pid'] != pid:
                self.next_activity_epoch += 1
                saved = dict(pid=pid, text=text, active=active, samples=deque(), last=None,
                             total=0, activity_epoch=self.next_activity_epoch, source='', log_key=log_key, log_offset=log_offset, generated=generated,
                             background_bytes=background_bytes, background_revision=background_revision, background_progress=background_progress)
            if active and saved['active']:
                # Count inserted/replaced non-whitespace characters, not the
                # visible spinner or elapsed-time row (excluded by the probe).
                count = sum(sum(not ch.isspace() for ch in text[j:k])
                            for kind, _, _, j, k in SequenceMatcher(None, saved['text'], text, autojunk=False).get_opcodes()
                            if kind in ('insert', 'replace')) if text != saved['text'] else 0
                visible_count = count
                # A folded terminal can stay unchanged while its root log grows.
                # Compare only the same file; initial history, replacement and
                # connection backlogs establish a baseline instead of activity.
                log_count = min(5000, max(0, log_offset - saved['log_offset'])) if log_key is not None and log_key == saved['log_key'] else 0
                background_count = min(5000, max(0, background_bytes - saved['background_bytes']))
                background_count += min(100, max(0, background_revision - saved['background_revision']))
                progress_count = 10 if background_progress and background_progress != saved['background_progress'] else 0
                # Claude may stream thinking while terminal prose and JSONL stay
                # unchanged. Use only the reported generated-token delta, never
                # elapsed seconds, a spinner frame or the initial counter.
                token_count = min(5000, max(0, generated - saved['generated'])) if generated is not None and saved['generated'] is not None else 0
                count = max(visible_count, log_count, token_count) + background_count + progress_count
                if count:
                    saved['samples'].append((now, count))
                    saved['last'] = now
                    saved['total'] += count
                    saved['source'] = ('workflow' if progress_count else 'background' if background_count else
                                       'tokens' if token_count > max(log_count, visible_count) else 'log' if log_count > visible_count else 'screen')
            if not active:
                saved['samples'].clear()
                saved['last'] = None
            while saved['samples'] and saved['samples'][0][0] <= now - self.window:
                saved['samples'].popleft()
            rate = sum(count for _, count in saved['samples']) / self.window if active else 0
            saved.update(text=text, active=active, log_key=log_key, log_offset=log_offset, background_bytes=background_bytes, background_revision=background_revision,
                         background_progress=background_progress)
            if generated is not None:
                saved['generated'] = generated
            elif not active:
                saved['generated'] = None
            current[key] = saved
            decorated.append(dict(row, output_total=saved['total'], activity_epoch=saved['activity_epoch'],
                                  dialogue_output=dialogue[self.dialogue.key(row)],
                                  activity_source=saved['source'] if active else '', output_rate=round(rate, 2),
                                  activity_level=round(min(1, math.log1p(rate) / math.log1p(60)), 3),
                                  activity_age=round(now - saved['last'], 2) if saved['last'] is not None else None))
        self.panes = current
        return decorated
