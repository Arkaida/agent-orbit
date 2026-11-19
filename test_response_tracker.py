import json
import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from configuration import defaults
from desktop_bridge import FeedState, run, source_payload
from response_tracker import SessionResponseTracker


def row(state='idle', group='remote:alpha', source='remote', pane='%1', pid=42, **values):
    result = dict(pane_id=pane, session='alpha', group_id=group, source=source,
                  process_id=pid, state=state, available=True, preview=[])
    result.update(values)
    return result


class ResponseTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = SessionResponseTracker()
        self.health = [dict(id='remote', connected=True)]

    def sample(self, rows, mono=0, wall=None, epoch=1, settings=None, gap=2, selection=None):
        self.tracker.observe('remote', rows, mono, 1000 + mono if wall is None else wall,
                             epoch, max_gap_seconds=gap, selection=selection)
        return self.tracker.annotate(rows, mono, self.health, settings)

    def age_waiting(self, seconds=60, state='idle', **values):
        rows = [row(state, **values)]
        result = self.sample(rows, 0)
        for second in range(1, seconds + 1):
            result = self.sample(rows, second, settings=dict(wait_reminder_minutes=1))
        return result[0]['response']

    def test_initial_idle_starts_from_observation_not_old_completion(self):
        original = row(completion_id='historical', completed_at=1)
        response = self.sample([original])[0]['response']
        self.assertEqual(response['kind'], 'waiting')
        self.assertEqual(response['since'], 1000)
        self.assertEqual(response['elapsed_seconds'], 0)
        self.assertTrue(response['observed'])
        self.assertFalse(response['overdue'])
        self.assertIsNotNone(response['episode_id'])
        self.assertNotIn('response', original)

    def test_display_refresh_only_ages_same_episode_and_does_not_infer_state(self):
        rows = [row()]
        first = self.sample(rows)[0]['response']
        for tenth in range(1, 10):
            aged = self.tracker.annotate(rows, tenth / 10, self.health)[0]['response']
            self.assertEqual(aged['episode_id'], first['episode_id'])
            self.assertEqual(aged['since'], 1000)
            self.assertEqual(aged['elapsed_seconds'], tenth / 10)
        self.sample([row('running')], 1)
        # A changed cached row without observe is not a new collector event.
        cached = self.tracker.annotate([row('idle')], 1.1, self.health)[0]['response']
        self.assertEqual(cached['kind'], 'running')
        self.assertIsNone(cached['episode_id'])

    def test_pending_idle_is_upgraded_by_matching_confirmed_completion(self):
        self.sample([row('running', completion_id='old', completed_at=900)], 0)
        pending = self.sample([row(completion_id='old', completed_at=900)], 1)[0]['response']
        self.assertTrue(pending['observed'])
        confirmed = self.sample([row(completion_id='new', completed_at=1001)], 2)[0]['response']
        self.assertFalse(confirmed['observed'])
        self.assertEqual(confirmed['episode_id'], pending['episode_id'])
        self.assertEqual(confirmed['since'], 1001)
        self.assertEqual(confirmed['elapsed_seconds'], 1)

    def test_direct_confirmed_idle_and_old_or_remote_clock_timestamps(self):
        self.sample([row('running')], 0)
        complete = self.sample([row(completion_id='fresh', completed_at=1000.5)], 1)[0]['response']
        self.assertFalse(complete['observed'])
        self.assertEqual(complete['elapsed_seconds'], .5)
        self.sample([row('running', completion_id='fresh', completed_at=1000.5)], 2)
        old = self.sample([row(completion_id='fresh', completed_at=1000.5)], 3)[0]['response']
        self.assertTrue(old['observed'])
        self.sample([row('running')], 4)
        skewed = self.sample([row(completion_id='future', completed_at=999999)], 5)[0]['response']
        self.assertTrue(skewed['observed'])
        self.assertEqual(skewed['since'], 1005)

    def test_answering_clears_old_episode_and_next_wait_creates_a_new_one(self):
        waiting = self.sample([row()], 0)[0]['response']
        running = self.sample([row('running')], 1)[0]['response']
        self.assertEqual(running['kind'], 'running')
        self.assertIsNone(running['episode_id'])
        self.assertEqual(running['elapsed_seconds'], 0)
        self.assertEqual(running['since'], 1001)
        self.assertFalse(running['overdue'])
        next_wait = self.sample([row()], 2)[0]['response']
        self.assertNotEqual(next_wait['episode_id'], waiting['episode_id'])
        self.assertEqual(next_wait['elapsed_seconds'], 0)

    def test_wait_threshold_and_disabled_reminders(self):
        due = self.age_waiting()
        self.assertTrue(due['overdue'])
        self.assertEqual(due['elapsed_seconds'], 60)
        disabled = self.tracker.annotate([row()], 60.1, self.health,
                                         dict(wait_reminder_enabled=False, wait_reminder_minutes=1))[0]['response']
        self.assertFalse(disabled['overdue'])
        self.assertEqual(disabled['episode_id'], due['episode_id'])

    def test_approval_input_paused_remind_but_error_unknown_never_do(self):
        for state in ('approval', 'input', 'paused'):
            self.setUp()
            due = self.age_waiting(state=state)
            self.assertEqual(due['kind'], 'attention')
            self.assertTrue(due['overdue'])
        for state in ('unknown', 'error', 'interrupted', 'stopped', 'missing'):
            self.setUp()
            response = self.age_waiting(state=state)
            self.assertEqual(response['kind'], 'unknown')
            self.assertFalse(response['overdue'])
            self.assertIsNone(response['episode_id'])
            self.assertIsNone(response['since'])
        warning_unknown = self.sample([row('unknown', attention_level='warning')], 61)[0]['response']
        self.assertEqual(warning_unknown['kind'], 'unknown')

    def test_error_transition_clears_previously_overdue_episode(self):
        self.age_waiting()
        error = self.sample([row('error')], 61)[0]['response']
        self.assertFalse(error['overdue'])
        self.assertIsNone(error['episode_id'])
        new_wait = self.sample([row()], 62)[0]['response']
        self.assertTrue(new_wait['observed'])
        self.assertEqual(new_wait['elapsed_seconds'], 0)

    def test_offline_time_is_excluded_even_when_caller_reuses_epoch(self):
        first = self.sample([row()], 0)[0]['response']
        offline = self.tracker.annotate([row()], 1, [dict(id='remote', connected=False)])[0]['response']
        self.assertEqual(offline['kind'], 'offline')
        self.assertIsNone(offline['episode_id'])
        self.assertIsNone(offline['elapsed_seconds'])
        restored = self.sample([row(completion_id='old', completed_at=1000)], 100)[0]['response']
        self.assertNotEqual(restored['episode_id'], first['episode_id'])
        self.assertEqual(restored['since'], 1100)
        self.assertEqual(restored['elapsed_seconds'], 0)
        self.assertTrue(restored['observed'])

    def test_stale_source_masks_waiting_and_recovery_reobserves(self):
        initial = self.sample([row()], 0)[0]['response']
        stale = self.tracker.annotate([row()], 3, self.health)[0]['response']
        self.assertEqual(stale['kind'], 'offline')
        recovered = self.sample([row()], 3.1)[0]['response']
        self.assertNotEqual(recovered['episode_id'], initial['episode_id'])
        self.assertEqual(recovered['elapsed_seconds'], 0)

    def test_selection_epoch_and_process_change_reset_observation(self):
        original = self.sample([row()], 0)[0]['response']
        selected = self.sample([row()], 1, epoch=2)[0]['response']
        self.assertNotEqual(original['episode_id'], selected['episode_id'])
        replacement = self.sample([row(pid=99)], 2, epoch=2)[0]['response']
        self.assertNotEqual(selected['episode_id'], replacement['episode_id'])
        self.assertTrue(replacement['observed'])
        self.assertEqual(replacement['elapsed_seconds'], 0)

    def test_duplicate_panes_share_one_episode_and_do_not_reset_same_process(self):
        single = self.sample([row()], 0)[0]['response']
        panes = [row(), row(pane='%2')]
        result = self.sample(panes, 1)
        self.assertEqual(result[0]['response'], result[1]['response'])
        self.assertEqual(result[0]['response']['episode_id'], single['episode_id'])
        self.assertEqual(result[0]['response']['elapsed_seconds'], 1)

    def test_group_priority_matches_visual_states(self):
        for second, expected in (('running', 'running'), ('unknown', 'unknown'),
                                 ('approval', 'attention'), ('error', 'unknown')):
            self.setUp()
            result = self.sample([row(), row(second, pane='%2')], 0)
            self.assertEqual(result[0]['response'], result[1]['response'])
            self.assertEqual(result[0]['response']['kind'], expected)
        self.setUp()
        mixed = self.sample([row(), row(pane='%2', available=False)], 0)
        self.assertEqual(mixed[0]['response'], mixed[1]['response'])
        self.assertEqual(mixed[0]['response']['kind'], 'waiting')

    def test_partial_sources_keep_live_group_and_mask_only_offline_source(self):
        remote = row()
        local = row(source='local', group='local:alpha')
        self.tracker.observe('remote', [remote], 0, 1000)
        self.tracker.observe('local', [local], 0, 1000)
        health = [dict(id='remote', connected=False), dict(id='local', connected=True)]
        result = self.tracker.annotate([remote, local], 1, health)
        self.assertEqual(result[0]['response']['kind'], 'offline')
        self.assertEqual(result[1]['response']['kind'], 'waiting')
        self.assertEqual(result[1]['response']['elapsed_seconds'], 1)

    def test_wall_clock_jump_does_not_change_wait_age_or_confirmation(self):
        self.sample([row('running')], 0, wall=1000)
        pending = self.sample([row()], 1, wall=1001)[0]['response']
        jumped = self.sample([row(completion_id='new', completed_at=1001)], 2, wall=-9999)[0]['response']
        self.assertEqual(jumped['since'], 1001)
        self.assertEqual(jumped['elapsed_seconds'], 1)
        self.assertFalse(jumped['observed'])
        self.assertEqual(jumped['episode_id'], pending['episode_id'])
        backwards = self.sample([row()], .5, wall=2000)[0]['response']
        self.assertTrue(backwards['observed'])
        self.assertEqual(backwards['elapsed_seconds'], 0)

    def test_slow_configured_sampling_and_settings_are_json_safe(self):
        self.sample([row()], 0, gap=180)
        result = self.sample([row()], 60, gap=180,
                             settings=dict(wait_reminder_minutes=1))[0]['response']
        self.assertTrue(result['overdue'])
        self.assertEqual(result['elapsed_seconds'], 60)
        for minutes in (float('nan'), -1, True, 121, 'one'):
            response = self.tracker.annotate([row()], 60, self.health,
                                             dict(wait_reminder_minutes=minutes))[0]['response']
            self.assertFalse(response['overdue'])  # Invalid values use default five minutes.
            json.dumps(response, allow_nan=False)

    def test_changed_awaiting_kind_starts_new_reminder_episode(self):
        old = self.age_waiting()
        question = self.sample([row('approval')], 61)[0]['response']
        self.assertNotEqual(question['episode_id'], old['episode_id'])
        self.assertEqual(question['elapsed_seconds'], 0)
        self.assertFalse(question['overdue'])

    def test_unselected_groups_are_pruned(self):
        self.sample([row()], 0)
        self.assertTrue(self.tracker.groups)
        self.tracker.annotate([], 1, self.health)
        self.assertEqual(self.tracker.groups, {})

    def test_source_payload_drops_deselected_chat_before_next_frame(self):
        feed = FeedState()
        feed.accept(dict(panes=[row()]), ['alpha'], 1, 0, 1000)
        self.assertEqual(len(source_payload('remote', feed, dict(connected=True), ['alpha'])['rows']), 1)
        self.assertEqual(source_payload('remote', feed, dict(connected=True), ['beta'])['rows'], [])

    def test_feed_completion_matches_response_episode_after_debounce(self):
        feed = FeedState()
        responses = []
        for mono, state in ((0, 'running'), (1, 'idle'), (2, 'idle')):
            feed.accept(dict(panes=[row(state)]), ['alpha'], 1, mono, 1000 + mono)
            rows = source_payload('remote', feed, dict(connected=True), ['alpha'])['rows']
            self.tracker.observe('remote', rows, mono, 1000 + mono, epoch=1)
            responses.append(self.tracker.annotate(rows, mono, self.health)[0]['response'])
        self.assertEqual(responses[0]['kind'], 'running')
        self.assertTrue(responses[1]['observed'])
        self.assertFalse(responses[2]['observed'])
        self.assertEqual(responses[1]['episode_id'], responses[2]['episode_id'])
        self.assertEqual(responses[2]['since'], 1001)

    def test_running_duration_is_continuous_monotonic_observation_without_reminders(self):
        first = self.sample([row('running')], 0)[0]['response']
        self.assertTrue(first['observed'])
        self.assertIsNone(first['episode_id'])
        continuous = self.sample([row('running'), row('running', pane='%2')], 60,
                                 wall=-9999, gap=180,
                                 settings=dict(wait_reminder_minutes=1))[0]['response']
        self.assertEqual(continuous['since'], 1000)
        self.assertEqual(continuous['elapsed_seconds'], 60)
        self.assertFalse(continuous['overdue'])
        self.assertIsNone(continuous['episode_id'])
        refreshed = self.tracker.annotate([row('running')], 60.5, self.health)[0]['response']
        self.assertEqual(refreshed['elapsed_seconds'], 60.5)

    def test_running_restarts_after_unknown_epoch_process_change_and_outage(self):
        self.sample([row('running')], 0)
        self.sample([row('unknown')], 1)
        restored = self.sample([row('running')], 2)[0]['response']
        self.assertEqual(restored['since'], 1002)
        self.assertEqual(restored['elapsed_seconds'], 0)
        changed_epoch = self.sample([row('running')], 3, epoch=2)[0]['response']
        self.assertEqual(changed_epoch['elapsed_seconds'], 0)
        changed_process = self.sample([row('running', pid=99)], 4, epoch=2)[0]['response']
        self.assertEqual(changed_process['elapsed_seconds'], 0)
        self.tracker.annotate([row('running', pid=99)], 4.5, [dict(id='remote', connected=False)])
        reconnected = self.sample([row('running', pid=99)], 5, epoch=2)[0]['response']
        self.assertEqual(reconnected['since'], 1005)
        self.assertEqual(reconnected['elapsed_seconds'], 0)

    def test_running_log_identity_replacement_restarts_observation(self):
        self.sample([row('running', telemetry=dict(identity='first-log'))], 0)
        changed = self.sample([row('running', telemetry=dict(identity='second-log'))], 1)[0]['response']
        self.assertEqual(changed['since'], 1001)
        self.assertEqual(changed['elapsed_seconds'], 0)

    def test_retrying_warning_and_system_errors_never_count_as_human_idle(self):
        for kind in ('retrying', 'fatal', 'network', 'quota', 'safety'):
            self.setUp()
            value = row('running', error_kind=kind, attention_level='warning')
            self.sample([value], 0)
            response = self.sample([value], 60, gap=180,
                                   settings=dict(wait_reminder_minutes=1))[0]['response']
            self.assertEqual(response['kind'], 'unknown')
            self.assertFalse(response['overdue'])
            self.assertIsNone(response['episode_id'])
            self.assertEqual((response['idle_seconds'], response['run_seconds']), (0, 0))
        self.setUp()
        value = row('running', error_kind='question', attention_level='warning')
        self.sample([value], 0)
        response = self.sample([value], 1)[0]['response']
        self.assertEqual(response['kind'], 'running')
        self.assertEqual(response['run_seconds'], 1)
        self.assertEqual(response['idle_seconds'], 0)

    def test_cumulative_idle_run_integrates_once_and_projects_without_mutation(self):
        self.sample([row()], 0)
        self.sample([row()], 2)
        self.sample([row('running')], 3)
        self.sample([row('running')], 5)
        self.sample([row()], 6)
        result = self.sample([row()], 8)[0]['response']
        self.assertEqual((result['idle_seconds'], result['run_seconds']), (5, 3))
        self.assertEqual(result['idle_run_ratio'], 1.667)
        self.assertEqual(result['efficiency_percent'], 37.5)
        self.assertEqual(result['scope'], 'observed_monitoring')
        for tenth in range(1, 10):
            projected = self.tracker.annotate([row()], 8 + tenth / 10, self.health)[0]['response']
            self.assertEqual(projected['idle_seconds'], 5 + tenth / 10)
        stored = self.tracker.groups[('remote', 'remote:alpha')]
        self.assertEqual((stored['idle_seconds'], stored['run_seconds']), (5, 3))

    def test_unknown_freezes_integral_and_empty_run_ratio_is_null(self):
        self.sample([row()], 0)
        idle = self.sample([row()], 2)[0]['response']
        self.assertIsNone(idle['idle_run_ratio'])
        self.assertEqual(idle['efficiency_percent'], 0)
        unknown = self.sample([row('unknown')], 3)[0]['response']
        self.assertEqual((unknown['idle_seconds'], unknown['run_seconds']), (2, 0))
        still_unknown = self.sample([row('unknown')], 5)[0]['response']
        self.assertEqual(still_unknown['idle_seconds'], 2)
        self.sample([row('running')], 6)
        running = self.sample([row('running')], 8)[0]['response']
        self.assertEqual((running['idle_seconds'], running['run_seconds']), (2, 2))
        self.assertEqual(running['efficiency_percent'], 50)

    def test_reconnect_preserves_totals_without_backfill_but_new_scope_resets(self):
        self.sample([row()], 0, selection=('server', ('alpha',)))
        self.sample([row()], 2, selection=('server', ('alpha',)))
        self.tracker.annotate([row()], 3, [dict(id='remote', connected=False)])
        resumed = self.sample([row()], 100, epoch=2, selection=('server', ('alpha',)))[0]['response']
        self.assertEqual(resumed['idle_seconds'], 2)
        self.assertEqual(resumed['elapsed_seconds'], 0)
        continued = self.sample([row()], 102, epoch=2, selection=('server', ('alpha',)))[0]['response']
        self.assertEqual(continued['idle_seconds'], 4)
        changed = self.sample([row()], 103, epoch=3, selection=('server', ('alpha', 'beta')))[0]['response']
        self.assertEqual((changed['idle_seconds'], changed['run_seconds']), (0, 0))
        self.sample([row('running')], 104, epoch=3, selection=('server', ('alpha', 'beta')))
        changed_pid = self.sample([row('running', pid=99)], 105, epoch=3,
                                  selection=('server', ('alpha', 'beta')))[0]['response']
        self.assertEqual((changed_pid['idle_seconds'], changed_pid['run_seconds']), (0, 0))


