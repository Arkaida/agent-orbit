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

    def test_interrupt_footer_allows_background_shortcut_suffix(self):
        result = analyse_screen(screen(running='• Working (42s • esc to interrupt · ctrl+b to run in background)'))
        self.assertEqual(result['state'], 'running')
        self.assertIn('ctrl+b', result['working'])

    def test_uisg_working_footer_with_external_background_terminal_hints(self):
        for suffix in (' · 2 background terminals running',
                       ' · 2 background terminals running · /ps to view · /stop to close',
                       ' · 1 background terminal running',
                       ' · 2 background term…', ' · 2 background termina',
                       ' · 2 background terminals running · /ps to vi…',
                       ' · ctrl+b to run in background'):
            with self.subTest(suffix=suffix):
                result = analyse_screen(screen(running='• Working (2h 06m 43s • esc to interrupt)' + suffix))
                self.assertEqual(result['state'], 'running')
                self.assertEqual(result['error_kind'], '')
                self.assertNotIn('esc to interrupt', result['dialogue_text'])

    def test_external_footer_hints_do_not_make_drafts_or_quoted_prose_busy(self):
        footer = '• Working (2h 06m 43s • esc to interrupt) · 2 background terminals running'
        self.assertEqual(analyse_screen(screen(draft=footer))['state'], 'idle')
        for prose in ('• Example: `' + footer + '`', footer + ' is only an example.',
                      '• Example: Working (42s • esc to interrupt) · 2 background terminals running',
                      '• Documentation (esc to interrupt) · 2 b',
                      footer + ' · arbitrary prose', footer + ' · /ps to view · unknown hint'):
            with self.subTest(prose=prose):
                self.assertEqual(analyse_screen(screen(body=prose))['state'], 'idle')

    def test_retained_terminal_hint_without_interrupt_footer_stays_idle(self):
        self.assertEqual(analyse_screen(screen(body='2 background terminals running · /ps to view · /stop to close'))['state'], 'idle')
        wrapped = '• Working (2h 06m 43s • esc to interrupt)\n  · 2 background terminals running · /ps to view'
        self.assertEqual(analyse_screen(screen(running=wrapped))['state'], 'running')

    def test_closed_code_fence_cannot_become_a_live_interrupt_footer(self):
        footer = '• Working (2h 06m 43s • esc to interrupt) · 2 background terminals running'
        self.assertEqual(analyse_screen(screen(body='• Example:\n```text\n' + footer + '\n```'))['state'], 'idle')
        self.assertEqual(analyse_screen(screen(body='• Streaming code:\n```python\nresult = 1', running=footer))['state'], 'running')

    def test_long_draft_keeps_live_status_before_current_prompt(self):
        draft = 'first draft line\n' + '\n'.join('  draft continuation {}'.format(i) for i in range(25))
        result = analyse_screen(screen(running='• Working (42s • esc to interrupt)', draft=draft))
        self.assertEqual(result['state'], 'running')
        self.assertIn('42s', result['working'])
        self.assertEqual(analyse_screen(screen(draft=draft))['state'], 'idle')

    def test_quoted_interrupt_hint_inside_draft_or_prose_is_not_running(self):
        draft = 'example:\n  • Working (42s • esc to interrupt · ctrl+b to run in background)\n' + '\n'.join('  draft continuation {}'.format(i) for i in range(20))
        self.assertEqual(analyse_screen(screen(draft=draft))['state'], 'idle')
        quoted = '• The command prints `Working (42s • esc to interrupt)`.'
        self.assertEqual(analyse_screen(screen(body=quoted))['state'], 'idle')

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

    def test_dialogue_window_keeps_full_assistant_body_and_gpt_prose(self):
        result = analyse_screen(screen('› user request\n  wrapped user input\n• GPT models support context growth.\n  first line\n  second line\n  third line\n  fourth line',
                                       '• Working (42s • esc to interrupt)', draft='private draft'))
        self.assertTrue(result['dialogue_available'])
        self.assertIn('GPT models support context growth.', result['dialogue_text'])
        self.assertIn('first line', result['dialogue_text'])
        self.assertIn('fourth line', result['dialogue_text'])
        self.assertNotIn('private draft', result['dialogue_text'])
        self.assertNotIn('user input', result['dialogue_text'])
        self.assertNotIn('Working', result['dialogue_text'])
        self.assertNotIn('Main [default]', result['dialogue_text'])

    def test_dialogue_excludes_tool_blocks_counters_and_history(self):
        result = analyse_screen(screen('• Actual answer\n  visible details\n• Ran python benchmark.py\n  ⎿ 999 lines of tool output\n+ 100 more lines\n• Follow-up prose\n  final answer\n+ Show details\n⎿ Tip: Use /btw to ask a question',
                                       '• Working (5s • esc to interrupt)'))
        self.assertEqual(result['dialogue_text'], 'Actual answer\n  visible details\nFollow-up prose\n  final answer')
        self.assertFalse(analyse_screen(screen('• Actual answer\nBack to bottom'))['dialogue_available'])
        self.assertFalse(analyse_screen('unknown terminal layout')['dialogue_available'])


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
