import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from local_probe import LocalCollector
from desktop_bridge import FeedState, source_payload
from process_snapshot import ProcessSnapshot


class FixtureResources:
    def sample_all(self, roots, processes):
        return {pid: dict(identity='fixture:' + str(pid), sample_id=1)
                for pid in roots if isinstance(pid, int) and pid > 0}


def event(kind, **extra):
    return json.dumps(dict(type='event_msg', payload=dict(type=kind, **extra))) + '\n'


def message(text, role='assistant', identity=None):
    payload = dict(type='message', role=role, content=[dict(type='output_text', text=text)])
    if identity is not None:
        payload['id'] = identity
    return json.dumps(dict(type='response_item', payload=payload)) + '\n'


class LocalProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / 'rollout-root.jsonl'
        self.path.write_text(json.dumps(dict(type='session_meta', payload=dict(id='root-id', source='vscode', cwd='/work/demo'))) + '\n' + event('task_started'))
        self.opened = [self.path]
        def runner(args, **kwargs):
            output = '10 1 S /bin/codex\n' if args[0] == 'ps' else 'p10\n' + ''.join('n' + str(path) + '\n' for path in self.opened)
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr='')
        self.collector = LocalCollector(self.home, runner=runner)
        self.collector.resources = FixtureResources()

    def test_actual_turn_events_drive_running_and_completion(self):
        first = self.collector.snapshot()['panes'][0]
        self.assertEqual(first['state'], 'running')
        self.assertEqual(self.collector.snapshot()['panes'][0]['state'], 'running')
        with self.path.open('a') as log: log.write(event('task_complete'))
        row = self.collector.snapshot()['panes'][0]
        self.assertEqual((row['state'], row['source'], row['pane_id']), ('idle', 'local', 'local:root-id'))

    def test_dialogue_accumulates_distinct_assistant_messages_in_one_turn(self):
        from activity_tracker import ActivityTracker
        tracker = ActivityTracker()
        first = self.collector.snapshot()['panes'][0]
        tracker.update([first], 0)
        with self.path.open('a') as log: log.write(message('Initial result', identity='m1'))
        second = self.collector.snapshot()['panes'][0]
        observed = tracker.update([second], 1)[0]['dialogue_output']
        self.assertEqual(observed['total_chars'], len('Initialresult'))
        with self.path.open('a') as log:
            log.write(message('Completely different conclusion', identity='m2'))
            log.write(message('secret user prompt', role='user'))
            log.write(json.dumps(dict(type='event_msg', payload=dict(type='token_count', info=dict(total_token_usage=dict(total_tokens=9000))))) + '\n')
        third = self.collector.snapshot()['panes'][0]
        self.assertEqual(third['dialogue_text'], 'Initial result\nCompletely different conclusion')
        self.assertNotIn('secret', third['dialogue_text'])
        self.assertEqual(tracker.update([third], 2)[0]['dialogue_output']['total_chars'],
                         len('InitialresultCompletelydifferentconclusion'))
        self.assertTrue(third['dialogue_complete'])

    def test_dialogue_message_replays_and_full_body_revisions_do_not_duplicate(self):
        with self.path.open('a') as log:
            log.write(message('Hello', identity='m1'))
            log.write(message('Hello world', identity='m1'))
            log.write(message('Hello world', identity='m1'))
            log.write(message('Later answer', identity='m2'))
            log.write(message('Hello world', identity='m1'))
        row = self.collector.snapshot()['panes'][0]
        self.assertEqual(row['dialogue_text'], 'Hello world\nLater answer')
        with self.path.open('a') as log:
            log.write(event('task_started'))
            log.write(message('Next turn'))
        self.assertEqual(self.collector.snapshot()['panes'][0]['dialogue_text'], 'Next turn')

    def test_dialogue_identity_replacement_and_bounded_completeness(self):
        first = self.collector.snapshot()['panes'][0]
        replacement = self.home / 'replacement'
        replacement.write_text(self.path.read_text() + message('Replaced file'))
        replacement.replace(self.path)
        second = self.collector.snapshot()['panes'][0]
        self.assertNotEqual(second['dialogue_identity'], first['dialogue_identity'])
        with self.path.open('a') as log: log.write(message('x' * 20000))
        capped = self.collector.snapshot()['panes'][0]
        self.assertEqual(len(capped['dialogue_text']), 16384)
        self.assertFalse(capped['dialogue_complete'])
        with self.path.open('a') as log: log.write('{"incomplete":')
        self.assertFalse(self.collector.snapshot()['panes'][0]['dialogue_complete'])

    def test_errors_are_visible_and_new_turn_clears_them(self):
        with self.path.open('a') as log: log.write(event('error', message='503 Service Unavailable'))
        row = self.collector.snapshot()['panes'][0]
        self.assertEqual(row['state'], 'error')
        self.assertIn('503', row['error_summary'])
        with self.path.open('a') as log: log.write(event('task_started'))
        self.assertEqual(self.collector.snapshot()['panes'][0]['error_summary'], '')

    def test_child_sessions_are_counted_under_their_parent(self):
        child = self.home / 'rollout-child.jsonl'
        child.write_text(json.dumps(dict(type='session_meta', payload=dict(id='child', source=dict(subagent=dict(thread_spawn=dict(parent_thread_id='root-id')))))) + '\n' + event('task_started'))
        self.opened.append(child)
        rows = self.collector.snapshot()['panes']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['background_agents'], 1)
        with child.open('a') as log: log.write(event('task_complete'))
        self.assertEqual(self.collector.snapshot()['panes'][0]['background_agents'], 0)

    def test_missing_lifecycle_is_unknown_not_a_false_completion(self):
        self.path.write_text(json.dumps(dict(type='session_meta', payload=dict(id='root-id', source='cli'))) + '\n')
        self.assertEqual(self.collector.snapshot()['panes'][0]['state'], 'unknown')

    def test_long_active_turn_recovers_start_before_initial_tail(self):
        with self.path.open('a') as log:
            for _ in range(4):
                log.write(json.dumps(dict(type='response_item', payload=dict(type='message', role='assistant', content=[dict(type='output_text', text='x' * 1048576)]))) + '\n')
        self.assertEqual(self.collector.snapshot()['panes'][0]['state'], 'running')
        with self.path.open('a') as log: log.write(event('task_complete'))
        self.assertEqual(self.collector.snapshot()['panes'][0]['state'], 'idle')

    def test_titles_are_read_without_modifying_the_database(self):
        database = self.home / 'state_5.sqlite'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE threads (id TEXT, title TEXT)')
            db.execute('INSERT INTO threads VALUES (?, ?)', ('root-id', 'Local task'))
        before = database.read_bytes()
        self.assertEqual(self.collector.snapshot()['panes'][0]['session'], 'Local task')
        self.assertEqual(before, database.read_bytes())

    def test_sources_keep_separate_identity_and_freshness(self):
        local = FeedState(); local.accept(self.collector.snapshot(), [], 1, 0, 0)
        remote = FeedState(); remote.accept(dict(panes=[dict(local.model.rows[0], pane_id='%1', source='remote')]), [], 1, 0, 0)
        a = source_payload('local', local, dict(connected=True), [])
        b = source_payload('remote', remote, dict(connected=False), [])
        self.assertTrue(a['rows'][0]['available'])
        self.assertFalse(b['rows'][0]['available'])
        self.assertNotEqual(a['rows'][0]['pane_id'], b['rows'][0]['pane_id'])

    def test_resource_ownership_counts_sessions_before_discovery_limit_and_selection(self):
        second = self.home / 'rollout-second.jsonl'
        second.write_text(json.dumps(dict(type='session_meta', payload=dict(id='second-id', source='cli'))) + '\n' + event('task_complete'))
        self.opened.append(second)
        os.utime(self.path, (10, 10)); os.utime(second, (20, 20))
        self.collector.limit = 1
        rows = self.collector.snapshot(selected=['second-id'])['panes']
        self.assertIsInstance(self.collector.processes, ProcessSnapshot)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['thread_id'], 'second-id')
        self.assertEqual(rows[0]['resources']['scope'], 'shared_process')
        self.assertEqual(rows[0]['resources']['shared_sessions'], 2)
        self.assertEqual(rows[0]['resources']['attribution'], 'shared')
        self.assertEqual(rows[0]['resources']['attribution_source'], 'open_rollout_fd')

    def test_known_shared_app_server_is_never_presented_as_exclusive_session_usage(self):
        original = self.collector.runner
        def runner(args, **kwargs):
            if args[0] == 'ps':
                return subprocess.CompletedProcess(args, 0, stdout='10 1 S /bin/codex-app-server\n', stderr='')
            return original(args, **kwargs)
        self.collector.runner = runner
        row = self.collector.snapshot()['panes'][0]
        self.assertEqual(row['resources']['scope'], 'shared_process')
        self.assertEqual(row['resources']['attribution'], 'shared')
        self.assertEqual(row['resources']['shared_sessions'], 1)

    def test_multiple_processes_opening_the_same_log_do_not_choose_an_arbitrary_owner(self):
        def runner(args, **kwargs):
            output = ('10 1 S /bin/codex\n20 1 S /bin/codex\n' if args[0] == 'ps' else
                      'p10\nn' + str(self.path) + '\np20\nn' + str(self.path) + '\n')
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr='')
        self.collector.runner = runner
        row = self.collector.snapshot()['panes'][0]
        self.assertEqual(row['state'], 'running')
        self.assertIsNone(row['process_id'])
        self.assertIsNone(row['resources'])
