import json
import copy
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

    def assertWindowUsage(self, metrics, tokens=None, output=None):
        self.assertEqual(metrics['window_tokens'], tokens)
        self.assertEqual(metrics['window_output_tokens'], output)
        if tokens is not None:
            self.assertIs(type(metrics['window_tokens']), int)
            self.assertIs(type(metrics['window_output_tokens']), int)

    def test_real_frame_baseline_and_fixed_window_deltas(self):
        first = [row(usage(total=1000000, output=200000))]
        result = self.observe(first, 0)
        self.assertEqual(result['total_tokens'], 1000000)
        self.assertIsNone(result['consumption_per_second'])
        self.assertWindowUsage(result)
        # UI redraws cannot be the second real sample or add usage.
        for tenth in range(1, 10):
            result = self.tracker.aggregate(first, tenth / 10, self.connections)
            self.assertIsNone(result['consumption_per_second'])
            self.assertEqual(result['live_usage_covered'], 0)
            self.assertWindowUsage(result)
        second = [row(usage(total=1000030, output=200006, tools=3))]
        result = self.observe(second, 1)
        self.assertEqual(result['consumption_per_second'], 1)
        self.assertEqual(result['upstream_tokens_per_second'], .2)
        self.assertEqual(result['tools_per_minute'], 1)
        self.assertWindowUsage(result, 30, 6)
        for tenth in range(1, 10):
            repaint = self.tracker.aggregate(second, 1 + tenth / 10, self.connections)
            self.assertEqual(repaint['total_tokens'], 1000030)
            self.assertEqual(repaint['consumption_per_second'], 1)
            self.assertWindowUsage(repaint, 30, 6)

    def test_window_counts_are_exact_integers_not_reconstructed_rounded_rates(self):
        self.observe([row(usage())], 0)
        rows = [row(usage(total=1007, output=202))]
        metrics = self.observe(rows, 1)
        self.assertEqual(metrics['consumption_per_second'], .233)
        self.assertWindowUsage(metrics, 7, 2)
        self.assertWindowUsage(self.tracker.pane_metrics(rows[0], 1, self.connections), 7, 2)
        rows = [row(usage(total=1011, output=203))]
        self.assertWindowUsage(self.observe(rows, 2), 11, 3)
        self.assertWindowUsage(self.tracker.pane_metrics(rows[0], 2, self.connections), 11, 3)

    def test_continuous_unchanged_samples_confirm_zero_window_usage(self):
        rows = [row(usage())]
        self.assertWindowUsage(self.observe(rows, 0))
        self.assertWindowUsage(self.observe(rows, 1), 0, 0)
        self.assertWindowUsage(self.tracker.pane_metrics(rows[0], 1, self.connections), 0, 0)

    def test_same_root_is_counted_once_across_multiple_panes_and_groups(self):
        rows = [row(usage(), pane='%1'), row(usage(), pane='%2'),
                row(usage(), pane='%3', session='mirror')]
        result = self.observe(rows, 0)
        self.assertEqual(result['usage_sessions'], 1)
        self.assertEqual(result['usage_covered'], 1)
        self.assertEqual(result['monitored_sessions'], 2)
        self.assertEqual(result['total_tokens'], 1000)
        changed = [dict(item, telemetry=usage(total=1037, output=211)) for item in rows]
        self.assertWindowUsage(self.observe(changed, 1), 37, 11)
        for item in changed:
            self.assertWindowUsage(self.tracker.pane_metrics(item, 1, self.connections), 37, 11)

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
        self.assertWindowUsage(first)
        full = [row(usage(total=1000000, output=500000, tools=500))]
        built = self.observe(full, 1)
        self.assertEqual(built['total_tokens'], 1000000)
        self.assertIsNone(built['consumption_per_second'])
        self.assertIsNone(built['tools_per_minute'])
        self.assertWindowUsage(built)
        second = self.observe([row(usage(total=1000030, output=500010, tools=501))], 2)
        self.assertEqual(second['consumption_per_second'], 1)
        self.assertEqual(second['tools_per_minute'], 1)
        self.assertWindowUsage(second, 30, 10)

    def test_incomplete_rescan_retains_confirmed_total_but_breaks_rate(self):
        self.observe([row(usage())], 0)
        self.observe([row(usage(total=1030))], 1)
        partial = self.observe([row(usage(total=9999, usage_complete=False))], 2)
        self.assertEqual(partial['total_tokens'], 1030)
        self.assertEqual(partial['usage_covered'], 1)
        self.assertEqual(partial['live_usage_covered'], 0)
        self.assertIsNone(partial['consumption_per_second'])
        self.assertWindowUsage(partial)
        self.assertWindowUsage(self.tracker.pane_metrics(
            row(usage(total=9999, usage_complete=False)), 2, self.connections))
        resumed = self.observe([row(usage(total=9999))], 3)
        self.assertIsNone(resumed['consumption_per_second'])
        self.assertWindowUsage(resumed)
        steady = self.observe([row(usage(total=10029))], 4)
        self.assertEqual(steady['consumption_per_second'], 1)
        self.assertWindowUsage(steady, 30, 0)

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
        self.assertWindowUsage(result)
        backlog = self.observe([row(usage(total=100000, output=20000, tools=600))], 2, epoch=2)
        self.assertEqual(backlog['total_tokens'], 100000)
        self.assertIsNone(backlog['consumption_per_second'])
        self.assertWindowUsage(backlog)
        fresh = self.observe([row(usage(total=100030, output=20006, tools=601))], 3, epoch=2)
        self.assertEqual(fresh['consumption_per_second'], 1)
        self.assertEqual(fresh['tools_per_minute'], 1)
        self.assertWindowUsage(fresh, 30, 6)

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
        self.assertWindowUsage(result, 30, 10)

    def test_window_counts_preserve_partial_live_coverage_with_unknown_root(self):
        unknown = row(session='unsupported', pane='%2')
        first = [row(usage()), unknown]
        self.assertWindowUsage(self.observe(first, 0))
        rows = [row(usage(total=1031, output=207)), unknown]
        metrics = self.observe(rows, 1)
        self.assertEqual((metrics['live_usage_covered'], metrics['usage_sessions']), (1, 2))
        self.assertWindowUsage(metrics, 31, 7)
        self.assertWindowUsage(self.tracker.pane_metrics(unknown, 1, self.connections))

    def test_selection_change_and_counter_reset_only_establish_baselines(self):
        self.observe([row(usage())], 0, epoch=('connection', ('alpha',)))
        changed = self.observe([row(usage(total=100000))], 1, epoch=('connection', ('alpha', 'beta')))
        self.assertIsNone(changed['consumption_per_second'])
        self.assertWindowUsage(changed)
        fresh = self.observe([row(usage(total=100030))], 2, epoch=('connection', ('alpha', 'beta')))
        self.assertEqual(fresh['consumption_per_second'], 1)
        self.assertWindowUsage(fresh, 30, 0)
        reset = self.observe([row(usage(total=100, output=20, tools=0))], 3,
                             epoch=('connection', ('alpha', 'beta')))
        self.assertIsNone(reset['consumption_per_second'])
        self.assertIsNone(reset['tools_per_minute'])
        self.assertEqual(reset['total_tokens'], 100)
        self.assertWindowUsage(reset)
        next_sample = self.observe([row(usage(total=130, output=30, tools=1))], 4,
                                  epoch=('connection', ('alpha', 'beta')))
        self.assertEqual(next_sample['consumption_per_second'], 1)
        self.assertEqual(next_sample['tools_per_minute'], 1)
        self.assertWindowUsage(next_sample, 30, 10)

    def test_fixed_windows_expire_without_any_fake_frame_deltas(self):
        self.observe([row(usage())], 0)
        changed = [row(usage(total=1030, output=230, tools=3))]
        self.observe(changed, 1)
        for second in range(2, 62):
            result = self.observe(changed, second)
            if second == 31:
                self.assertEqual(result['consumption_per_second'], 0)
                self.assertEqual(result['tools_per_minute'], 1)
                self.assertWindowUsage(result, 0, 0)
                self.assertWindowUsage(self.tracker.pane_metrics(changed[0], second, self.connections), 0, 0)
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
        self.assertWindowUsage(stale)

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

    def test_response_group_is_one_slot_without_joining_distinct_root_ledgers(self):
        group = dict(id='pair', kind='running')
        first = [row(usage(), response_group=group),
                 row(usage(total=2000, output=400, identity='codex:beta:2'),
                     session='beta', pane='%2', state='idle', response_group=group),
                 row(usage(total=4000, output=800, identity='codex:gamma:3'),
                     session='gamma', pane='%3', state='idle')]
        first.append(dict(first[0], pane_id='%mirror'))
        self.observe(first, 0)
        second = [dict(first[0], telemetry=usage(total=1030, output=206)),
                  dict(first[1], telemetry=usage(total=2060, output=412, identity='codex:beta:2')),
                  first[2],
                  dict(first[3], telemetry=usage(total=1030, output=206))]
        result = self.observe(second, 1)
        self.assertEqual((result['monitored_sessions'], result['known_sessions'], result['running_sessions']), (2, 2, 1))
        self.assertEqual(result['utilization_percent'], 50)
        self.assertEqual((result['usage_sessions'], result['usage_covered']), (3, 3))
        self.assertEqual(result['total_tokens'], 7090)
        self.assertWindowUsage(result, 90, 18)
        self.assertWindowUsage(self.tracker.pane_metrics(second[0], 1, self.connections), 30, 6)
        self.assertWindowUsage(self.tracker.pane_metrics(second[1], 1, self.connections), 60, 12)

    def test_response_group_keeps_unidentified_root_coverage_and_slot_namespaces(self):
        group = dict(id='pair', kind='waiting')
        rows = [row(session='alpha', response_group=group, state='idle'),
                row(session='beta', pane='%2', response_group=group, state='idle'),
                row(usage(), session='pair', pane='%3', state='running')]
        result = self.observe(rows, 0)
        self.assertEqual((result['monitored_sessions'], result['known_sessions'], result['running_sessions']), (2, 2, 1))
        self.assertEqual((result['usage_sessions'], result['usage_covered']), (3, 1))
        self.assertEqual(result['total_tokens'], 1000)

    def test_response_group_priority_only_changes_explicit_joint_slots(self):
        # Critical/attention states still outrank work, while an idle or unknown
        # partner cannot hide actual work inside an explicit response group.
        cases = [('idle', {}, 1, 1), ('unknown', {}, 1, 1),
                 ('approval', {}, 1, 0), ('error', {}, 1, 0),
                 ('missing', {}, 1, 0), ('missing', dict(available=False), 1, 0),
                 ('running', dict(attention_level='warning'), 1, 0)]
        for partner_state, changes, known, running in cases:
            with self.subTest(partner=partner_state, changes=changes):
                group = dict(id='pair', kind='unknown')
                rows = [row(state='running', response_group=group),
                        row(state=partner_state, session='beta', pane='%2', response_group=group, **changes)]
                for ordered in (rows, list(reversed(rows))):
                    result = self.tracker.aggregate(ordered, 0, self.connections)
                    self.assertEqual(result['monitored_sessions'], 1)
                    self.assertEqual((result['known_sessions'], result['running_sessions']), (known, running))
        ordinary = [row(state='running'), row(state='unknown', pane='%2')]
        result = self.tracker.aggregate(ordinary, 0, self.connections)
        self.assertEqual((result['known_sessions'], result['running_sessions']), (0, 0))

    def test_response_group_cross_source_usage_keeps_source_namespaces(self):
        group = dict(id='pair', kind='running')
        remote = [row(usage(), response_group=group)]
        local = [row(usage(total=2000), source='local', state='idle', response_group=group)]
        health = self.connections + [dict(id='local', connected=True)]
        for source, rows in (('remote', remote), ('local', local)):
            self.tracker.observe(source, rows, 0)
        remote = [dict(remote[0], telemetry=usage(total=1030, output=206))]
        local = [dict(local[0], telemetry=usage(total=2060, output=212))]
        for source, rows in (('remote', remote), ('local', local)):
            self.tracker.observe(source, rows, 1)
        result = self.tracker.aggregate(remote + local, 1, health)
        self.assertEqual((result['monitored_sessions'], result['known_sessions'], result['running_sessions']), (1, 1, 1))
        self.assertEqual((result['usage_sessions'], result['usage_covered']), (2, 2))
        self.assertEqual(result['total_tokens'], 3090)
        self.assertWindowUsage(result, 90, 18)

    def test_response_group_partial_offline_cannot_become_a_confirmed_idle_slot(self):
        health = [dict(id='remote', connected=True), dict(id='local', connected=False)]
        for joint_kind in ('offline', 'unknown', 'waiting'):
            group = dict(id='pair', kind=joint_kind)
            local = row(source='local', state='idle', available=False, response_group=group)
            remote = row(state='idle', response_group=group)
            result = self.tracker.aggregate([remote, local], 0, health)
            self.assertEqual((result['monitored_sessions'], result['known_sessions'], result['running_sessions']), (1, 0, 0))
            self.assertIsNone(result['utilization_percent'])
            working = self.tracker.aggregate([dict(remote, state='running'), local], 0, health)
            self.assertEqual((working['known_sessions'], working['running_sessions']), (1, 1))
            self.assertEqual(working['utilization_percent'], 100)
            offline = self.tracker.aggregate([dict(remote, available=False), local], 0, health)
            self.assertEqual((offline['known_sessions'], offline['running_sessions']), (0, 0))

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
        self.assertWindowUsage(empty)

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

    def test_per_pane_metrics_do_not_include_other_root_usage(self):
        first = [row(usage()), row(usage(identity='codex:beta:2', total=2000), session='beta', pane='%2')]
        self.observe(first, 0)
        second = [row(usage(total=1030, output=206, tools=3)),
                  row(usage(identity='codex:beta:2', total=2300, output=260, tools=12), session='beta', pane='%2')]
        global_metrics = self.observe(second, 1)
        before = copy.deepcopy(self.tracker.records)
        alpha = self.tracker.pane_metrics(second[0], 1, self.connections)
        beta = self.tracker.pane_metrics(second[1], 1, self.connections)
        self.assertEqual(alpha['scope'], 'root_session')
        self.assertEqual(alpha['root_identity'], 'codex:root:1')
        self.assertEqual(beta['root_identity'], 'codex:beta:2')
        self.assertEqual((alpha['total_tokens'], beta['total_tokens']), (1030, 2300))
        self.assertEqual((alpha['consumption_per_second'], beta['consumption_per_second']), (1, 10))
        self.assertEqual((alpha['upstream_tokens_per_second'], beta['upstream_tokens_per_second']), (.2, 2))
        self.assertEqual((alpha['tools_per_minute'], beta['tools_per_minute']), (1, 10))
        self.assertEqual(global_metrics['consumption_per_second'], 11)
        self.assertWindowUsage(alpha, 30, 6)
        self.assertWindowUsage(beta, 300, 60)
        self.assertWindowUsage(global_metrics, 330, 66)
        self.assertEqual(self.tracker.records, before)

    def test_per_pane_baseline_missing_and_offline_rates_remain_null(self):
        rows = [row(usage())]
        self.observe(rows, 0)
        for tenth in range(10):
            metrics = self.tracker.pane_metrics(rows[0], tenth / 10, self.connections)
            self.assertIsNone(metrics['consumption_per_second'])
            self.assertEqual(metrics['total_tokens'], 1000)
            self.assertWindowUsage(metrics)
        rows = [row(usage(total=1030))]
        self.observe(rows, 1)
        offline = self.tracker.pane_metrics(dict(rows[0], available=False), 1, self.connections)
        self.assertEqual(offline['total_tokens'], 1030)
        self.assertEqual(offline['usage_covered'], 1)
        self.assertEqual(offline['live_usage_covered'], 0)
        self.assertIsNone(offline['consumption_per_second'])
        self.assertWindowUsage(offline)
        stale = self.tracker.pane_metrics(rows[0], 4, self.connections)
        self.assertIsNone(stale['consumption_per_second'])
        self.assertWindowUsage(stale)
        stopped = self.tracker.pane_metrics(dict(rows[0], state='missing'), 1, self.connections)
        self.assertWindowUsage(stopped)
        missing = self.tracker.pane_metrics(row(), 1, self.connections)
        self.assertIsNone(missing['total_tokens'])
        self.assertIsNone(missing['tools_per_minute'])
        self.assertEqual(missing['usage_covered'], 0)
        self.assertIsNone(missing['root_identity'])
        unsupported = self.tracker.pane_metrics(row(usage(scope='subagent')), 1, self.connections)
        self.assertIsNone(unsupported['root_identity'])
        self.assertWindowUsage(missing)
        json.dumps(missing, allow_nan=False)

    def test_per_pane_duplicate_roots_and_source_namespaces(self):
        remote = [row(usage()), row(usage(), pane='%2')]
        local = [row(usage(total=5000), source='local')]
        self.tracker.observe('remote', remote, 0)
        self.tracker.observe('local', local, 0)
        remote = [row(usage(total=1030)), row(usage(total=1030), pane='%2')]
        local = [row(usage(total=5300), source='local')]
        self.tracker.observe('remote', remote, 1)
        self.tracker.observe('local', local, 1)
        health = self.connections + [dict(id='local', connected=True)]
        self.assertEqual(self.tracker.pane_metrics(remote[0], 1, health),
                         self.tracker.pane_metrics(remote[1], 1, health))
        self.assertEqual(self.tracker.pane_metrics(local[0], 1, health)['consumption_per_second'], 10)
        self.assertEqual(self.tracker.pane_metrics(remote[0], 1, health)['consumption_per_second'], 1)
        self.assertWindowUsage(self.tracker.pane_metrics(local[0], 1, health), 300, 0)
        self.assertWindowUsage(self.tracker.pane_metrics(remote[0], 1, health), 30, 0)

    def test_per_pane_tools_can_be_complete_without_token_coverage(self):
        first = [row(usage(usage_complete=False))]
        self.observe(first, 0)
        second = [row(usage(usage_complete=False, tool_completions=3))]
        self.observe(second, 1)
        metrics = self.tracker.pane_metrics(second[0], 1, self.connections)
        self.assertIsNone(metrics['total_tokens'])
        self.assertIsNone(metrics['consumption_per_second'])
        self.assertWindowUsage(metrics)
        self.assertEqual(metrics['tools_per_minute'], 1)
        self.assertEqual(metrics['tools_covered'], 1)

    def test_tool_activity_is_static_on_first_complete_frame_without_faking_a_rate(self):
        rows = [row(usage(active_tools=2), state='idle')]
        result = self.observe(rows, 0)
        metrics = self.tracker.pane_metrics(rows[0], 0, self.connections)
        self.assertEqual((metrics['tool_completions'], metrics['active_tools']), (2, 2))
        self.assertEqual(metrics['tools_epoch'], 0)
        self.assertIsNone(metrics['tools_per_minute'])
        self.assertEqual((result['running_sessions'], rows[0]['state']), (0, 'idle'))
        before = copy.deepcopy(self.tracker.records)
        for tenth in range(1, 10):
            repaint = self.tracker.pane_metrics(rows[0], tenth / 10, self.connections)
            self.assertEqual((repaint['tool_completions'], repaint['active_tools']), (2, 2))
            self.assertIsNone(repaint['tools_per_minute'])
        self.assertEqual(self.tracker.records, before)
        finished = [row(usage(tools=3, active_tools=1), state='idle')]
        self.observe(finished, 1)
        metrics = self.tracker.pane_metrics(finished[0], 1, self.connections)
        self.assertEqual((metrics['tool_completions'], metrics['active_tools'], metrics['tools_per_minute']), (3, 1, 1))
        self.assertEqual(metrics['tools_epoch'], 0)

    def test_tool_activity_is_unknown_offline_stale_missing_or_incomplete(self):
        rows = [row(usage(active_tools=1))]
        self.observe(rows, 0)
        for changed, now, health in (
                (dict(rows[0], available=False), 0, self.connections),
                (dict(rows[0], state='missing'), 0, self.connections),
                (rows[0], 3, self.connections),
                (rows[0], 0, [dict(id='remote', connected=False)])):
            metrics = self.tracker.pane_metrics(changed, now, health)
            self.assertIsNone(metrics['active_tools'])
            self.assertEqual(metrics['tool_completions'], 2)  # Retained cumulative accounting.
        partial = [row(usage(active_tools=9, tool_completions=999, tools_complete=False))]
        self.observe(partial, 1)
        metrics = self.tracker.pane_metrics(partial[0], 1, self.connections)
        self.assertIsNone(metrics['active_tools'])
        self.assertEqual(metrics['tool_completions'], 2)
        self.assertIsNone(metrics['tools_per_minute'])
        self.assertEqual(metrics['tools_epoch'], 1)

    def test_tool_epoch_resets_reconnect_counter_drop_and_does_not_change_on_redraw(self):
        rows = [row(usage(active_tools=1))]
        self.observe(rows, 0)
        self.observe([row(usage(tools=3, active_tools=0))], 1)
        health = [dict(id='remote', connected=False)]
        self.tracker.aggregate(rows, 1.1, health)
        first_epoch = self.tracker.pane_metrics(rows[0], 1.1, health)['tools_epoch']
        for tenth in range(2, 10):
            self.tracker.aggregate(rows, 1 + tenth / 10, health)
        self.assertEqual(self.tracker.pane_metrics(rows[0], 1.9, health)['tools_epoch'], first_epoch)
        resumed = [row(usage(tools=100, active_tools=2))]
        self.observe(resumed, 2, epoch=2)
        metrics = self.tracker.pane_metrics(resumed[0], 2, self.connections)
        self.assertEqual((metrics['tool_completions'], metrics['active_tools'], metrics['tools_epoch']), (100, 2, first_epoch))
        self.assertIsNone(metrics['tools_per_minute'])
        reset = [row(usage(tools=0, active_tools=0))]
        self.observe(reset, 3, epoch=2)
        metrics = self.tracker.pane_metrics(reset[0], 3, self.connections)
        self.assertEqual((metrics['tool_completions'], metrics['active_tools'], metrics['tools_epoch']), (0, 0, first_epoch + 1))
        self.assertIsNone(metrics['tools_per_minute'])

    def test_unknown_tool_activity_never_becomes_zero_and_complete_duplicate_wins(self):
        for value in (None, -1, True, 1.5):
            rows = [row(usage(active_tools=value))]
            self.observe(rows, 0)
            self.assertIsNone(self.tracker.pane_metrics(rows[0], 0, self.connections)['active_tools'])
        rows = [row(usage(active_tools=None)), row(usage(active_tools=2), pane='%2')]
        self.observe(rows, 1)
        for item in rows:
            metrics = self.tracker.pane_metrics(item, 1, self.connections)
            self.assertEqual(metrics['active_tools'], 2)
            self.assertIs(type(metrics['tools_epoch']), int)
            json.dumps(metrics, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
