import json
from pathlib import Path
import tempfile
import unittest

from remote_probe import ResultLogWatcher


def codex_meta(source='cli'):
    return dict(type='session_meta', payload=dict(id='synthetic-root', source=source))


def event(kind):
    return dict(type='event_msg', payload=dict(type=kind))


def claude_reply(mid='synthetic-final', text='Synthetic final answer', **extra):
    return dict(type='assistant', message=dict(id=mid, stop_reason='end_turn',
                content=[dict(type='text', text=text)]), **extra)


class ResultMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'session.jsonl'
        self.watcher = ResultLogWatcher()

    def write(self, records, append=False):
        with self.path.open('a' if append else 'w') as log:
            log.write(''.join(json.dumps(item) + '\n' for item in records))

    def sample(self, agent='codex'):
        return self.watcher.sample(self.path, agent)

    def test_codex_initial_history_is_stable_across_reconnect_and_tools_are_not_results(self):
        self.write([codex_meta(), event('task_started'), event('task_complete')])
        first = self.sample()
        self.assertTrue(first['result_observable'])
        self.assertIsInstance(first['result_marker'], str)
        self.assertEqual(self.sample(), first)
        self.assertEqual(ResultLogWatcher().sample(self.path, 'codex'), first)
        self.write([event('token_count'), dict(type='response_item', payload=dict(type='function_call_output', output='done')),
                    dict(type='response_item', payload=dict(type='message', role='assistant', phase='commentary',
                         content=[dict(type='output_text', text='Working now')]))], append=True)
        self.assertEqual(self.sample()['result_marker'], first['result_marker'])
        self.write([event('task_started'), event('task_complete')], append=True)
        second = self.sample()
        self.assertNotEqual(second['result_marker'], first['result_marker'])
        self.assertEqual(second['result_identity'], first['result_identity'])

    def test_codex_full_log_without_result_is_known_empty(self):
        self.write([codex_meta(), event('task_started')])
        row = self.sample()
        self.assertTrue(row['result_observable'])
        self.assertIsNone(row['result_marker'])

    def test_codex_duplicate_completion_boundary_does_not_become_another_reply(self):
        self.write([codex_meta(), event('task_started'), event('task_complete')])
        first = self.sample()
        self.write([event('turn_complete')], append=True)
        self.assertEqual(self.sample()['result_marker'], first['result_marker'])

    def test_claude_final_text_only_not_thinking_tool_sidechain_or_duration(self):
        first_record = dict(type='user', message=dict(content='Synthetic request'))
        self.write([first_record, claude_reply()])
        first = self.sample('claude')
        self.write([dict(type='system', subtype='turn_duration'),
                    dict(type='assistant', message=dict(id='thinking', stop_reason='end_turn',
                         content=[dict(type='thinking', thinking='hidden')])),
                    dict(type='assistant', message=dict(id='tool', stop_reason='tool_use',
                         content=[dict(type='tool_use', name='Bash', id='tool-a', input={})])),
                    claude_reply('side-result', isSidechain=True)], append=True)
        self.assertEqual(self.sample('claude')['result_marker'], first['result_marker'])
        self.assertEqual(ResultLogWatcher().sample(self.path, 'claude')['result_marker'], first['result_marker'])

    def test_same_text_new_message_id_is_new_result_but_repeated_id_is_not(self):
        self.write([dict(type='user', message=dict(content='Synthetic request')), claude_reply('first-id')])
        first = self.sample('claude')
        self.write([claude_reply('first-id')], append=True)
        self.assertEqual(self.sample('claude')['result_marker'], first['result_marker'])
        self.write([claude_reply('new-id')], append=True)
        second = self.sample('claude')
        self.assertNotEqual(second['result_marker'], first['result_marker'])
        self.assertEqual(second['result_identity'], first['result_identity'])

    def test_reconnect_same_message_replayed_after_original_rolls_out_of_tail_is_stable(self):
        self.write([dict(type='user', message=dict(content='Synthetic request')), claude_reply('same-message')])
        self.watcher = ResultLogWatcher(max_bytes=512)
        first = self.sample('claude')
        self.write([dict(type='progress', data='x' * 120)] * 20 + [claude_reply('same-message')], append=True)
        self.assertEqual(self.sample('claude')['result_marker'], first['result_marker'])
        reconnected = ResultLogWatcher(max_bytes=512).sample(self.path, 'claude')
        self.assertEqual(reconnected['result_identity'], first['result_identity'])
        self.assertEqual(reconnected['result_marker'], first['result_marker'])

    def test_error_heartbeat_and_task_notification_do_not_mark_result(self):
        self.write([dict(type='user', message=dict(content='Synthetic request'))])
        first = self.sample('claude')
        self.write([dict(type='assistant', isApiErrorMessage=True, message=dict(stop_reason='end_turn',
                         content=[dict(type='text', text='error')])),
                    dict(type='system', subtype='api_error'), dict(type='progress', data=dict(type='agent_progress')),
                    dict(type='user', message=dict(content='<task-notification>completed</task-notification>'))], append=True)
        self.assertEqual(self.sample('claude')['result_marker'], first['result_marker'])

    def test_unknown_old_tail_without_result_remains_unknown_until_verified_final(self):
        self.write([codex_meta(), event('task_complete')] + [dict(type='progress', data='x' * 120)] * 100)
        self.watcher = ResultLogWatcher(max_bytes=512)
        old = self.sample()
        self.assertFalse(old['result_observable'])
        self.assertIsNone(old['result_marker'])
        self.write([event('task_started'), event('task_complete')], append=True)
        new = self.sample()
        self.assertTrue(new['result_observable'])
        self.assertIsNotNone(new['result_marker'])

    def test_truncation_or_in_place_rewrite_changes_identity_before_new_result(self):
        self.write([codex_meta(), event('task_started'), event('task_complete')])
        first = self.sample()
        self.write([codex_meta(), event('task_started')])
        truncated = self.sample()
        self.assertNotEqual(truncated['result_identity'], first['result_identity'])
        self.assertIsNone(truncated['result_marker'])
        self.write([event('task_complete')], append=True)
        new = self.sample()
        self.assertEqual(new['result_identity'], truncated['result_identity'])
        self.assertIsNotNone(new['result_marker'])

    def test_partial_or_malformed_suffix_is_unknown_not_a_new_result(self):
        self.write([codex_meta(), event('task_complete')])
        first = self.sample()
        with self.path.open('ab') as log:
            log.write(b'{"type":"event_msg","payload":')
        partial = self.sample()
        self.assertFalse(partial['result_observable'])
        self.assertEqual(partial['result_marker'], first['result_marker'])
        with self.path.open('ab') as log:
            log.write(b'{"type":"task_started"}}\nnot-json\n')
        self.assertFalse(self.sample()['result_observable'])
        self.write([event('task_complete')], append=True)
        self.assertTrue(self.sample()['result_observable'])

    def test_complete_final_record_without_trailing_newline(self):
        self.path.write_text(json.dumps(codex_meta()) + '\n' + json.dumps(event('task_complete')))
        first = self.sample()
        self.assertTrue(first['result_observable'])
        self.assertIsNotNone(first['result_marker'])
        with self.path.open('a') as log:
            log.write('\n')
        self.assertEqual(self.sample()['result_marker'], first['result_marker'])
        self.assertEqual(ResultLogWatcher().sample(self.path, 'codex')['result_marker'], first['result_marker'])

    def test_child_or_unreadable_logs_are_unobservable(self):
        self.write([codex_meta(dict(subagent=dict(parent='root'))), event('task_complete')])
        self.assertEqual(self.sample(), ResultLogWatcher.empty())
        self.write([claude_reply(isSidechain=True)])
        self.assertEqual(self.sample('claude'), ResultLogWatcher.empty())
        self.path.unlink()
        self.assertEqual(self.sample(), ResultLogWatcher.empty())

    def test_large_gap_does_not_promote_old_result_to_new_marker(self):
        self.write([codex_meta(), event('task_complete')])
        self.watcher = ResultLogWatcher(max_bytes=512)
        first = self.sample()
        self.write([dict(type='progress', data='x' * 120)] * 100, append=True)
        row = self.sample()
        self.assertFalse(row['result_observable'])
        self.assertEqual(row['result_marker'], first['result_marker'])


if __name__ == '__main__':
    unittest.main()
