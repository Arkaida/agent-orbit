import json
import unittest

from desktop_bridge import FeedState, source_payload
from usage_tracker import UsageTracker


def usage(total=1000, output=200, tools=2, identity='codex:root:1', **values):
    result = dict(identity=identity, agent='codex', scope='root_session',
                  usage_available=True, usage_complete=True,
                  input_tokens=total - output, output_tokens=output,
                  cached_input_tokens=0, cache_write_input_tokens=0,
                  total_tokens=total, tool_completions=tools, tools_complete=True)
    result.update(values)
    return result


def row(telemetry=None, source='remote', session='alpha', state='running', pane='%1', **values):
    result = dict(pane_id=pane, session=session, group_id=source + ':' + session,
                  source=source, process_id=42, state=state, status=state,
                  preview=['Synthetic output'], available=True)
    if telemetry is not None:
        result['telemetry'] = telemetry
    result.update(values)
    return result


class UsageTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = UsageTracker()
        self.connections = [dict(id='remote', connected=True)]

    def observe(self, rows, now, epoch=1):
        self.tracker.observe('remote', rows, now, epoch)
        return self.tracker.aggregate(rows, now, self.connections)

    def test_real_frame_baseline_and_fixed_window_deltas(self):
        first = [row(usage(total=1000000, output=200000))]
        result = self.observe(first, 0)
        self.assertEqual(result['total_tokens'], 1000000)
        self.assertIsNone(result['consumption_per_second'])
        # UI redraws cannot be the second real sample or add usage.
        for tenth in range(1, 10):
            result = self.tracker.aggregate(first, tenth / 10, self.connections)
            self.assertIsNone(result['consumption_per_second'])
            self.assertEqual(result['live_usage_covered'], 0)
        second = [row(usage(total=1000030, output=200006, tools=3))]
        result = self.observe(second, 1)
        self.assertEqual(result['consumption_per_second'], 1)
        self.assertEqual(result['upstream_tokens_per_second'], .2)
        self.assertEqual(result['tools_per_minute'], 1)
        for tenth in range(1, 10):
            repaint = self.tracker.aggregate(second, 1 + tenth / 10, self.connections)
            self.assertEqual(repaint['total_tokens'], 1000030)
            self.assertEqual(repaint['consumption_per_second'], 1)

    def test_same_root_is_counted_once_across_multiple_panes_and_groups(self):
        rows = [row(usage(), pane='%1'), row(usage(), pane='%2'),
                row(usage(), pane='%3', session='mirror')]
        result = self.observe(rows, 0)
        self.assertEqual(result['usage_sessions'], 1)
        self.assertEqual(result['usage_covered'], 1)
        self.assertEqual(result['monitored_sessions'], 2)
        self.assertEqual(result['total_tokens'], 1000)

    def test_dedup_prefers_reliable_complete_counters_then_newer_counters(self):
        rows = [row(usage(total=1000), pane='%1'),
                row(usage(total=9000, usage_complete=False), pane='%2'),
                row(usage(total=1100, tools=4), pane='%3')]
        result = self.observe(rows, 0)
        self.assertEqual(result['total_tokens'], 1100)
        self.assertEqual(result['tools_covered'], 1)

    def test_activity_bytes_are_never_usage_and_unsupported_roots_keep_coverage_slots(self):
        rows = [row(usage()), row(session='unsupported', pane='%2',
                                 output_total=999999999, output_rate=99999)]
        result = self.observe(rows, 0)
        self.assertEqual((result['usage_covered'], result['usage_sessions']), (1, 2))
        self.assertEqual(result['total_tokens'], 1000)
        only_bytes = self.observe(rows[1:], 1)
        self.assertIsNone(only_bytes['total_tokens'])
        self.assertIsNone(only_bytes['upstream_tokens_per_second'])

    def test_partial_scan_does_not_count_history_or_create_a_rate_spike(self):
        incomplete = [row(usage(total=100, usage_complete=False, tools_complete=False))]
        first = self.observe(incomplete, 0)
        self.assertEqual(first['usage_covered'], 0)
        self.assertIsNone(first['total_tokens'])
        full = [row(usage(total=1000000, output=500000, tools=500))]
        built = self.observe(full, 1)
        self.assertEqual(built['total_tokens'], 1000000)
        self.assertIsNone(built['consumption_per_second'])
        self.assertIsNone(built['tools_per_minute'])
        second = self.observe([row(usage(total=1000030, output=500010, tools=501))], 2)
        self.assertEqual(second['consumption_per_second'], 1)
        self.assertEqual(second['tools_per_minute'], 1)

    def test_incomplete_rescan_retains_confirmed_total_but_breaks_rate(self):
        self.observe([row(usage())], 0)
        self.observe([row(usage(total=1030))], 1)
        partial = self.observe([row(usage(total=9999, usage_complete=False))], 2)
        self.assertEqual(partial['total_tokens'], 1030)
        self.assertEqual(partial['usage_covered'], 1)
        self.assertEqual(partial['live_usage_covered'], 0)
        self.assertIsNone(partial['consumption_per_second'])
        resumed = self.observe([row(usage(total=9999))], 3)
        self.assertIsNone(resumed['consumption_per_second'])
        steady = self.observe([row(usage(total=10029))], 4)
        self.assertEqual(steady['consumption_per_second'], 1)

    def test_offline_cached_totals_and_reconnect_backlog(self):
        self.observe([row(usage())], 0)
        rows = [row(usage(total=1030, output=210, tools=3))]
        self.observe(rows, 1)
        offline = [dict(rows[0], available=False)]
        health = [dict(id='remote', connected=False)]
        result = self.tracker.aggregate(offline, 1.5, health)
        self.assertEqual(result['total_tokens'], 1030)
        self.assertEqual((result['usage_covered'], result['live_usage_covered']), (1, 0))
        self.assertIsNone(result['consumption_per_second'])
        self.assertIsNone(result['tools_per_minute'])
        backlog = self.observe([row(usage(total=100000, output=20000, tools=600))], 2, epoch=2)
        self.assertEqual(backlog['total_tokens'], 100000)
        self.assertIsNone(backlog['consumption_per_second'])
        fresh = self.observe([row(usage(total=100030, output=20006, tools=601))], 3, epoch=2)
        self.assertEqual(fresh['consumption_per_second'], 1)
        self.assertEqual(fresh['tools_per_minute'], 1)

    def test_partial_connections_sum_only_live_rates_with_separate_coverage(self):
        remote = [row(usage())]
        local = [row(usage(identity='codex:root:1', total=2000), source='local', session='beta')]
        self.tracker.observe('remote', remote, 0)
        self.tracker.observe('local', local, 0)
        remote = [row(usage(total=1030, output=210))]
        local = [row(usage(identity='codex:root:1', total=2060, output=220), source='local', session='beta')]
        self.tracker.observe('remote', remote, 1)
        self.tracker.observe('local', local, 1)
        result = self.tracker.aggregate(remote + [dict(local[0], available=False)], 1,
                                       [dict(id='remote', connected=True), dict(id='local', connected=False)])
        self.assertEqual(result['usage_covered'], 2)
        self.assertEqual(result['total_tokens'], 3090)
        self.assertEqual(result['live_usage_covered'], 1)
        self.assertEqual(result['consumption_per_second'], 1)

    def test_selection_change_and_counter_reset_only_establish_baselines(self):
        self.observe([row(usage())], 0, epoch=('connection', ('alpha',)))
        changed = self.observe([row(usage(total=100000))], 1, epoch=('connection', ('alpha', 'beta')))
        self.assertIsNone(changed['consumption_per_second'])
        fresh = self.observe([row(usage(total=100030))], 2, epoch=('connection', ('alpha', 'beta')))
        self.assertEqual(fresh['consumption_per_second'], 1)
        reset = self.observe([row(usage(total=100, output=20, tools=0))], 3,
                             epoch=('connection', ('alpha', 'beta')))
        self.assertIsNone(reset['consumption_per_second'])
        self.assertIsNone(reset['tools_per_minute'])
        self.assertEqual(reset['total_tokens'], 100)
        next_sample = self.observe([row(usage(total=130, output=30, tools=1))], 4,
                                  epoch=('connection', ('alpha', 'beta')))
        self.assertEqual(next_sample['consumption_per_second'], 1)
        self.assertEqual(next_sample['tools_per_minute'], 1)

    def test_fixed_windows_expire_without_any_fake_frame_deltas(self):
        self.observe([row(usage())], 0)
        changed = [row(usage(total=1030, output=230, tools=3))]
        self.observe(changed, 1)
        for second in range(2, 62):
            result = self.observe(changed, second)
            if second == 31:
                self.assertEqual(result['consumption_per_second'], 0)
                self.assertEqual(result['tools_per_minute'], 1)
        self.assertEqual(result['tools_per_minute'], 0)

    def test_slow_configured_sampling_keeps_continuity_and_stale_data_does_not(self):
        first = [row(usage())]
        second = [row(usage(total=1030))]
        self.tracker.observe('remote', first, 0, max_gap_seconds=9)
        self.tracker.observe('remote', second, 3, max_gap_seconds=9)
        result = self.tracker.aggregate(second, 3, self.connections)
        self.assertEqual(result['consumption_per_second'], 1)
        stale = self.tracker.aggregate(second, 13, self.connections)
        self.assertIsNone(stale['consumption_per_second'])
        self.assertEqual(stale['total_tokens'], 1030)

    def test_utilization_uses_known_online_groups_and_conservative_priority(self):
        rows = [row(usage()), row(state='idle', session='idle', pane='%2'),
                row(state='unknown', session='unknown', pane='%3'),
                row(state='running', session='unknown', pane='%4'),
                row(state='missing', session='missing', pane='%5'),
                row(state='idle', session='offline', pane='%6', available=False),
                row(state='approval', session='approval', pane='%7')]
        result = self.observe(rows, 0)
        self.assertEqual(result['monitored_sessions'], 6)
        self.assertEqual(result['known_sessions'], 3)
        self.assertEqual(result['running_sessions'], 1)
        self.assertEqual(result['utilization_percent'], 33.33)
        unknown = self.observe([row(state='unknown')], 1)
        self.assertIsNone(unknown['utilization_percent'])

    def test_invalid_counters_and_unknown_cache_are_not_zero(self):
        invalid = self.observe([row(usage(total=True, tool_completions=-1))], 0)
        self.assertIsNone(invalid['total_tokens'])
        self.assertEqual(invalid['tools_covered'], 0)
        unknown_cache = self.observe([row(usage(cached_input_tokens=None))], 1)
        self.assertEqual(unknown_cache['total_tokens'], 1000)
        self.assertIsNone(unknown_cache['cached_input_tokens'])
        json.dumps(unknown_cache, allow_nan=False)
        self.assertIsInstance(unknown_cache['window_seconds'], int)
        self.assertIsInstance(unknown_cache['work_window_seconds'], int)

    def test_replaced_and_unselected_identities_are_pruned_but_cached_roots_remain(self):
        self.observe([row(usage())], 0)
        self.observe([row(usage(identity='codex:new:2'))], 1)
        self.assertEqual(set(self.tracker.records), {('remote', 'codex:new:2')})
        offline = [row(usage(identity='codex:new:2'), available=False)]
        self.tracker.aggregate(offline, 1.5, [dict(id='remote', connected=False)])
        self.assertEqual(len(self.tracker.records), 1)
        empty = self.tracker.aggregate([], 2, self.connections)
        self.assertEqual(self.tracker.records, {})
        self.assertEqual(empty['usage_sessions'], 0)
        self.assertIsNone(empty['total_tokens'])

    def test_telemetry_survives_feed_activity_completion_payload_and_redraws(self):
        feed = FeedState()
        first = row(usage(), source='remote')
        feed.accept(dict(panes=[first]), ['alpha'], 1, 0, 0)
        payload = source_payload('remote', feed, dict(connected=True), ['alpha'])
        self.assertEqual(payload['rows'][0]['telemetry'], first['telemetry'])
        self.tracker.observe('remote', payload['rows'], 0, epoch=1)
        for tenth in range(1, 10):
            result = self.tracker.aggregate(payload['rows'], tenth / 10, self.connections)
            self.assertIsNone(result['consumption_per_second'])
        second = row(usage(total=1030, output=210, tools=3), source='remote')
        feed.accept(dict(panes=[second]), ['alpha'], 1, 1, 1)
        payload = source_payload('remote', feed, dict(connected=True), ['alpha'])
        self.tracker.observe('remote', payload['rows'], 1, epoch=1)
        result = self.tracker.aggregate(payload['rows'], 1, self.connections)
        self.assertEqual(result['consumption_per_second'], 1)
        cached = source_payload('remote', feed, dict(connected=False), ['alpha'])
        result = self.tracker.aggregate(cached['rows'], 1.1, [dict(id='remote', connected=False)])
        self.assertIsNone(result['consumption_per_second'])
        self.assertEqual(result['total_tokens'], 1030)
        json.dumps(dict(metrics=result, rows=cached['rows']), allow_nan=False)

    def test_feed_continuity_accepts_configured_slow_interval(self):
        feed = FeedState()
        feed.accept(dict(panes=[row(usage())]), [], 1, 0, 0, max_gap_seconds=9)
        generation = feed.generation
        feed.accept(dict(panes=[row(usage(total=1030))]), [], 1, 3, 3, max_gap_seconds=9)
        self.assertEqual(feed.generation, generation)


if __name__ == '__main__':
    unittest.main()