class GroupedResponseTests(unittest.TestCase):
    def setUp(self):
        self.tracker = SessionResponseTracker()
        self.settings = dict(wait_reminder_minutes=1, session_groups=[
            dict(id='pair', name='PAIR', members=['remote:alpha', 'remote:beta'])])
        self.tracker.configure(self.settings)
        self.rows = {}
        self.health = [dict(id='remote', connected=True), dict(id='local', connected=True)]

    def value(self, name, state='idle', source='remote', pane=None, **values):
        return row(state, group=source + ':' + name, source=source,
                   pane=pane or '%' + name, pid={'alpha': 1, 'beta': 2, 'gamma': 3}[name], **values)

    def sample(self, rows, now, source='remote', epoch=1, health=None, gap=2, selection='same'):
        self.rows[source] = rows
        self.tracker.observe(source, rows, now, 1000 + now, epoch,
                             max_gap_seconds=gap, selection=selection)
        return self.tracker.annotate([value for values in self.rows.values() for value in values],
                                     now, self.health if health is None else health, self.settings)

    def test_one_running_one_waiting_never_nags_waiting_member(self):
        rows = [self.value('alpha', 'running'), self.value('beta')]
        for second in range(61):
            result = self.sample(rows, second)
        group = result[0]['response_group']
        self.assertEqual(group['kind'], 'running')
        self.assertEqual((group['idle_seconds'], group['run_seconds']), (0, 60))
        self.assertEqual(group['running_members'], 1)
        self.assertFalse(group['overdue'])
        self.assertIsNone(group['episode_id'])
        self.assertTrue(result[1]['response']['reminder_suppressed'])
        self.assertFalse(result[1]['response']['overdue'])
        self.assertEqual(result[1]['response']['idle_seconds'], 60)

    def test_all_waiting_share_one_episode_and_union_idle_not_member_sum(self):
        rows = [self.value('alpha'), self.value('beta', 'approval')]
        for second in range(61):
            result = self.sample(rows, second)
        first, second = (value['response_group'] for value in result)
        self.assertEqual(first, second)
        self.assertEqual(first['kind'], 'attention')
        self.assertEqual((first['count'], first['known_members'], first['running_members']), (2, 2, 0))
        self.assertEqual((first['idle_seconds'], first['run_seconds']), (60, 0))
        self.assertTrue(first['overdue'])
        self.assertIsNotNone(first['episode_id'])
        self.assertTrue(all(not value['response']['overdue'] for value in result))

    def test_two_running_flag_conflict_without_double_count_or_control(self):
        rows = [self.value('alpha', 'running'), self.value('beta', 'running')]
        self.sample(rows, 0)
        result = self.sample(rows, 2)
        group = result[0]['response_group']
        self.assertEqual(group['running_members'], 2)
        self.assertTrue(group['parallel_conflict'])
        self.assertEqual(group['run_seconds'], 2)
        self.assertTrue(all(value['state'] == 'running' for value in result))

    def test_missing_configured_member_is_not_silently_omitted_from_denominator(self):
        rows = [self.value('alpha')]
        self.sample(rows, 0)
        result = self.sample(rows, 2)[0]['response_group']
        self.assertEqual((result['count'], result['known_members']), (2, 1))
        self.assertEqual(result['kind'], 'offline')
        self.assertEqual(result['idle_seconds'], 0)
        self.assertIsNone(result['episode_id'])
        self.assertFalse(result['overdue'])

    def test_unknown_critical_or_offline_member_freezes_joint_waiting(self):
        for state, values in (('unknown', {}), ('missing', {}), ('error', {}),
                              ('idle', dict(available=False)),
                              ('running', dict(error_kind='retrying', attention_level='warning'))):
            self.setUp()
            rows = [self.value('alpha'), self.value('beta')]
            self.sample(rows, 0)
            self.sample(rows, 2)
            changed = [self.value('alpha'), self.value('beta', state, **values)]
            group = self.sample(changed, 3)[0]['response_group']
            self.assertIn(group['kind'], ('unknown', 'offline'))
            self.assertEqual(group['idle_seconds'], 2)
            self.assertFalse(group['overdue'])
            self.assertIsNone(group['episode_id'])

    def test_any_confirmed_running_suppresses_reminders_even_with_other_member_offline(self):
        rows = [self.value('alpha', 'running')]
        self.sample(rows, 0)
        group = self.sample(rows, 2)[0]['response_group']
        self.assertEqual(group['kind'], 'running')
        self.assertEqual((group['known_members'], group['count']), (1, 2))
        self.assertEqual(group['run_seconds'], 2)
        self.assertFalse(group['overdue'])

    def test_disabling_source_blocks_queued_cross_source_samples_until_fresh_reenable(self):
        for local_state in ('idle', 'running'):
            with self.subTest(local_state=local_state):
                self.setUp()
                self.settings.update(remote_enabled=True, local_enabled=True)
                self.settings['session_groups'][0]['members'] = ['remote:alpha', 'local:beta']
                self.tracker.configure(self.settings)
                remote = [self.value('alpha')]
                local = [self.value('beta', local_state, source='local')]
                self.sample(remote, 0)
                self.sample(local, .1, source='local')
                self.sample(remote, .5)
                before = self.sample(local, .6, source='local')[0]['response_group']
                field = 'idle_seconds' if local_state == 'idle' else 'run_seconds'
                self.assertEqual(before[field], .5)

                self.settings['local_enabled'] = False
                self.tracker.configure(self.settings)
                self.rows.pop('local')
                # The bridge polls queued remote frames after applying settings,
                # before annotate gets an opportunity to prune the local cache.
                remote_health = [dict(id='remote', connected=True)]
                disabled = self.sample(remote, 1, health=remote_health)[0]['response_group']
                self.assertEqual(disabled['kind'], 'offline')
                self.assertEqual(disabled[field], before[field])
                self.assertIsNone(disabled['episode_id'])
                self.assertFalse(disabled['overdue'])

                # Enabling a source alone must not revive its old observation.
                self.settings['local_enabled'] = True
                self.tracker.configure(self.settings)
                no_sample = self.sample(remote, 1.2)[0]['response_group']
                self.assertEqual(no_sample['kind'], 'offline')
                self.assertEqual(no_sample[field], before[field])
                resumed = self.sample(local, 1.3, source='local')[0]['response_group']
                self.assertEqual(resumed[field], before[field])
                self.assertEqual(resumed['elapsed_seconds'], 0)
                if local_state == 'idle':
                    self.assertNotEqual(resumed['episode_id'], before['episode_id'])
                continued = self.sample(remote, 1.5)[0]['response_group']
                self.assertEqual(continued[field], .7)

    def test_cross_source_union_integrates_async_frames_once_and_refresh_is_readonly(self):
        self.settings['session_groups'][0]['members'] = ['remote:alpha', 'local:beta']
        self.tracker.configure(self.settings)
        self.sample([self.value('alpha', 'running')], 0)
        self.sample([self.value('beta', source='local')], .5, source='local')
        self.sample([self.value('alpha', 'running')], 1)
        self.sample([self.value('beta', source='local')], 1.5, source='local')
        result = self.sample([self.value('alpha', 'running')], 2)
        self.assertEqual(result[0]['response_group']['run_seconds'], 2)
        all_rows = [value for values in self.rows.values() for value in values]
        for tenth in range(1, 5):
            projected = self.tracker.annotate(all_rows, 2 + tenth / 10, self.health, self.settings)
            self.assertEqual(projected[0]['response_group']['run_seconds'], 2 + tenth / 10)
        self.assertEqual(self.tracker.response_groups['pair']['run_seconds'], 2)

    def test_group_reconnect_preserves_total_without_counting_disconnected_gap(self):
        rows = [self.value('alpha'), self.value('beta')]
        self.sample(rows, 0)
        before = self.sample(rows, 2)[0]['response_group']
        self.tracker.annotate(rows, 3, [dict(id='remote', connected=False)], self.settings)
        after = self.sample(rows, 100, epoch=2)[0]['response_group']
        self.assertEqual(after['idle_seconds'], 2)
        self.assertEqual(after['elapsed_seconds'], 0)
        self.assertNotEqual(before['episode_id'], after['episode_id'])
        self.assertTrue(after['observed'])
        continued = self.sample(rows, 102, epoch=2)[0]['response_group']
        self.assertEqual(continued['idle_seconds'], 4)

    def test_running_to_all_waiting_starts_joint_episode_then_resumption_clears_it(self):
        running = [self.value('alpha', 'running'), self.value('beta')]
        self.sample(running, 0)
        self.sample(running, 2)
        waiting = [self.value('alpha'), self.value('beta')]
        joint = self.sample(waiting, 3)[0]['response_group']
        self.assertEqual(joint['kind'], 'waiting')
        self.assertEqual(joint['run_seconds'], 3)
        self.assertEqual(joint['elapsed_seconds'], 0)
        self.assertIsNotNone(joint['episode_id'])
        resumed = self.sample(running, 4)[0]['response_group']
        self.assertIsNone(resumed['episode_id'])
        self.assertEqual(resumed['idle_seconds'], 1)
        self.assertFalse(resumed['overdue'])

    def test_independent_session_and_duplicate_panes_do_not_change_group_totals(self):
        rows = [self.value('alpha'), self.value('alpha', pane='%mirror'),
                self.value('beta'), self.value('gamma')]
        for second in range(61):
            result = self.sample(rows, second)
        grouped = result[0]['response_group']
        self.assertEqual(grouped['idle_seconds'], 60)
        self.assertEqual(grouped['count'], 2)
        self.assertEqual(result[1]['response_group'], grouped)
        self.assertIsNone(result[-1]['response_group'])
        self.assertFalse(result[-1]['response']['reminder_suppressed'])
        self.assertTrue(result[-1]['response']['overdue'])

    def test_group_identity_and_membership_changes_reset_scope_but_rename_does_not(self):
        rows = [self.value('alpha'), self.value('beta')]
        self.sample(rows, 0)
        self.sample(rows, 2)
        self.settings['session_groups'][0]['name'] = 'Renamed'
        renamed = self.sample(rows, 3)[0]['response_group']
        self.assertEqual(renamed['idle_seconds'], 3)
        self.assertEqual(renamed['name'], 'Renamed')
        changed_rows = [dict(rows[0], process_id=99), rows[1]]
        replaced = self.sample(changed_rows, 4)[0]['response_group']
        self.assertEqual(replaced['idle_seconds'], 0)
        self.settings['session_groups'][0]['members'] = ['remote:alpha', 'remote:gamma']
        self.tracker.configure(self.settings)
        changed = self.sample([changed_rows[0], self.value('gamma')], 5)[0]['response_group']
        self.assertEqual(changed['idle_seconds'], 0)
        json.dumps(changed, allow_nan=False)


