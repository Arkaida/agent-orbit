import unittest

from activity_tracker import ActivityTracker
from desktop_bridge import FeedState


def row(text='result', state='running', pid=1, **values):
    return dict(pane_id='%1', session='x', process_id=pid, state=state,
                activity=text, preview=[text], **values)


class ActivityTests(unittest.TestCase):
    def test_first_sample_and_timer_changes_are_silent(self):
        tracker = ActivityTracker()
        self.assertEqual(tracker.update([row(working='Working (1s)')], 0)[0]['output_rate'], 0)
        self.assertEqual(tracker.update([row(working='Working (45s)')], 1)[0]['output_rate'], 0)

    def test_fold_counters_and_cli_tips_do_not_create_output_peaks(self):
        tracker = ActivityTracker()
        tracker.update([row('answer\n+ 1 line (^t to expand)')], 0)
        control = tracker.update([row('answer\n+ 100 lines (^t to expand)\n⎿ Tip: Use /btw to ask a question')], 1)[0]
        self.assertEqual(control['output_total'], 0)
        self.assertIsNone(control['activity_age'])
        real = tracker.update([row('answer with new content\n+ Show details')], 2)[0]
        self.assertGreater(real['output_total'], 0)

    def test_actual_output_increases_activity_then_decays(self):
        tracker = ActivityTracker()
        tracker.update([row('abc')], 0)
        result = tracker.update([row('abc1234567890')], 1)[0]
        self.assertEqual(result['output_rate'], 1)
        self.assertGreater(result['activity_level'], 0)
        self.assertEqual(result['activity_age'], 0)
        self.assertEqual(tracker.update([row('abc1234567890')], 12)[0]['output_rate'], 0)

    def test_idle_retry_and_history_do_not_generate_activity(self):
        for state, error in (('idle', ''), ('history', ''), ('unknown', ''), ('running', 'retrying')):
            with self.subTest(state=state, error=error):
                tracker = ActivityTracker()
                tracker.update([row('abc')], 0)
                result = tracker.update([row('entirely changed', state, error_kind=error)], 1)[0]
                self.assertEqual(result['output_rate'], 0)

    def test_reconnect_and_process_replacement_do_not_count_backlog(self):
        tracker = ActivityTracker()
        tracker.update([row('abc')], 0, epoch=0)
        self.assertEqual(tracker.update([row('new output')], 1, epoch=1)[0]['output_rate'], 0)
        self.assertEqual(tracker.update([row('different process', pid=2)], 2, epoch=1)[0]['output_rate'], 0)

    def test_background_output_can_drive_activity_with_unchanged_foreground(self):
        tracker = ActivityTracker()
        tracker.update([row(background_output_bytes=100)], 0)
        self.assertGreater(tracker.update([row(background_output_bytes=200)], 1)[0]['activity_level'], 0)

    def test_workflow_progress_updates_drive_activity(self):
        tracker = ActivityTracker()
        tracker.update([row(background_progress='0/4')], 0)
        self.assertEqual(tracker.update([row(background_progress='0/4')], .5)[0]['output_rate'], 0)
        self.assertGreater(tracker.update([row(background_progress='1/4')], 1)[0]['activity_level'], 0)

    def test_claude_generated_token_growth_is_activity_without_new_prose(self):
        tracker = ActivityTracker()
        def thinking(glyph, seconds, tokens):
            return row(agent='claude', working='{} Thinking… ({}s · ↓ {} tokens)'.format(glyph, seconds, tokens))
        first = tracker.update([thinking('✻', 1, '1.0k')], 0)[0]
        self.assertEqual(first['output_total'], 0)
        timer = tracker.update([thinking('·', 9, '1.0k')], 1)[0]
        self.assertEqual(timer['output_total'], 0)
        generated = tracker.update([thinking('*', 10, '1.2k')], 2)[0]
        self.assertEqual(generated['output_total'], 200)
        self.assertEqual(generated['activity_source'], 'tokens')
        self.assertEqual(generated['activity_age'], 0)
        reset = tracker.update([thinking('✢', 1, '10')], 3)[0]
        self.assertEqual(reset['output_total'], 200)
        following = tracker.update([thinking('✻', 2, '20')], 4)[0]
        self.assertEqual(following['output_total'], 210)

    def test_claude_counter_first_seen_reconnect_and_other_agents_are_baselines(self):
        tracker = ActivityTracker()
        working = '* Thinking… (20s · ↓ 50k tokens)'
        self.assertEqual(tracker.update([row(agent='claude', working=working)], 0, epoch=1)[0]['output_total'], 0)
        reconnect = tracker.update([row(agent='claude', working=working.replace('50k', '60k'))], 1, epoch=2)[0]
        self.assertEqual(reconnect['output_total'], 0)
        tracker = ActivityTracker()
        tracker.update([row(agent='codex', working=working)], 0)
        other = tracker.update([row(agent='codex', working=working.replace('50k', '60k'))], 1)[0]
        self.assertEqual(other['output_total'], 0)

    def test_root_log_growth_is_visible_even_when_terminal_is_folded(self):
        tracker = ActivityTracker()
        cursor = dict(path='/logs/root.jsonl', device=1, inode=2, offset=1000)
        first = tracker.update([row(lifecycle_cursor=cursor)], 0)[0]
        self.assertEqual(first['output_total'], 0)
        changed = tracker.update([row(lifecycle_cursor=dict(cursor, offset=1120))], .5)[0]
        self.assertEqual(changed['output_total'], 120)
        self.assertEqual(changed['activity_source'], 'log')
        self.assertEqual(changed['activity_age'], 0)
        same = tracker.update([row(lifecycle_cursor=dict(cursor, offset=1120))], 1)[0]
        self.assertEqual(same['output_total'], 120)
        self.assertEqual(same['activity_age'], .5)

    def test_file_replacement_and_reconnect_never_turn_backlog_into_peaks(self):
        tracker = ActivityTracker()
        cursor = dict(path='/logs/root.jsonl', device=1, inode=2, offset=1000)
        first = tracker.update([row(lifecycle_cursor=cursor)], 0, epoch=1)[0]
        replacement = tracker.update([row(lifecycle_cursor=dict(cursor, inode=3, offset=9000))], 1, epoch=1)[0]
        self.assertEqual(replacement['output_total'], 0)
        resumed = tracker.update([row(lifecycle_cursor=dict(cursor, inode=3, offset=18000))], 2, epoch=2)[0]
        self.assertEqual(resumed['output_total'], 0)
        self.assertNotEqual(first['activity_epoch'], resumed['activity_epoch'])

    def test_cumulative_output_survives_decay_but_isolated_from_new_process(self):
        tracker = ActivityTracker()
        tracker.update([row('abc')], 0)
        changed = tracker.update([row('abcdef')], 1)[0]
        self.assertEqual(changed['output_total'], 3)
        quiet = tracker.update([row('abcdef')], 20)[0]
        self.assertEqual(quiet['output_total'], 3)
        self.assertEqual(quiet['activity_level'], 0)
        replacement = tracker.update([row('new process backlog', pid=2)], 21)[0]
        self.assertEqual(replacement['output_total'], 0)
        self.assertNotEqual(replacement['activity_epoch'], changed['activity_epoch'])

    def test_idle_log_updates_and_restarting_work_are_not_new_output(self):
        tracker = ActivityTracker()
        cursor = dict(path='/logs/root.jsonl', device=1, inode=2, offset=1000)
        tracker.update([row(lifecycle_cursor=cursor)], 0)
        idle = tracker.update([row(state='idle', lifecycle_cursor=dict(cursor, offset=2000))], 1)[0]
        self.assertEqual(idle['output_total'], 0)
        running = tracker.update([row(lifecycle_cursor=dict(cursor, offset=3000))], 2)[0]
        self.assertEqual(running['output_total'], 0)
        output = tracker.update([row(lifecycle_cursor=dict(cursor, offset=3010))], 3)[0]
        self.assertEqual(output['output_total'], 10)

    def test_buffer_is_bounded_and_input_is_preserved(self):
        tracker = ActivityTracker()
        original = row('x' * 2000)
        tracker.update([original], 0)
        self.assertEqual(len(tracker.panes['%1']['text']), 1200)
        self.assertNotIn('output_rate', original)

    def test_bridge_emits_rates_and_breaks_continuity(self):
        state = FeedState()
        state.accept(dict(panes=[row('a')]), ['x'], 1, 0, 0)
        state.accept(dict(panes=[row('a more output')]), ['x'], 1, .5, .5)
        self.assertGreater(state.rows[0]['output_rate'], 0)
        state.connection(False, 1, 1, 'offline')
        state.accept(dict(panes=[row('backlog after reconnect')]), ['x'], 2, 3, 3)
        self.assertEqual(state.rows[0]['output_rate'], 0)


