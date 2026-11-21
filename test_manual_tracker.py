import json
from pathlib import Path
import tempfile
import unittest

from desktop_bridge import FeedState, source_payload
from manual_tracker import ManualRunningTracker
from response_tracker import SessionResponseTracker
from remote_probe import ResultLogWatcher


def row(name='alpha', source='remote', state='idle', marker='old-final', identity='root-a', observable=True, **fields):
    return dict(session=name, source=source, pane_id='%' + name if source == 'remote' else 'local:' + name,
                group_id=source + ':' + name, state=state, status=state, preview=[], process_id=1,
                available=True, result_identity=identity, result_marker=marker,
                result_observable=observable, **fields)


def command(cid='one', target='remote:alpha', action='running', members=None):
    return dict(id=cid, action=action, target=target, members=members or [target])


class ManualTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'manual-overrides.json'
        self.tracker = ManualRunningTracker(self.path)

    def write(self, *commands):
        self.path.write_text(json.dumps(dict(version=1, commands=list(commands))))

    def mark(self, rows=None, value=None):
        self.write(value or command())
        self.assertTrue(self.tracker.poll(rows or [row()]))

    def test_startup_commands_are_consumed_without_replay_and_ids_are_once_only(self):
        self.write(command())
        tracker = ManualRunningTracker(self.path)
        self.assertFalse(tracker.poll([row()]))
        self.assertFalse(tracker.active)
        self.write(command(), command('two'))
        self.assertTrue(tracker.poll([row()]))
        self.assertEqual(tracker.active['remote:alpha']['id'], 'two')
        self.write(command())
        tracker.poll([row()])
        self.assertEqual(tracker.active['remote:alpha']['id'], 'two')

    def test_new_result_clears_but_raw_idle_heartbeats_tokens_and_same_result_do_not(self):
        self.mark()
        for changes in ({}, dict(output_total=999), dict(fingerprint='changed'), dict(telemetry={'output_tokens': 999})):
            sample = row(**changes)
            self.tracker.observe('remote', [sample])
            marked = self.tracker.apply('remote', [sample])[0]
            self.assertTrue(marked['manual_running'])
            self.assertEqual(marked['state'], 'running')
            self.assertEqual(marked['manual_override_id'], 'one')
        sample = row(marker='new-final')
        self.assertTrue(self.tracker.observe('remote', [sample]))
        decorated = self.tracker.apply('remote', [sample])[0]
        self.assertFalse(decorated['manual_running'])
        self.assertEqual(decorated['state'], 'idle')

    def test_clear_never_forces_real_running_idle(self):
        sample = row(state='running')
        self.mark([sample])
        self.write(command('two', action='clear'))
        self.assertTrue(self.tracker.poll([sample]))
        result = self.tracker.apply('remote', [sample])[0]
        self.assertFalse(result['manual_running'])
        self.assertEqual(result['state'], 'running')
        self.mark([sample], command('three'))
        sample = row(state='running', marker='new-final')
        self.tracker.observe('remote', [sample])
        self.assertEqual(self.tracker.apply('remote', [sample])[0]['state'], 'running')

    def test_cancel_is_applied_to_next_real_frame_without_mutating_cached_or_raw_state(self):
        feed = FeedState()
        raw = row()
        def transform(rows):
            self.tracker.observe('remote', rows)
            return self.tracker.apply('remote', rows)
        feed.accept(dict(panes=[raw]), ['alpha'], 1, 0, 1000, transform=transform)
        self.mark([raw])
        for now in (0.5, 1, 1.5):
            feed.accept(dict(panes=[raw]), ['alpha'], 1, now, 1000 + now, transform=transform)
            self.assertEqual(feed.rows[0]['state'], 'running')
            self.assertEqual(feed.model.rows[0]['state'], 'idle')
        self.write(command('cancel', action='clear'))
        self.assertTrue(self.tracker.poll([raw]))
        # Command consumption is local; it neither invents a collector frame nor
        # rewrites the agent state. The next genuine sample applies cancellation.
        self.assertEqual(feed.last_sample, 1.5)
        self.assertEqual(feed.rows[0]['state'], 'running')
        feed.accept(dict(panes=[raw]), ['alpha'], 1, 2, 1002, transform=transform)
        self.assertEqual(feed.rows[0]['state'], 'idle')
        self.assertFalse(feed.rows[0]['manual_running'])
        self.assertEqual(raw['state'], 'idle')
        self.assertNotIn('manual_running', raw)

    def test_error_approval_offline_and_warning_take_priority(self):
        self.mark()
        for state in ('error', 'interrupted', 'stopped', 'missing', 'approval', 'input', 'paused', 'history'):
            sample = row(state=state)
            result = self.tracker.apply('remote', [sample])[0]
            self.assertEqual(result['state'], state)
            self.assertTrue(result['manual_running'])
        sample = row(attention_level='warning')
        self.assertEqual(self.tracker.apply('remote', [sample])[0]['state'], 'idle')
        sample = row(); sample['available'] = False
        self.assertEqual(self.tracker.apply('remote', [sample])[0]['state'], 'idle')
        self.assertFalse(self.tracker.apply('remote', [sample])[0]['available'])

    def test_missing_or_changed_identity_builds_baseline_without_false_completion(self):
        self.mark([row(observable=False, marker=None)])
        self.assertFalse(self.tracker.observe('remote', [row(marker='historical-result')]))
        self.assertTrue(self.tracker.active)
        self.assertFalse(self.tracker.observe('remote', [row(identity='replacement-root', marker='another-history')]))
        self.assertTrue(self.tracker.active)
        self.assertTrue(self.tracker.observe('remote', [row(identity='replacement-root', marker='genuine-next-result')]))

    def test_proven_root_without_prior_result_finishes_on_first_result(self):
        self.mark([row(marker=None)])
        self.assertTrue(self.tracker.observe('remote', [row(marker='first-final')]))
        self.assertFalse(self.tracker.active)

    def test_group_only_forces_one_member_and_any_member_result_clears(self):
        settings = dict(session_groups=[dict(id='pair', members=['remote:alpha', 'local:beta'])])
        self.tracker.configure(settings)
        rows = [row(), row('beta', 'local', identity='root-b')]
        self.mark(rows, command(target='group:pair', members=['remote:alpha', 'local:beta']))
        remote = self.tracker.apply('remote', [rows[0]])
        local = self.tracker.apply('local', [rows[1]])
        self.assertEqual(sum(item['state'] == 'running' for item in remote + local), 1)
        self.assertTrue(all(item['manual_running'] for item in remote + local))
        self.assertTrue(self.tracker.observe('local', [row('beta', 'local', identity='root-b', marker='new-final')]))
        self.assertFalse(self.tracker.apply('remote', [rows[0]])[0]['manual_running'])

    def test_group_actual_work_uses_natural_member_without_inventing_parallel_work(self):
        self.tracker.configure(dict(session_groups=[dict(id='pair', members=['remote:alpha', 'local:beta'])]))
        rows = [row(), row('beta', 'local', state='running', identity='root-b')]
        self.mark(rows, command(target='group:pair', members=['remote:alpha', 'local:beta']))
        self.assertEqual(self.tracker.apply('remote', [rows[0]])[0]['state'], 'idle')
        self.assertEqual(self.tracker.apply('local', [rows[1]])[0]['state'], 'running')

    def test_unknown_targets_spoofed_members_invalid_files_and_removed_groups_are_rejected(self):
        self.write(command(target='remote:missing'))
        self.assertFalse(self.tracker.poll([row()]))
        self.write(command('two', members=['remote:beta']))
        self.assertFalse(self.tracker.poll([row()]))
        self.path.write_text('{partial')
        self.assertFalse(self.tracker.poll([row()]))
        self.path.write_text(json.dumps(dict(version=2, commands=[command('three')])))
        self.assertFalse(self.tracker.poll([row()]))
        self.tracker.configure(dict(session_groups=[dict(id='pair', members=['remote:alpha', 'local:beta'])]))
        self.mark([row()], command('four', target='group:pair', members=['remote:alpha', 'local:beta']))
        self.tracker.configure({})
        self.assertFalse(self.tracker.active)

    def test_override_precedes_completion_activity_and_response_accounting(self):
        feed = FeedState()
        responses = SessionResponseTracker()
        def accept(sample, at):
            def transform(rows):
                self.tracker.observe('remote', rows)
                return self.tracker.apply('remote', rows)
            feed.accept(dict(panes=[sample]), ['alpha'], 1, at, 1000 + at, transform=transform)
            rows = source_payload('remote', feed, dict(connected=True), ['alpha'])['rows']
            responses.observe('remote', rows, at, 1000 + at)
            return responses.annotate(rows, at, dict(connected=True))[0]
        accept(row(), 0)
        self.mark()
        self.assertEqual(accept(row(), 1)['response']['kind'], 'running')
        self.assertEqual(accept(row(), 2)['response']['run_seconds'], 1)
        self.assertFalse(feed.events)
        waiting = accept(row(marker='new-final'), 3)
        self.assertEqual(waiting['response']['kind'], 'waiting')
        self.assertFalse(waiting['manual_running'])
        self.assertEqual(waiting['response']['run_seconds'], 2)
        accept(row(marker='new-final'), 4.1)
        self.assertEqual(len(feed.events), 1)

    def test_real_codex_and_claude_markers_clear_only_after_a_new_root_result(self):
        for agent in ('codex', 'claude'):
            with self.subTest(agent=agent):
                path = Path(self.temp.name) / (agent + '.jsonl')
                metadata = (dict(type='session_meta', payload=dict(id='synthetic-root', source='cli'))
                            if agent == 'codex' else dict(type='user', message=dict(content='Synthetic request')))
                final = (dict(type='event_msg', payload=dict(type='turn_complete')) if agent == 'codex' else
                         dict(type='assistant', message=dict(id='old-message', stop_reason='end_turn',
                                                           content=[dict(type='text', text='Synthetic final')])) )
                path.write_text(json.dumps(metadata) + '\n' + json.dumps(final) + '\n')
                watcher = ResultLogWatcher()
                initial = row()
                initial.update(watcher.sample(path, agent))
                tracker = ManualRunningTracker(Path(self.temp.name) / (agent + '-commands.json'))
                tracker.known = [initial]
                self.assertTrue(tracker.command(command()))
                repeated = dict(initial, **ResultLogWatcher().sample(path, agent))
                self.assertFalse(tracker.observe('remote', [repeated]))
                self.assertTrue(tracker.active)
                interim = (dict(type='event_msg', payload=dict(type='token_count', info={})) if agent == 'codex' else
                           dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='thinking', thinking='synthetic')])) )
                with path.open('a') as log:
                    log.write(json.dumps(interim) + '\n')
                self.assertFalse(tracker.observe('remote', [dict(initial, **watcher.sample(path, agent))]))
                self.assertTrue(tracker.active)
                if agent == 'codex':
                    start = dict(type='event_msg', payload=dict(type='turn_started'))
                else:
                    start = dict(type='user', message=dict(content='Synthetic next request'))
                    final['message']['id'] = 'new-message'
                with path.open('a') as log:
                    log.write(json.dumps(start) + '\n' + json.dumps(final) + '\n')
                self.assertTrue(tracker.observe('remote', [dict(initial, **watcher.sample(path, agent))]))
                self.assertFalse(tracker.active)


if __name__ == '__main__':
    unittest.main()
