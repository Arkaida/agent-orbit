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


if __name__ == '__main__':
    unittest.main()