class DialogueOutputTests(unittest.TestCase):
    def setUp(self):
        self.tracker = ActivityTracker()

    def observe(self, text='answer', now=0, sample=None, epoch=0, **values):
        item = row(dialogue_text=text, dialogue_source='assistant_screen',
                   dialogue_identity='terminal:codex', dialogue_available=True,
                   dialogue_complete=False, dialogue_sample_id=str(now if sample is None else sample))
        item.update(values)
        return self.tracker.update([item], now, epoch=epoch)[0]['dialogue_output']

    def test_visible_output_is_independent_of_hardware_and_mixed_activity(self):
        baseline = self.observe('abc')
        changed = self.observe('abc1234567890', 1)
        self.assertEqual(baseline['total_chars'], 0)
        self.assertEqual(changed['total_chars'], 10)
        noisy = self.observe('abc1234567890', 2, background_output_bytes=9999,
                             background_revision=5, background_progress='4/4',
                             lifecycle_cursor=dict(path='/log', device=1, inode=2, offset=9999),
                             agent='claude', working='* Thinking… (9s · ↓ 10k tokens)')
        # Root identity was newly discovered, so it establishes a fresh stream.
        self.assertEqual(noisy['total_chars'], 0)
        same = self.observe('abc1234567890', 3, background_output_bytes=19999,
                            lifecycle_cursor=dict(path='/log', device=1, inode=2, offset=19999),
                            agent='claude', working='* Thinking… (10s · ↓ 20k tokens)')
        self.assertEqual(same['total_chars'], 0)

    def test_reflow_deletion_scroll_and_uncertain_replacement(self):
        self.observe('one two three')
        self.assertEqual(self.observe('one\n two\n three', 1)['total_chars'], 0)
        self.assertEqual(self.observe('two three', 2)['total_chars'], 0)
        self.assertEqual(self.observe('three four', 3)['total_chars'], 4)
        self.assertEqual(self.observe('unrelated screen', 4)['total_chars'], 4)
        self.assertEqual(self.observe('unrelated screen正文', 5)['total_chars'], 6)

    def test_baselines_for_epoch_process_identity_and_direct_gap(self):
        first = self.observe('abc')
        changed = self.observe('abcd', 1)
        self.assertEqual(changed['total_chars'], 1)
        gap = self.observe('abcde backlog', 4)
        self.assertEqual(gap['total_chars'], 0)
        self.assertNotEqual(gap['epoch'], first['epoch'])
        reconnected = self.observe('more backlog', 5, epoch=1)
        self.assertEqual(reconnected['total_chars'], 0)
        self.assertNotEqual(reconnected['epoch'], gap['epoch'])
        replacement = self.observe('other source backlog', 6, epoch=1, dialogue_identity='new-log')
        self.assertEqual(replacement['total_chars'], 0)
        self.assertNotEqual(replacement['epoch'], reconnected['epoch'])
        process = self.observe('replacement process', 7, epoch=1, process_id=2)
        self.assertNotEqual(process['epoch'], replacement['epoch'])

    def test_completed_final_chunk_and_resume_do_not_replay_history(self):
        self.observe('abc')
        final = self.observe('abc final chunk', 1, state='idle')
        self.assertEqual(final['total_chars'], 0)
        resumed = self.observe('abc final chunk', 2)
        self.assertEqual(resumed['total_chars'], 0)
        self.assertNotEqual(resumed['epoch'], final['epoch'])
        self.assertEqual(self.observe('abc final chunk new', 3)['total_chars'], 3)

    def test_missing_history_and_error_sources_never_manufacture_output(self):
        self.observe('abc')
        unavailable = self.observe('abc user input', 1, dialogue_available=False)
        self.assertFalse(unavailable['available'])
        recovered = self.observe('abc history', 2)
        self.assertTrue(recovered['available'])
        self.assertEqual(recovered['total_chars'], 0)
        self.assertNotEqual(recovered['epoch'], unavailable['epoch'])
        self.assertEqual(self.observe('abc history unsafe', 3, error_kind='question')['total_chars'], 0)
        self.assertEqual(self.observe('abc history unsafe', 4)['total_chars'], 0)
        legacy = self.tracker.update([row('many bytes')], 5)[0]['dialogue_output']
        self.assertFalse(legacy['available'])
        self.assertEqual(legacy['total_chars'], 0)

    def test_reused_payload_does_not_renew_sample_freshness(self):
        baseline = self.observe('abc', 0, sample='first')
        reused = self.observe('abc', 1, sample='first')
        self.assertEqual(reused['sample_id'], baseline['sample_id'])
        self.assertEqual(reused['sample_age_seconds'], 1)
        reused_later = self.observe('abc', 4, sample='first')
        self.assertEqual(reused_later['sample_age_seconds'], 4)
        recovered = self.observe('abc backlog', 5, sample='new')
        self.assertNotEqual(recovered['epoch'], baseline['epoch'])
        self.assertEqual(recovered['total_chars'], 0)

    def test_real_quiet_observations_stay_fresh_and_next_chunk_counts(self):
        first = self.observe('abc', 0)
        for second in range(1, 7):
            quiet = self.observe('abc', second)
            self.assertEqual(quiet['sample_id'], second + 1)
            self.assertEqual(quiet['sample_age_seconds'], 0)
            self.assertEqual(quiet['epoch'], first['epoch'])
        self.assertEqual(self.observe('abc新结果', 7)['total_chars'], 3)

    @staticmethod
    def collector_frame(text, sample):
        return dict(panes=[row(dialogue_text=text, dialogue_source='assistant_screen',
                               dialogue_identity='terminal:codex', dialogue_available=True,
                               dialogue_sample_id=str(sample))])

    def test_bridge_counts_dialogue_at_configured_three_and_five_second_intervals(self):
        for interval in (3, 5):
            with self.subTest(interval=interval):
                state = FeedState()
                gap = max(2, interval * 3)
                for sample, text in enumerate(('abc', 'abcdef', 'abcdefghi')):
                    now = sample * interval
                    state.accept(self.collector_frame(text, sample), ['x'], 1, now, now,
                                 max_gap_seconds=gap)
                    observed = state.rows[0]['dialogue_output']
                    self.assertEqual(observed['total_chars'], sample * 3)
                    self.assertEqual(observed['sample_id'], sample + 1)
                    if sample == 0:
                        epoch = observed['epoch']
                    else:
                        self.assertEqual(observed['epoch'], epoch)

    def test_configured_slow_sampling_still_baselines_gaps_reconnects_and_replays(self):
        state = FeedState(); gap = 15
        state.accept(self.collector_frame('abc', 0), ['x'], 1, 0, 0, max_gap_seconds=gap)
        state.accept(self.collector_frame('abcdef', 1), ['x'], 1, 5, 5, max_gap_seconds=gap)
        active = state.rows[0]['dialogue_output']
        self.assertEqual(active['total_chars'], 3)
        # Heartbeats/reused frames do not extend the producer's observation age.
        for now in (10, 15, 20):
            state.accept(self.collector_frame('abcdef', 1), ['x'], 1, now, now, max_gap_seconds=gap)
            self.assertEqual(state.rows[0]['dialogue_output']['sample_id'], active['sample_id'])
        state.accept(self.collector_frame('abcdef missed backlog', 2), ['x'], 1, 25, 25, max_gap_seconds=gap)
        resumed = state.rows[0]['dialogue_output']
        self.assertEqual(resumed['total_chars'], 0)
        self.assertNotEqual(resumed['epoch'], active['epoch'])
        state.accept(self.collector_frame('abcdef missed backlog more', 3), ['x'], 2, 30, 30, max_gap_seconds=gap)
        reconnected = state.rows[0]['dialogue_output']
        self.assertEqual(reconnected['total_chars'], 0)
        self.assertNotEqual(reconnected['epoch'], resumed['epoch'])
        state.accept(self.collector_frame('abcdef missed backlog more output', 4), ['x'], 2, 50, 50, max_gap_seconds=gap)
        self.assertEqual(state.rows[0]['dialogue_output']['total_chars'], 0)
        self.assertNotEqual(state.rows[0]['dialogue_output']['epoch'], reconnected['epoch'])

    def test_aliases_share_epoch_and_count_but_shared_pid_logs_do_not(self):
        def aliases(text, sample):
            common = row(dialogue_text=text, dialogue_source='assistant_screen',
                         dialogue_identity='terminal:codex', dialogue_available=True,
                         dialogue_sample_id=sample)
            return [common, dict(common, pane_id='%2', session='alias')]
        first = self.tracker.update(aliases('abc', '1'), 0)
        second = self.tracker.update(aliases('abcdef', '2'), 1)
        self.assertEqual(first[0]['dialogue_output'], first[1]['dialogue_output'])
        self.assertEqual(second[0]['dialogue_output'], second[1]['dialogue_output'])
        self.assertEqual(second[0]['dialogue_output']['total_chars'], 3)
        independent = aliases('x', '3')
        independent[0]['dialogue_identity'] = 'assistant-log:1'
        independent[1]['dialogue_identity'] = 'assistant-log:2'
        result = self.tracker.update(independent, 2)
        self.assertNotEqual(result[0]['dialogue_output']['epoch'], result[1]['dialogue_output']['epoch'])

    def test_bound_and_input_preservation_and_unique_tracker_instances(self):
        self.observe('x' * 40000)
        self.assertEqual(len(next(iter(self.tracker.dialogue.streams.values()))['text']), 16384)
        item = row(dialogue_text='abc', dialogue_available=True, dialogue_source='assistant_screen')
        a = self.tracker.update([item], 1)[0]['dialogue_output']
        b = ActivityTracker().update([item], 1)[0]['dialogue_output']
        self.assertNotIn('dialogue_output', item)
        self.assertNotEqual(a['epoch'], b['epoch'])


if __name__ == '__main__':
    unittest.main()