class ResponseBridgeSettingsTests(unittest.TestCase):
    def test_host_override_keeps_response_continuity_across_settings_reload(self):
        # The saved configuration monitors local sessions; --host additionally
        # enables remote monitoring without rewriting the saved source switch.
        settings = dict(defaults(), remote_enabled=False, local_enabled=True)
        clock = dict(now=0.0, sleeps=0)
        frame = dict(panes=[dict(session='alpha', pane_id='%1', process_id=1,
                                 state='idle', screen='prompt', preview=[])])
        stream = SimpleNamespace(host='example', last_packet=0, error='', process=object(),
                                 connections=1, total_frames=0, cursors={}, interval=.5,
                                 subscribe=lambda _: None, poll=lambda: [frame], close=lambda: None)
        local = SimpleNamespace(host='本机', last_packet=None, error='', process=object(),
                                connections=1, total_frames=0, cursors={}, interval=.5,
                                poll=lambda: [], close=lambda: None)

        def advance(_):
            clock['sleeps'] += 1
            if clock['sleeps'] == 3:
                raise KeyboardInterrupt
            clock['now'] += .6

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            with patch('desktop_bridge.load_settings', side_effect=lambda _: dict(settings)), \
                    patch('desktop_bridge.read_follow', return_value=[]), \
                    patch('desktop_bridge.data_dir', return_value=Path(folder)), \
                    patch('desktop_bridge.Stream', return_value=stream), \
                    patch('desktop_bridge.LocalStream', return_value=local), \
                    patch('desktop_bridge.signal.signal'), \
                    patch('desktop_bridge.time.monotonic', side_effect=lambda: clock['now']), \
                    patch('desktop_bridge.time.time', side_effect=lambda: 1000 + clock['now']), \
                    patch('desktop_bridge.time.sleep', side_effect=advance), \
                    contextlib.redirect_stdout(output):
                with self.assertRaises(KeyboardInterrupt):
                    run(argparse.Namespace(host='example', socket=None))
        packets = [json.loads(line) for line in output.getvalue().splitlines()]
        responses = [packet['rows'][0]['response'] for packet in packets]
        self.assertEqual(len(responses), 3)
        self.assertEqual(len({response['episode_id'] for response in responses}), 1)
        self.assertEqual(responses[-1]['elapsed_seconds'], 1.2)


if __name__ == '__main__':
    unittest.main()
