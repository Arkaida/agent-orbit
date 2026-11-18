import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from local_probe import LocalCollector
from desktop_bridge import FeedState, source_payload


def event(kind, **extra):
    return json.dumps(dict(type='event_msg', payload=dict(type=kind, **extra))) + '\n'


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

    def test_actual_turn_events_drive_running_and_completion(self):
        first = self.collector.snapshot()['panes'][0]
        self.assertEqual(first['state'], 'running')
        self.assertEqual(self.collector.snapshot()['panes'][0]['state'], 'running')
        with self.path.open('a') as log: log.write(event('task_complete'))
        row = self.collector.snapshot()['panes'][0]
        self.assertEqual((row['state'], row['source'], row['pane_id']), ('idle', 'local', 'local:root-id'))

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
