import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_probe import LocalCollector
from remote_probe import UsageLogWatcher, snapshot


def encoded(*records):
    return ''.join(json.dumps(record) + '\n' for record in records).encode()


def metadata(source='cli'):
    return dict(type='session_meta', payload=dict(id='root', source=source))


def codex_usage(incoming=100, output=20, cached=30, written=5, **changes):
    usage = dict(input_tokens=incoming, output_tokens=output,
                 cached_input_tokens=cached, cache_write_input_tokens=written,
                 total_tokens=incoming + output, reasoning_output_tokens=output)
    usage.update(changes)
    return dict(type='event_msg', payload=dict(type='token_count', info=dict(total_token_usage=usage)))


def claude_usage(mid='message-1', incoming=10, output=5, cached=30, written=20, **changes):
    usage = dict(input_tokens=incoming, output_tokens=output,
                 cache_read_input_tokens=cached, cache_creation_input_tokens=written)
    usage.update(changes)
    return dict(type='assistant', message=dict(id=mid, usage=usage, content=[]))


def codex_tool(call='call-1', item='item-1', kind='function_call_output', failed=False):
    return dict(type='response_item', payload=dict(type=kind, call_id=call,
                                                 id=item, output='failed' if failed else 'done'))


def claude_tool(tid='tool-1', failed=False):
    return dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id=tid,
                                                       is_error=failed, content='result')]))


def codex_call(call='call-1', item='call-item-1', kind='function_call'):
    return dict(type='response_item', payload=dict(type=kind, call_id=call, id=item,
                                                 name='synthetic_tool', arguments='{}'))


def claude_call(tid='tool-1', mid='message-1', stop_reason='tool_use'):
    record = claude_usage(mid)
    record['message'].update(content=[dict(type='tool_use', id=tid, name='SyntheticTool', input={})],
                             stop_reason=stop_reason)
    return record


class UsageProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'root.jsonl'

    def write(self, *records):
        self.path.write_bytes(encoded(*records))

    def append(self, *records):
        with self.path.open('ab') as log:
            log.write(encoded(*records))

    def drain(self, watcher, agent='codex'):
        for _ in range(10000):
            result = watcher.sample(self.path, agent)
            saved = watcher.files.get((agent, str(self.path)))
            if saved is None or saved['offset'] >= self.path.stat().st_size:
                return result
        self.fail('bounded scan did not reach EOF')

    def test_codex_authoritative_snapshots_replace_instead_of_sum(self):
        self.write(metadata(), codex_usage(), codex_usage(), codex_usage(150, 40, 50, 8))
        watcher = UsageLogWatcher()
        result = watcher.sample(self.path, 'codex')
        self.assertEqual((result['input_tokens'], result['output_tokens'], result['total_tokens']), (150, 40, 190))
        self.assertEqual((result['cached_input_tokens'], result['cache_write_input_tokens']), (50, 8))
        self.assertTrue(result['usage_available'])
        self.assertTrue(result['usage_complete'])
        self.append(codex_usage(80, 10))
        self.assertEqual(watcher.sample(self.path, 'codex')['total_tokens'], 90)

    def test_codex_active_calls_complete_and_replayed_starts_stay_closed(self):
        self.write(metadata(), codex_call(), codex_call())
        watcher = UsageLogWatcher()
        first = watcher.sample(self.path, 'codex')
        self.assertEqual((first['active_tools'], first['tool_completions']), (1, 0))
        self.append(codex_call('custom-2', 'custom-item-2', 'custom_tool_call'))
        self.assertEqual(watcher.sample(self.path, 'codex')['active_tools'], 2)
        self.append(codex_tool(), codex_tool())
        closed = watcher.sample(self.path, 'codex')
        self.assertEqual((closed['active_tools'], closed['tool_completions']), (1, 1))
        self.append(codex_tool('custom-2', 'output-2', 'custom_tool_call_output', failed=True), codex_call())
        final = watcher.sample(self.path, 'codex')
        self.assertEqual((final['active_tools'], final['tool_completions']), (0, 2))
        self.assertTrue(final['tools_complete'])

    def test_codex_terminal_lifecycle_clears_pending_without_counting_a_result(self):
        for kind in ('turn_complete', 'task_complete', 'turn_aborted', 'task_aborted',
                     'turn_failed', 'task_failed', 'error'):
            with self.subTest(kind=kind):
                self.write(metadata(), codex_call(), dict(type='event_msg', payload=dict(type=kind)))
                result = UsageLogWatcher().sample(self.path, 'codex')
                self.assertEqual((result['active_tools'], result['tool_completions']), (0, 0))

    def test_codex_builtin_active_status_requires_matchable_identity_and_status(self):
        self.write(metadata(), dict(type='response_item', payload=dict(type='web_search_call', id='web-1', status='searching')))
        watcher = UsageLogWatcher()
        self.assertEqual(watcher.sample(self.path, 'codex')['active_tools'], 1)
        self.append(dict(type='response_item', payload=dict(type='web_search_call', id='web-1', status='completed')))
        result = watcher.sample(self.path, 'codex')
        self.assertEqual((result['active_tools'], result['tool_completions']), (0, 1))
        self.append(dict(type='response_item', payload=dict(type='local_shell_call', id='shell-1', status='in_progress')))
        self.assertEqual(watcher.sample(self.path, 'codex')['active_tools'], 1)
        self.append(dict(type='response_item', payload=dict(type='local_shell_call', id='shell-1', status='cancelled')))
        cancelled = watcher.sample(self.path, 'codex')
        self.assertEqual((cancelled['active_tools'], cancelled['tool_completions']), (0, 1))
        self.append(dict(type='response_item', payload=dict(type='web_search_call', id='web-2')))
        self.assertIsNone(watcher.sample(self.path, 'codex')['active_tools'])

    def test_unidentified_tool_start_is_unknown_until_an_authoritative_turn_end(self):
        self.write(metadata(), codex_call(None))
        watcher = UsageLogWatcher()
        unknown = watcher.sample(self.path, 'codex')
        self.assertIsNone(unknown['active_tools'])
        self.assertTrue(unknown['tools_complete'])  # Completion accounting is unchanged.
        self.append(dict(type='event_msg', payload=dict(type='turn_complete')))
        self.assertEqual(watcher.sample(self.path, 'codex')['active_tools'], 0)
        self.write(claude_call(None))
        self.assertIsNone(UsageLogWatcher().sample(self.path, 'claude')['active_tools'])

    def test_claude_active_calls_ignore_sidechains_and_only_real_final_clears(self):
        sidechain = claude_call('child-tool', 'child-message')
        sidechain['isSidechain'] = True
        self.write(claude_call(), claude_call(), sidechain)
        watcher = UsageLogWatcher()
        self.assertEqual(watcher.sample(self.path, 'claude')['active_tools'], 1)
        self.append(claude_usage(output=8))  # Text/usage alone is not a turn end.
        self.assertEqual(watcher.sample(self.path, 'claude')['active_tools'], 1)
        self.append(claude_call('tool-2', 'message-2'))
        self.assertEqual(watcher.sample(self.path, 'claude')['active_tools'], 2)
        self.append(claude_tool(), claude_tool(), claude_call())
        result = watcher.sample(self.path, 'claude')
        self.assertEqual((result['active_tools'], result['tool_completions']), (1, 1))
        final = claude_usage('message-3')
        final['message']['stop_reason'] = 'end_turn'
        self.append(final)
        result = watcher.sample(self.path, 'claude')
        self.assertEqual((result['active_tools'], result['tool_completions']), (0, 1))

    def test_claude_tool_result_and_structured_terminal_boundaries(self):
        for boundary in (dict(type='system', subtype='turn_duration'),
                         dict(type='system', subtype='turn_aborted'),
                         dict(type='result', subtype='success'),
                         dict(type='result', subtype='error_during_execution'),
                         dict(type='assistant', isApiErrorMessage=True, message=dict(content=[]))):
            with self.subTest(boundary=boundary):
                self.write(claude_call(), boundary)
                result = UsageLogWatcher().sample(self.path, 'claude')
                self.assertEqual((result['active_tools'], result['tool_completions']), (0, 0))
        self.write(claude_call(), claude_tool())
        result = UsageLogWatcher().sample(self.path, 'claude')
        self.assertEqual((result['active_tools'], result['tool_completions']), (0, 1))

    def test_active_tool_scan_and_partial_record_do_not_guess_pending_count(self):
        self.write(metadata(), codex_call())
        watcher = UsageLogWatcher(max_bytes=31)
        self.assertIsNone(watcher.sample(self.path, 'codex')['active_tools'])
        self.assertEqual(self.drain(watcher)['active_tools'], 1)
        output = encoded(codex_tool())
        with self.path.open('ab') as log:
            log.write(output[:len(output) // 2])
        self.assertIsNone(self.drain(watcher)['active_tools'])
        with self.path.open('ab') as log:
            log.write(output[len(output) // 2:])
        final = self.drain(watcher)
        self.assertEqual((final['active_tools'], final['tool_completions']), (0, 1))

    def test_corrupt_history_and_excluded_roots_keep_active_tools_unknown(self):
        self.path.write_bytes(encoded(metadata(), codex_call()) + b'{broken}\n')
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertIsNone(result['active_tools'])
        self.write(metadata(dict(subagent=dict(thread_spawn=dict(parent_thread_id='parent')))), codex_call())
        self.assertIsNone(UsageLogWatcher().sample(self.path, 'codex')['active_tools'])
        self.assertIsNone(UsageLogWatcher().sample(None, 'claude')['active_tools'])

    def test_claude_message_id_merges_individual_maxima_and_cache_input(self):
        self.write(claude_usage(), claude_usage(output=7, cached=29),
                   claude_usage(incoming=9, output=6, written=22),
                   claude_usage('message-2', incoming=2, output=3, cached=4, written=6))
        result = UsageLogWatcher().sample(self.path, 'claude')
        self.assertEqual((result['input_tokens'], result['output_tokens'], result['total_tokens']), (74, 10, 84))
        self.assertEqual((result['cached_input_tokens'], result['cache_write_input_tokens']), (34, 28))
        self.assertTrue(result['usage_complete'])

    def test_claude_duplicate_messages_remain_deduplicated_across_samples(self):
        self.write(claude_usage())
        watcher = UsageLogWatcher()
        first = watcher.sample(self.path, 'claude')
        self.append(claude_usage(), claude_usage(output=9))
        result = watcher.sample(self.path, 'claude')
        self.assertEqual(first['input_tokens'], result['input_tokens'])
        self.assertEqual(result['output_tokens'], 9)
        self.assertEqual(result['total_tokens'], 69)

    def test_initial_tool_scan_is_bounded_and_codex_usage_bootstraps_from_tail(self):
        prefix = encoded(metadata(), codex_usage())
        self.path.write_bytes(prefix + encoded(codex_tool(), codex_usage(180, 60)))
        watcher = UsageLogWatcher(max_bytes=len(prefix))
        first = watcher.sample(self.path, 'codex')
        self.assertEqual(watcher.files[('codex', str(self.path))]['offset'], len(prefix))
        self.assertEqual(first['total_tokens'], 240)
        self.assertTrue(first['usage_complete'])
        self.assertFalse(first['tools_complete'])
        final = self.drain(watcher)
        self.assertEqual(final['total_tokens'], 240)
        self.assertEqual(final['tool_completions'], 1)
        self.assertTrue(final['usage_complete'])
        self.assertTrue(final['tools_complete'])

    def test_codex_tail_snapshot_never_rolls_back_while_history_replays(self):
        self.write(metadata(), codex_usage())
        for index in range(40):
            self.append(codex_usage(200 + index, 30 + index))
        self.append(codex_usage(9000, 5000))
        watcher = UsageLogWatcher(max_bytes=512)
        first = watcher.sample(self.path, 'codex')
        self.assertEqual(first['total_tokens'], 14000)
        self.assertTrue(first['usage_complete'])
        self.assertFalse(first['tools_complete'])
        for _ in range(5):
            self.assertEqual(watcher.sample(self.path, 'codex')['total_tokens'], 14000)
        self.append(codex_usage(11000, 7000))
        updated = watcher.sample(self.path, 'codex')
        self.assertEqual(updated['total_tokens'], 18000)
        self.assertTrue(updated['usage_complete'])
        self.assertFalse(updated['tools_complete'])

    def test_codex_tail_skips_rate_limit_only_events(self):
        self.write(metadata(), codex_usage())
        self.append(dict(type='response_item', payload=dict(type='message', text='x' * 2048)),
                    codex_usage(2000, 1000),
                    dict(type='event_msg', payload=dict(type='token_count', info=None)))
        result = UsageLogWatcher(max_bytes=768).sample(self.path, 'codex')
        self.assertEqual(result['total_tokens'], 3000)
        self.assertTrue(result['usage_complete'])
        self.assertFalse(result['tools_complete'])

    def test_codex_tail_latest_invalid_usage_is_partial_until_a_new_valid_snapshot(self):
        self.write(metadata(), codex_usage())
        self.append(dict(type='response_item', payload=dict(type='message', text='x' * 2048)),
                    codex_usage(2000, 1000), codex_usage(input_tokens=-1))
        watcher = UsageLogWatcher(max_bytes=768)
        partial = watcher.sample(self.path, 'codex')
        self.assertEqual(partial['total_tokens'], 3000)
        self.assertFalse(partial['usage_complete'])
        self.append(codex_usage(3000, 2000))
        complete = watcher.sample(self.path, 'codex')
        self.assertEqual(complete['total_tokens'], 5000)
        self.assertTrue(complete['usage_complete'])

    def test_codex_tail_latest_malformed_record_is_not_hidden_by_older_snapshot(self):
        self.write(metadata(), codex_usage())
        self.append(dict(type='response_item', payload=dict(type='message', text='x' * 2048)),
                    codex_usage(2000, 1000))
        with self.path.open('ab') as log:
            log.write(b'{"type":"event_msg","payload":{"type":"token_count",broken}\n')
        result = UsageLogWatcher(max_bytes=768).sample(self.path, 'codex')
        self.assertEqual(result['total_tokens'], 3000)
        self.assertFalse(result['usage_complete'])

    def test_codex_tail_usage_outside_bounded_window_stays_partial(self):
        self.write(metadata(), codex_usage(),
                   dict(type='response_item', payload=dict(type='message', text='x' * 4096)))
        result = UsageLogWatcher(max_bytes=512).sample(self.path, 'codex')
        self.assertEqual(result['total_tokens'], 120)
        self.assertFalse(result['usage_complete'])
        self.assertFalse(result['tools_complete'])

    def test_codex_pending_tool_tail_can_recover_without_new_usage(self):
        self.write(metadata(), codex_usage(),
                   dict(type='response_item', payload=dict(type='message', text='x' * 4096)),
                   codex_usage(2000, 1000))
        tool = encoded(codex_tool('pending', 'pending-item'))
        with self.path.open('ab') as log:
            log.write(tool[:len(tool) // 2])
        watcher = UsageLogWatcher(max_bytes=768)
        self.assertFalse(watcher.sample(self.path, 'codex')['usage_complete'])
        with self.path.open('ab') as log:
            log.write(tool[len(tool) // 2:])
        result = watcher.sample(self.path, 'codex')
        self.assertEqual(result['total_tokens'], 3000)
        self.assertTrue(result['usage_complete'])

    def test_claude_usage_never_uses_the_codex_tail_shortcut(self):
        self.write(claude_usage(), dict(type='user', message=dict(content='x' * 4096)),
                   claude_usage('message-2', incoming=1000))
        watcher = UsageLogWatcher(max_bytes=512)
        initial = watcher.sample(self.path, 'claude')
        self.assertEqual(initial['total_tokens'], 65)
        self.assertFalse(initial['usage_complete'])
        final = self.drain(watcher, 'claude')
        self.assertEqual(final['total_tokens'], 1120)
        self.assertTrue(final['usage_complete'])

    def test_jsonl_record_survives_many_small_read_chunks(self):
        self.write(metadata(), codex_usage(), codex_tool())
        result = self.drain(UsageLogWatcher(max_bytes=7))
        self.assertEqual((result['total_tokens'], result['tool_completions']), (120, 1))
        self.assertTrue(result['usage_complete'])
        self.assertTrue(result['tools_complete'])

    def test_partial_last_line_waits_for_writer_and_preserves_previous_totals(self):
        self.write(metadata(), codex_usage())
        watcher = UsageLogWatcher()
        watcher.sample(self.path, 'codex')
        line = encoded(codex_usage(200, 60))
        with self.path.open('ab') as log:
            log.write(line[:len(line) // 2])
        partial = watcher.sample(self.path, 'codex')
        self.assertEqual(partial['total_tokens'], 120)
        self.assertFalse(partial['usage_complete'])
        with self.path.open('ab') as log:
            log.write(line[len(line) // 2:])
        final = watcher.sample(self.path, 'codex')
        self.assertEqual(final['total_tokens'], 260)
        self.assertTrue(final['usage_complete'])

    def test_complete_final_record_without_newline_is_counted_once(self):
        self.path.write_bytes(encoded(metadata()) + encoded(codex_tool()).rstrip(b'\n'))
        watcher = UsageLogWatcher()
        first = watcher.sample(self.path, 'codex')
        self.assertEqual(first['tool_completions'], 1)
        self.assertTrue(first['tools_complete'])
        with self.path.open('ab') as log:
            log.write(b'\n')
        self.assertEqual(watcher.sample(self.path, 'codex')['tool_completions'], 1)

    def test_file_replacement_resets_usage_tools_and_identity(self):
        self.write(metadata(), codex_usage(), codex_tool())
        watcher = UsageLogWatcher()
        before = watcher.sample(self.path, 'codex')
        self.path.rename(self.path.with_suffix('.old'))
        self.write(metadata(), codex_usage(3, 2))
        after = watcher.sample(self.path, 'codex')
        self.assertNotEqual(before['identity'], after['identity'])
        self.assertEqual((after['total_tokens'], after['tool_completions']), (5, 0))
        self.assertTrue(after['tools_complete'])

    def test_same_inode_truncation_resets_usage_and_identity(self):
        self.write(metadata(), codex_usage(), codex_tool())
        watcher = UsageLogWatcher()
        before = watcher.sample(self.path, 'codex')
        inode = self.path.stat().st_ino
        self.write(metadata(), codex_usage(3, 2))
        after = watcher.sample(self.path, 'codex')
        self.assertEqual(inode, self.path.stat().st_ino)
        self.assertNotEqual(before['identity'], after['identity'])
        self.assertEqual(after['total_tokens'], 5)

    def test_rewrite_that_regrows_before_next_sample_is_detected_by_anchor(self):
        self.write(metadata(), codex_usage())
        watcher = UsageLogWatcher()
        before = watcher.sample(self.path, 'codex')
        self.write(metadata(), codex_usage(9000, 8000), codex_tool())
        after = watcher.sample(self.path, 'codex')
        self.assertNotEqual(before['identity'], after['identity'])
        self.assertEqual((after['total_tokens'], after['tool_completions']), (17000, 1))

    def test_first_identity_is_stable_across_watcher_discovery_order(self):
        self.write(metadata(), codex_usage())
        other = self.path.with_name('other.jsonl')
        other.write_bytes(encoded(metadata(), codex_usage()))
        one, two = UsageLogWatcher(), UsageLogWatcher()
        first = one.sample(self.path, 'codex')
        two.sample(other, 'codex')
        second = two.sample(self.path, 'codex')
        self.assertEqual(first['identity'], second['identity'])

    def test_claude_sidechains_and_explicit_subagent_paths_are_excluded(self):
        child = claude_usage('child', incoming=900, output=800)
        child['isSidechain'] = True
        tool = claude_tool('child-tool')
        tool['isSidechain'] = True
        self.write(claude_usage(), child, tool, claude_tool())
        watcher = UsageLogWatcher()
        root = watcher.sample(self.path, 'claude')
        self.assertEqual((root['total_tokens'], root['tool_completions']), (65, 1))
        child_path = self.path.parent / 'subagents' / 'child.jsonl'
        child_path.parent.mkdir()
        child_path.write_bytes(encoded(claude_usage()))
        excluded = watcher.sample(child_path, 'claude')
        self.assertIsNone(excluded['total_tokens'])
        self.assertIsNone(excluded['tool_completions'])

    def test_codex_child_metadata_and_unvalidated_root_never_count_usage(self):
        for source in (dict(subagent=dict(thread_spawn=dict(parent_thread_id='parent'))), None):
            self.write(metadata(source), codex_usage(), codex_tool())
            result = UsageLogWatcher().sample(self.path, 'codex')
            self.assertFalse(result['usage_available'])
            self.assertIsNone(result['total_tokens'])
            self.assertIsNone(result['tool_completions'])

    def test_missing_log_and_missing_usage_remain_null(self):
        watcher = UsageLogWatcher()
        for result in (watcher.sample(None, 'codex'), watcher.sample(self.path, 'codex')):
            self.assertIsNone(result['total_tokens'])
            self.assertIsNone(result['tool_completions'])
            self.assertFalse(result['usage_complete'])
        self.write(metadata(), dict(type='event_msg', payload=dict(type='token_count', info=None)))
        result = watcher.sample(self.path, 'codex')
        self.assertIsNone(result['input_tokens'])
        self.assertIsNone(result['output_tokens'])
        self.assertIsNone(result['total_tokens'])
        self.assertFalse(result['usage_available'])
        self.assertEqual(result['tool_completions'], 0)
        self.assertTrue(result['tools_complete'])

    def test_codex_missing_optional_cache_is_unknown_without_invented_zero(self):
        record = codex_usage()
        del record['payload']['info']['total_token_usage']['cached_input_tokens']
        del record['payload']['info']['total_token_usage']['cache_write_input_tokens']
        self.write(metadata(), record)
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertTrue(result['usage_complete'])
        self.assertIsNone(result['cached_input_tokens'])
        self.assertIsNone(result['cache_write_input_tokens'])

    def test_claude_missing_field_is_unknown_and_can_be_filled_by_same_message(self):
        record = claude_usage()
        del record['message']['usage']['cache_read_input_tokens']
        self.write(record)
        watcher = UsageLogWatcher()
        partial = watcher.sample(self.path, 'claude')
        self.assertIsNone(partial['input_tokens'])
        self.assertIsNone(partial['cached_input_tokens'])
        self.assertIsNone(partial['total_tokens'])
        self.assertFalse(partial['usage_complete'])
        self.append(claude_usage())
        self.assertTrue(watcher.sample(self.path, 'claude')['usage_complete'])

    def test_invalid_negative_float_and_bool_usage_never_become_complete_zero(self):
        for value in (-1, 1.5, True):
            self.write(metadata(), codex_usage(input_tokens=value))
            codex = UsageLogWatcher().sample(self.path, 'codex')
            self.assertFalse(codex['usage_available'])
            self.assertIsNone(codex['input_tokens'])
            self.assertFalse(codex['usage_complete'])
            self.write(claude_usage(cache_read_input_tokens=value))
            claude = UsageLogWatcher().sample(self.path, 'claude')
            self.assertIsNone(claude['input_tokens'])
            self.assertFalse(claude['usage_complete'])

    def test_codex_valid_later_snapshot_recovers_usage_after_missing_or_corrupt_history(self):
        self.path.write_bytes(encoded(metadata(), codex_usage(input_tokens=-1)) + b'{bad json}\n' + encoded(codex_usage(180, 60)))
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertEqual(result['total_tokens'], 240)
        self.assertTrue(result['usage_complete'])
        self.assertFalse(result['tools_complete'])

    def test_bad_later_snapshot_preserves_observed_totals_but_marks_partial(self):
        self.write(metadata(), codex_usage(), codex_usage(input_tokens=-1))
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertEqual(result['total_tokens'], 120)
        self.assertTrue(result['usage_available'])
        self.assertFalse(result['usage_complete'])

    def test_claude_api_error_display_does_not_invent_an_unbilled_message(self):
        self.write(claude_usage(), dict(type='assistant', isApiErrorMessage=True,
                                        message=dict(content=[dict(type='text', text='API error')])) )
        result = UsageLogWatcher().sample(self.path, 'claude')
        self.assertEqual(result['total_tokens'], 65)
        self.assertTrue(result['usage_complete'])

    def test_claude_unidentified_real_usage_is_partial(self):
        unidentified = claude_usage()
        del unidentified['message']['id']
        self.write(claude_usage(), unidentified)
        result = UsageLogWatcher().sample(self.path, 'claude')
        self.assertEqual(result['total_tokens'], 65)
        self.assertFalse(result['usage_complete'])

    def test_codex_completed_tools_deduplicate_both_ids_and_count_failures(self):
        self.write(metadata(), codex_tool(), codex_tool(item='item-2'),
                   codex_tool('call-2', 'item-3', 'custom_tool_call_output', failed=True),
                   codex_tool(None, 'item-3', failed=True))
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertEqual(result['tool_completions'], 2)
        self.assertTrue(result['tools_complete'])

    def test_claude_completed_tools_deduplicate_and_count_failed_results(self):
        self.write(claude_tool(), claude_tool(), claude_tool('tool-2', failed=True))
        result = UsageLogWatcher().sample(self.path, 'claude')
        self.assertEqual(result['tool_completions'], 2)
        self.assertTrue(result['tools_complete'])

    def test_codex_later_alias_bridge_merges_previously_separate_tools(self):
        self.write(metadata(), codex_tool(None, 'item-1'), codex_tool('call-1', None),
                   codex_tool('call-1', 'item-1'), codex_tool('call-1', 'item-2'))
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertEqual(result['tool_completions'], 1)
        self.assertTrue(result['tools_complete'])

    def test_codex_builtin_tool_results_require_explicit_terminal_status_and_id(self):
        records = [metadata()]
        for index, kind in enumerate(('web_search_call', 'local_shell_call', 'image_generation_call')):
            for status in ('started', 'in_progress', 'completed', 'completed', 'failed'):
                tid = '{}-{}'.format(index, 'failed' if status == 'failed' else 'done')
                records.append(dict(type='response_item', payload=dict(type=kind, id=tid, status=status)))
        self.write(*records)
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertEqual(result['tool_completions'], 6)
        self.assertTrue(result['tools_complete'])
        self.append(dict(type='response_item', payload=dict(type='web_search_call', status='completed')))
        self.assertFalse(UsageLogWatcher().sample(self.path, 'codex')['tools_complete'])

    def test_missing_tool_identity_is_partial(self):
        self.write(metadata(), codex_tool(None, None), codex_tool())
        result = UsageLogWatcher().sample(self.path, 'codex')
        self.assertEqual(result['tool_completions'], 1)
        self.assertFalse(result['tools_complete'])

    def test_read_error_preserves_observed_values_and_identity(self):
        self.write(metadata(), codex_usage(), codex_tool())
        watcher = UsageLogWatcher()
        before = watcher.sample(self.path, 'codex')
        with patch('remote_probe.open', side_effect=PermissionError('unreadable')):
            after = watcher.sample(self.path, 'codex')
        self.assertEqual((after['total_tokens'], after['tool_completions'], after['identity']),
                         (before['total_tokens'], before['tool_completions'], before['identity']))
        self.assertFalse(after['usage_complete'])
        self.assertFalse(after['tools_complete'])

    def test_oversized_line_is_bounded_and_later_codex_snapshot_recovers_usage(self):
        self.path.write_bytes(encoded(metadata()) + b'"' + b'x' * 150 + b'"\n' + encoded(codex_usage()))
        watcher = UsageLogWatcher(max_bytes=32)
        watcher.MAX_LINE_BYTES = 128
        result = self.drain(watcher)
        # The usage fixture is larger than this tiny limit; lift it and append
        # a new authoritative snapshot after verifying the discarded carry.
        self.assertLessEqual(len(watcher.files[('codex', str(self.path))]['buffer']), 128)
        self.assertFalse(result['tools_complete'])
        watcher.MAX_LINE_BYTES = 4096
        self.append(codex_usage())
        result = self.drain(watcher)
        self.assertEqual(result['total_tokens'], 120)
        self.assertTrue(result['usage_complete'])
        self.assertFalse(result['tools_complete'])

    def test_only_inactive_paths_are_pruned(self):
        self.write(metadata(), codex_usage())
        other = self.path.with_name('other.jsonl')
        other.write_bytes(encoded(metadata(), codex_usage()))
        watcher = UsageLogWatcher()
        with patch('remote_probe.time.monotonic', return_value=0):
            watcher.sample(self.path, 'codex')
            watcher.sample(other, 'codex')
        with patch('remote_probe.time.monotonic', return_value=121):
            watcher.sample(self.path, 'codex')
        self.assertIn(('codex', str(self.path)), watcher.files)
        self.assertNotIn(('codex', str(other)), watcher.files)

    def test_local_collector_attaches_only_the_root_usage(self):
        self.write(metadata(), codex_usage())
        child = self.path.with_name('child.jsonl')
        child.write_bytes(encoded(metadata(dict(subagent=dict(thread_spawn=dict(parent_thread_id='root')))),
                                  codex_usage(9000, 8000)))
        collector = LocalCollector(self.path.parent)
        collector.discovery = [('root', dict(path=self.path, pid=42, metadata=dict(cwd='/work'), children=[child]))]
        collector.next_discovery = float('inf')
        row = collector.snapshot()['panes'][0]
        self.assertEqual(row['telemetry']['scope'], 'root_session')
        self.assertEqual(row['telemetry']['total_tokens'], 120)
        self.assertTrue(row['telemetry']['usage_complete'])

    def test_remote_snapshot_attaches_independent_root_usage(self):
        self.write(metadata(), codex_usage(), codex_tool())
        listing = 'main\t0\t0\t%1\tcodex\t/work\t0\t42\t\t0\n'
        class Result:
            returncode = 0
            stderr = ''
            def __init__(self, stdout):
                self.stdout = stdout
        with patch('remote_probe.subprocess.run', side_effect=[Result(listing), Result('› ')]), \
                patch('remote_probe.read_processes', return_value={42: (1, 'S', 'codex')}), \
                patch('remote_probe.ROLLOUTS.sample', return_value=([], '', True, None)), \
                patch('remote_probe.ROLLOUTS.root_log', return_value=str(self.path)), \
                patch('remote_probe.CODEX_BACKGROUND.sample', return_value={}), \
                patch('remote_probe.CODEX_COMPLETIONS.apply'), \
                patch('remote_probe.USAGE_LOGS', UsageLogWatcher()):
            row = snapshot('', set())['panes'][0]
        self.assertEqual(row['telemetry']['total_tokens'], 120)
        self.assertEqual(row['telemetry']['tool_completions'], 1)
        self.assertTrue(row['telemetry']['usage_complete'])


if __name__ == '__main__':
    unittest.main()
