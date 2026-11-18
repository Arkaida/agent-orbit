import json
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from monitor import Model, Stream, clip
from remote_probe import analyse_screen


def screen(body='• Testing\n  output 1', running='', draft='Ask Codex to do anything'):
    return '{}\n{}\n\n» {}\n\n  GPT-6.1-Sol ultra · /project · Main [default]\n  ? for shortcuts\n'.format(body, running, draft)


class ScreenTests(unittest.TestCase):
    def test_body_changes_with_stable_heading(self):
        a = analyse_screen(screen('• Same heading\n  first output'))
        b = analyse_screen(screen('• Same heading\n  new output'))
        self.assertNotEqual(a['preview'], b['preview'])
        self.assertEqual(b['preview'][-1], 'new output')

    def test_running_overrides_visible_input_box(self):
        result = analyse_screen(screen(running='• Working (42s • esc to interrupt)'))
        self.assertEqual(result['state'], 'running')
        self.assertIn('42s', result['working'])
        self.assertNotIn('Working', '\n'.join(result['preview']))

    def test_idle_and_draft(self):
        self.assertEqual(analyse_screen(screen())['state'], 'idle')
        self.assertEqual(analyse_screen(screen(draft='继续'))['status'], '等待输入（有草稿）')

    def test_prose_approval_is_not_a_dialog(self):
        self.assertEqual(analyse_screen(screen('• The approval implementation is complete.'))['state'], 'idle')

    def test_question_and_history(self):
        result = analyse_screen(screen('• New work\n? 1 question\nshift+← to answer', '• Working (1s • esc to interrupt)'))
        self.assertIn('有问题待回答', result['status'])
        self.assertEqual(result['preview'], ['• New work'])
        self.assertEqual(analyse_screen(screen('old text\nBack to bottom · esc'))['state'], 'history')

    def test_dead_and_unknown(self):
        self.assertEqual(analyse_screen(screen(), dead=True)['state'], 'stopped')
        self.assertEqual(analyse_screen('unrecognized codex screen')['state'], 'unknown')

    def test_response_wait_menu_is_running_instead_of_unknown_or_done(self):
        result = analyse_screen('3. Learn more\nNo action is required. Codex will keep waiting, and this menu will close when the response is ready.')
        self.assertEqual(result['state'], 'running')
        self.assertEqual(result['error_kind'], '')


class ModelTests(unittest.TestCase):
    def test_deselected_local_chats_do_not_become_missing_alerts(self):
        model = Model()
        model.accept({'panes': [dict(session='local chat', pane_id='local:1', thread_id='1', source='local', state='idle')]})
        self.assertEqual(model.visible(['__none__']), [])
        self.assertEqual(len(model.visible(['1'])), 1)

    def test_fresh_data_is_separate_from_changed_content(self):
        m = Model()
        row = dict(session='x', pane_id='%1', fingerprint='a', screen='same')
        m.accept({'panes': [row]}, now=1)
        m.accept({'panes': [row]}, now=4)
        self.assertEqual(m.last_received, 4)
        self.assertEqual(m.changes['%1'][1], 1)
        m.accept({'panes': [{**row, 'fingerprint': 'b'}]}, now=5)
        self.assertEqual(m.changes['%1'][1], 5)

    def test_missing_followed_pane_and_remote_errors(self):
        m = Model()
        m.accept({'panes': [dict(session='x', pane_id='%1', screen='text')]}, now=1)
        m.accept({'error': 'offline'}, now=2)
        self.assertEqual(m.last_received, 1)
        self.assertEqual(m.visible(['missing'])[0]['state'], 'missing')
        self.assertEqual(m.rows[0]['session'], 'x')

    def test_terminal_cell_clipping_and_controls(self):
        self.assertEqual(clip('中文test', 5), '中文t')
        self.assertNotIn('\x1b', clip('\x1btest', 80))


class StreamTests(unittest.TestCase):
    def collect(self, stream, condition, timeout=5):
        frames = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frames.extend(stream.poll())
            if condition(frames):
                return frames
            time.sleep(.02)
        self.fail('stream timed out: ' + stream.error)

    def test_split_frames_flush_subscription_and_reconnect(self):
        # A real local process stands in for SSH; tests the actual pipes/selectors.
        producer = '''import json,os,sys,time
os.write(1,b'{"panes":')
time.sleep(.05)
os.write(1,b'[],"sequence":1}\\n{"panes":[],"sequence":2}\\n')
for line in sys.stdin:
 value=json.loads(line)
 print(json.dumps({'panes':[], 'selected':value['selected']}),flush=True)
'''
        popen = subprocess.Popen
        def fake_ssh(*args, **kwargs):
            return popen([sys.executable, '-u', '-c', producer], **kwargs)
        s = Stream('fake', '/tmp/fake', '', .5, ['a'])
        try:
            with patch('monitor.subprocess.Popen', side_effect=fake_ssh):
                frames = self.collect(s, lambda fs: len(fs) >= 2)
                self.assertEqual([f['sequence'] for f in frames], [1, 2])
                s.subscribe(['b'])
                self.collect(s, lambda fs: any(f.get('selected') == ['b'] for f in fs))
                self.assertEqual(s.connections, 1)
                s.process.terminate()
                self.collect(s, lambda fs: bool(s.error))
                self.assertIsNone(s.process)
                s.retry_at = 0
                self.collect(s, lambda fs: len(fs) >= 2)
                self.assertEqual(s.connections, 2)
                self.assertEqual(s.selected, ['b'])
        finally:
            s.close()

    def test_no_frames_timeout_preserves_nonblocking_poll(self):
        popen = subprocess.Popen
        def fake_ssh(*args, **kwargs):
            return popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        s = Stream('fake', '/tmp/fake', '', .5, [])
        try:
            with patch('monitor.subprocess.Popen', side_effect=fake_ssh):
                s.poll()
                s.started = time.monotonic() - 20
                start = time.monotonic()
                s.poll()
                self.assertLess(time.monotonic() - start, 1)
                self.assertIn('未收到画面', s.error)
                self.assertIsNone(s.process)
        finally:
            s.close()


if __name__ == '__main__':
    unittest.main()
