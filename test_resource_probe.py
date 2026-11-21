import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from resource_probe import AsyncProcessResources, ProcessResources, parse_tcp_stats


def socket_stats(inode=101, cookie='a001', rx=100, tx=200, kind='sent', pid=None):
    users = ' users:(("codex",pid={},fd=5))'.format(pid) if pid else ''
    fields = []
    if rx is not None:
        fields.append('bytes_received:' + str(rx))
    if tx is not None:
        fields.append('bytes_{}:{}'.format(kind, tx))
    return ('ESTAB 0 0 192.0.2.1:1000 198.51.100.2:443{} ino:{} sk:{}\n'
            ' cubic rto:200 {}\n').format(users, inode, cookie, ' '.join(fields))


class ResourceProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name)
        self.output, self.calls, self.options = '', [], []
        (self.proc / 'self/ns').mkdir(parents=True)
        (self.proc / 'self/ns/net').symlink_to('net:[1000]')
        self.processes = {10: (1, 'S', '/bin/codex')}
        self.pid(10)
        def runner(command, **kwargs):
            self.calls.append(command)
            self.options.append(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout=self.output, stderr='')
        self.probe = ProcessResources(proc_root=self.proc, platform_name='linux',
                                      runner=runner, page_size=4096, nonce='fixture')

    def pid(self, pid, start=123, rss=2, rchar=1000, wchar=500, read=100, write=50):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / 'fd').mkdir(exist_ok=True)
        (directory / 'ns').mkdir(exist_ok=True)
        if not (directory / 'ns/net').is_symlink():
            (directory / 'ns/net').symlink_to('net:[1000]')
        fields = ['S', str(self.processes.get(pid, (1,))[0])] + ['0'] * 17 + [str(start)] + ['0'] * 20
        (directory / 'stat').write_text('{} (codex (fixture)) '.format(pid) + ' '.join(fields))
        (directory / 'statm').write_text('100 {} 0 0 0 0 0\n'.format(rss))
        (directory / 'io').write_text('rchar: {}\nwchar: {}\nread_bytes: {}\nwrite_bytes: {}\n'.format(rchar, wchar, read, write))

    def fd(self, pid, inode=101, fd=5):
        path = self.proc / str(pid) / 'fd' / str(fd)
        if path.is_symlink():
            path.unlink()
        path.symlink_to('socket:[{}]'.format(inode))

    def sample(self, now, roots=(10,)):
        return self.probe.sample_all(roots, self.processes, now)

    def test_memory_tree_includes_unmonitored_child_agents_but_excludes_zombies(self):
        self.processes.update({11: (10, 'S', '/bin/python'),
                               12: (10, 'S', '/bin/claude'),
                               13: (12, 'S', '/bin/node'),
                               14: (10, 'Z', '/bin/bash')})
        for pid in range(11, 15):
            self.pid(pid, rss=1)
        row = self.sample(0)[10]
        self.assertEqual(row['process_count'], 4)
        self.assertEqual(row['memory_bytes'], 5 * 4096)
        self.assertTrue(row['memory_complete'])
        self.assertEqual(row['memory_kind'], 'rss_sum')

    def test_separately_selected_child_agent_is_a_resource_tree_boundary(self):
        self.processes.update({11: (10, 'S', '/bin/python'),
                               12: (10, 'S', '/bin/claude'),
                               13: (12, 'S', '/bin/node')})
        for pid in (11, 12, 13):
            self.pid(pid, rss=1)
        rows = self.sample(0, (10, 12))
        self.assertEqual(rows[10]['process_count'], 2)
        self.assertEqual(rows[10]['memory_bytes'], 3 * 4096)
        self.assertEqual(rows[12]['process_count'], 2)
        self.assertEqual(rows[12]['memory_bytes'], 2 * 4096)

    def test_io_is_root_only_and_initial_history_is_not_new_activity(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(11, rchar=100000)
        first = self.sample(0)[10]
        self.assertEqual(first['io_read_bytes'], 0)
        self.assertIsNone(first['io_read_per_second'])
        self.pid(10, rchar=1020, wchar=510, read=102, write=53)
        self.pid(11, rchar=200000)
        second = self.sample(1)[10]
        self.assertEqual(second['io_read_bytes'], 20)
        self.assertEqual(second['io_write_bytes'], 10)
        self.assertEqual(second['disk_read_bytes'], 2)
        self.assertEqual(second['disk_write_bytes'], 3)
        self.assertEqual(second['io_read_per_second'], 20)
        self.assertEqual(second['io_scope'], 'root_process_with_reaped_children')

    def test_root_pid_reuse_and_collector_restart_change_identity(self):
        first = self.sample(0)[10]
        self.pid(10, start=999, rchar=99999)
        reused = self.sample(1)[10]
        self.assertNotEqual(first['identity'], reused['identity'])
        self.assertEqual(reused['io_read_bytes'], 0)
        self.assertIsNone(reused['io_read_per_second'])
        other = ProcessResources(proc_root=self.proc, platform_name='linux',
                                 runner=self.probe.runner, page_size=4096, nonce='new-collector')
        self.assertNotEqual(other.sample_all([10], self.processes, 0)[10]['identity'], reused['identity'])

    def test_sampling_is_shared_throttled_and_returned_rows_are_copies(self):
        first = self.sample(0, (10, 10))
        first[10]['memory_bytes'] = -1
        second = self.sample(.5)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second[10]['sample_id'], 1)
        self.assertEqual(second[10]['memory_bytes'], 8192)
        self.assertEqual(self.sample(1)[10]['sample_id'], 2)
        self.assertEqual(len(self.calls), 2)

    def test_tcp_counts_only_owned_socket_deltas_without_users_pid_metadata(self):
        self.fd(10)
        self.output = socket_stats() + socket_stats(999, 'ff99', 1000000, 2000000)
        first = self.sample(0)[10]
        self.assertEqual(first['network_rx_bytes'], 0)
        self.assertIsNone(first['network_rx_per_second'])
        self.output = socket_stats(rx=140, tx=230) + socket_stats(999, 'ff99', 9999999, 9999999)
        second = self.sample(1)[10]
        self.assertEqual(second['network_rx_bytes'], 40)
        self.assertEqual(second['network_tx_bytes'], 30)
        self.assertEqual(second['network_tx_per_second'], 30)
        self.assertTrue(second['network_complete'])
        self.assertNotIn('192.0.2.1', json.dumps(second))

    def test_shared_socket_is_not_duplicated_into_two_session_ledgers(self):
        self.processes[20] = (1, 'S', '/bin/claude')
        self.pid(20)
        self.fd(10); self.fd(20)
        self.output = socket_stats()
        first = self.sample(0, (10, 20))
        self.output = socket_stats(rx=5000, tx=9000)
        second = self.sample(1, (10, 20))
        self.assertEqual(len(self.calls), 2)  # One ss call per whole sample, not per root.
        for row in list(first.values()) + list(second.values()):
            self.assertTrue(row['network_shared'])
            self.assertFalse(row['network_complete'])
            self.assertFalse(row['network_available'])
            self.assertIsNone(row['network_rx_bytes'])
            self.assertIsNone(row['network_tx_per_second'])

    def test_shared_and_exclusive_sockets_report_only_known_partial_delta(self):
        self.processes[20] = (1, 'S', '/bin/claude')
        self.pid(20); self.fd(10); self.fd(20)
        self.fd(10, 202, fd=6)
        self.output = socket_stats() + socket_stats(202, 'bb02', 10, 20)
        self.sample(0, (10, 20))
        self.output = socket_stats(rx=99999, tx=99999) + socket_stats(202, 'bb02', 40, 60)
        row = self.sample(1, (10, 20))[10]
        self.assertTrue(row['network_available'])
        self.assertFalse(row['network_complete'])
        self.assertEqual(row['network_rx_bytes'], 30)
        self.assertEqual(row['network_tx_bytes'], 40)

    def test_socket_close_and_new_cookie_do_not_count_existing_history(self):
        self.fd(10); self.output = socket_stats()
        self.sample(0)
        self.output = socket_stats(rx=120, tx=230)
        self.assertEqual(self.sample(1)[10]['network_rx_bytes'], 20)
        self.output = ''
        self.assertEqual(self.sample(2)[10]['network_rx_per_second'], 0)
        self.output = socket_stats(cookie='be01', rx=10000, tx=20000)
        self.assertEqual(self.sample(3)[10]['network_rx_bytes'], 20)
        self.output = socket_stats(cookie='be01', rx=10040, tx=20050)
        row = self.sample(4)[10]
        self.assertEqual(row['network_rx_bytes'], 60)
        self.assertEqual(row['network_tx_bytes'], 80)

    def test_io_permission_gap_does_not_backfill_unobserved_consumption(self):
        self.sample(0)
        (self.proc / '10/io').write_text('rchar: malformed\n')
        missing = self.sample(1)[10]
        self.assertFalse(missing['io_available'])
        self.assertIsNone(missing['io_read_bytes'])
        self.pid(10, rchar=99999)
        self.assertEqual(self.sample(2)[10]['io_read_bytes'], 0)
        self.pid(10, rchar=100009)
        self.assertEqual(self.sample(3)[10]['io_read_bytes'], 10)

    def test_missing_proc_and_fd_access_are_unknown_not_zero(self):
        (self.proc / '10/statm').unlink()
        row = self.sample(0)[10]
        self.assertIsNone(row['memory_bytes'])
        self.assertFalse(row['memory_complete'])
        self.assertIsNone(row['network_rx_bytes'])
        self.pid(10)
        original = self.probe._fds
        self.probe._fds = lambda pid, budget: (set(), False)
        self.addCleanup(setattr, self.probe, '_fds', original)
        row = self.sample(1)[10]
        self.assertEqual(row['memory_bytes'], 8192)
        self.assertFalse(row['network_available'])
        self.assertIsNone(row['network_rx_bytes'])

    def test_missing_tcp_counter_is_partial_and_never_filled_from_interface_bytes(self):
        self.fd(10)
        self.output = socket_stats(rx=None, tx=200)
        first = self.sample(0)[10]
        self.assertFalse(first['network_complete'])
        self.assertIsNone(first['network_rx_bytes'])
        self.assertEqual(first['network_tx_bytes'], 0)
        self.output = socket_stats(rx=None, tx=250)
        second = self.sample(1)[10]
        self.assertEqual(second['network_tx_per_second'], 50)
        self.assertIsNone(second['network_rx_per_second'])
        self.assertEqual(second['network_scope'], 'tcp_process_tree')

    def test_counter_reset_or_late_counter_establishes_new_rate_baseline(self):
        self.fd(10)
        self.output = socket_stats(rx=None, tx=200)
        self.sample(0)
        self.output = socket_stats(rx=500, tx=20)
        row = self.sample(1)[10]
        self.assertIsNone(row['network_rx_per_second'])
        self.assertIsNone(row['network_tx_per_second'])
        self.output = socket_stats(rx=530, tx=60)
        row = self.sample(2)[10]
        self.assertEqual(row['network_rx_per_second'], 30)
        self.assertEqual(row['network_tx_per_second'], 40)

    def test_unsupported_or_timed_out_ss_does_not_break_memory_or_io(self):
        for error in [FileNotFoundError(), subprocess.TimeoutExpired(['ss'], .7)]:
            def runner(command, **kwargs):
                raise error
            self.probe.runner = runner
            self.probe.next_sample = -1
            row = self.sample(0)[10]
            self.assertTrue(row['memory_available'])
            self.assertTrue(row['io_available'])
            self.assertFalse(row['network_available'])
            self.assertIsNone(row['network_rx_bytes'])

    def test_gap_clock_reset_and_counter_reset_establish_fresh_baselines(self):
        self.sample(0)
        self.pid(10, rchar=1100)
        self.assertEqual(self.sample(1)[10]['io_read_bytes'], 100)
        self.pid(10, rchar=99999)
        after_gap = self.sample(10)[10]
        self.assertEqual(after_gap['io_read_bytes'], 100)
        self.assertIsNone(after_gap['io_read_per_second'])
        self.pid(10, rchar=10)
        reset = self.sample(11)[10]
        self.assertEqual(reset['io_read_bytes'], 100)
        self.assertIsNone(reset['io_read_per_second'])
        self.assertIsNone(self.sample(5)[10]['io_read_per_second'])

    def test_process_budget_marks_partial_and_never_scans_unbounded_tree(self):
        self.processes.update({11: (10, 'S', '/bin/bash'), 12: (10, 'S', '/bin/python')})
        self.pid(11); self.pid(12)
        self.probe.max_processes = 2
        row = self.sample(0)[10]
        self.assertEqual(row['process_count'], 2)
        self.assertFalse(row['tree_complete'])
        self.assertFalse(row['memory_complete'])
        self.assertFalse(row['network_complete'])

    def test_fd_budget_marks_network_unknown_instead_of_reporting_zero(self):
        self.fd(10)
        self.probe.max_total_fds = 0
        row = self.sample(0)[10]
        self.assertFalse(row['network_available'])
        self.assertIsNone(row['network_rx_bytes'])

    def test_no_selected_processes_does_not_spawn_resource_commands(self):
        self.assertEqual(self.sample(0, ()), {})
        self.assertEqual(self.calls, [])

    def test_cross_network_namespace_cannot_be_reported_as_zero_tcp_traffic(self):
        namespace = self.proc / '10/ns/net'
        namespace.unlink(); namespace.symlink_to('net:[2000]')
        self.output = ''  # ss only sees the collector's namespace.
        row = self.sample(0)[10]
        self.assertTrue(row['memory_available'])
        self.assertTrue(row['io_available'])
        self.assertFalse(row['network_available'])
        self.assertFalse(row['network_complete'])
        self.assertIsNone(row['network_rx_bytes'])
        self.assertIsNone(row['network_tx_bytes'])
        self.assertEqual(row['network_reason'], 'namespace_mismatch')

    def test_unknown_root_or_collector_namespace_keeps_tcp_unknown(self):
        for path in (self.proc / '10/ns/net', self.proc / 'self/ns/net'):
            with self.subTest(namespace=str(path.relative_to(self.proc))):
                original = path.readlink()
                path.unlink()
                self.probe.next_sample = -1
                row = self.sample(0)[10]
                self.assertFalse(row['network_available'])
                self.assertIsNone(row['network_rx_bytes'])
                self.assertEqual(row['network_reason'], 'namespace_unavailable')
                path.symlink_to(original)

    def test_cross_namespace_child_keeps_partial_tree_network_unknown(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(11, rss=1)
        namespace = self.proc / '11/ns/net'
        namespace.unlink(); namespace.symlink_to('net:[2000]')
        self.fd(10); self.output = socket_stats()
        row = self.sample(0)[10]
        self.assertEqual(row['memory_bytes'], 3 * 4096)
        self.assertTrue(row['memory_complete'])
        self.assertFalse(row['network_available'])
        self.assertIsNone(row['network_rx_bytes'])
        self.assertEqual(row['network_reason'], 'namespace_mismatch')

    def test_resource_commands_use_fixed_locale_and_bounded_timeout(self):
        with patch.dict('os.environ', {'ORBIT_RESOURCE_TEST': 'retained'}):
            self.sample(0)
        self.assertEqual(self.options[-1]['timeout'], .35)
        self.assertEqual(self.options[-1]['env']['LC_ALL'], 'C')
        self.assertEqual(self.options[-1]['env']['ORBIT_RESOURCE_TEST'], 'retained')
        self.probe.platform = 'darwin'
        self.output = '10 Thu Nov 20 10:00:00 2026 100\n'
        self.sample(1)
        self.assertEqual(self.calls[-1][0], 'ps')
        self.assertEqual(self.options[-1]['timeout'], .35)
        self.assertEqual(self.options[-1]['env']['LC_ALL'], 'C')

    def test_missing_process_table_limits_scope_instead_of_claiming_complete_tree(self):
        self.processes = None
        row = self.sample(0)[10]
        self.assertEqual(row['memory_bytes'], 8192)
        self.assertFalse(row['tree_complete'])
        self.assertFalse(row['memory_complete'])
        self.assertFalse(row['network_available'])

    def test_darwin_uses_rss_only_and_does_not_fake_linux_metrics(self):
        self.output = '10 Thu Nov 20 10:00:00 2026 100\n11 Thu Nov 20 10:00:01 2026 50\n'
        self.processes[11] = (10, 'S', '/usr/bin/python')
        self.probe.platform = 'darwin'
        row = self.sample(0)[10]
        self.assertEqual(row['memory_bytes'], 150 * 1024)
        self.assertTrue(row['memory_complete'])
        self.assertEqual(self.calls[0][0], 'ps')
        self.assertEqual(self.calls[0][2], '10,11')
        self.assertIsNone(row['io_read_bytes'])
        self.assertIsNone(row['network_rx_bytes'])
        self.assertEqual(row['network_reason'], 'unsupported')


class SocketParserTests(unittest.TestCase):
    def test_parser_merges_multiline_counters_prefers_sent_and_keeps_only_metadata(self):
        text = socket_stats(rx=12, tx=15, kind='acked', pid=10) + ' bytes_sent:19\n'
        rows, complete = parse_tcp_stats(text)
        self.assertTrue(complete)
        self.assertEqual(rows, {(101, 'a001'): dict(rx=12, tx=19, tx_kind='sent')})

    def test_inode_without_cookie_is_not_a_stable_socket_identity(self):
        rows, complete = parse_tcp_stats('ESTAB 0 0 local remote ino:101\n bytes_received:10 bytes_sent:20\n')
        self.assertFalse(complete)
        self.assertEqual(rows, {})

    def test_truncated_socket_listing_is_explicitly_partial(self):
        rows, complete = parse_tcp_stats(socket_stats() * 2, max_lines=1)
        self.assertFalse(complete)
        self.assertEqual(rows[(101, 'a001')]['rx'], None)


class AsyncResourceProbeTests(unittest.TestCase):
    def test_slow_resource_worker_never_blocks_state_snapshots_or_duplicates_workers(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class SlowCollector:
            calls = 0
            def sample_all(self, roots, processes, now=None):
                self.calls += 1
                started.set()
                release.wait()
                return {pid: dict(memory_bytes=1234, sample_id=self.calls) for pid in roots}
        collector = SlowCollector()
        clock = [0.0]
        probe = AsyncProcessResources(collector=collector, clock=lambda: clock[0])
        before = time.monotonic()
        self.assertEqual(probe.sample_all([10, 20], {}), {})
        self.assertLess(time.monotonic() - before, .1)
        self.assertTrue(started.wait(.1))
        clock[0] = 3
        before = time.monotonic()
        for _ in range(5):
            self.assertEqual(probe.sample_all([10], {}), {})
        self.assertLess(time.monotonic() - before, .1)
        self.assertEqual(collector.calls, 1)
        release.set(); probe._worker.join(.1)
        self.assertFalse(probe._worker.is_alive())
        # No new worker is eligible at the initial timestamp; only selected roots return.
        clock[0] = .5
        rows = probe.sample_all([10], {})
        self.assertEqual(set(rows), {10})
        self.assertEqual(rows[10]['memory_bytes'], 1234)
        self.assertEqual(rows[10]['sample_age_seconds'], .5)
        self.assertEqual(collector.calls, 1)
        rows[10]['memory_bytes'] = -1
        self.assertEqual(probe.sample_all([10], {})[10]['memory_bytes'], 1234)
        clock[0] = 5
        self.assertEqual(probe.sample_all([10], {}), {})  # Stale values are hidden immediately.
        probe._worker.join(.1)

    def test_failed_worker_clears_resource_cache_without_raising_snapshot_error(self):
        class BrokenCollector:
            def sample_all(self, roots, processes, now=None):
                raise RuntimeError('resource command failed')
        probe = AsyncProcessResources(collector=BrokenCollector())
        self.assertEqual(probe.sample_all([10], {}, now=0), {})
        probe._worker.join(.1)
        self.assertFalse(probe._busy)
        self.assertEqual(probe.sample_all([10], {}, now=.5), {})

    def test_no_roots_does_not_start_worker(self):
        probe = AsyncProcessResources()
        self.assertEqual(probe.sample_all([], {}, now=0), {})
        self.assertIsNone(probe._worker)


if __name__ == '__main__':
    unittest.main()
