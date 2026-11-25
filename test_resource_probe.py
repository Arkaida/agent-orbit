import json
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import threading
import time
import unittest
from unittest.mock import patch

from process_snapshot import ProcessSnapshot, as_snapshot
from resource_probe import AsyncProcessResources, ProcessResources, parse_tcp_stats, parse_gpu_stats


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
        self.gpu_output, self.gpu_calls, self.gpu_options = '', [], []
        (self.proc / 'self/ns').mkdir(parents=True)
        (self.proc / 'self/ns/net').symlink_to('net:[1000]')
        self.processes = {10: (1, 'S', '/bin/codex')}
        self.pid(10)
        def runner(command, **kwargs):
            if command[0] == 'nvidia-smi':
                self.gpu_calls.append(command)
                self.gpu_options.append(kwargs)
                return subprocess.CompletedProcess(command, 0, stdout=self.gpu_output, stderr='')
            self.calls.append(command)
            self.options.append(kwargs)
            return subprocess.CompletedProcess(command, 0, stdout=self.output, stderr='')
        self.probe = ProcessResources(proc_root=self.proc, platform_name='linux',
                                      runner=runner, page_size=4096, nonce='fixture', clock_ticks=100)

    def stat(self, pid, start=123, utime=0, stime=0, cutime=0, cstime=0, threads=1):
        fields = ['0'] * 40
        fields[0], fields[1] = 'S', str(self.processes.get(pid, (1,))[0])
        for index, value in ((11, utime), (12, stime), (13, cutime), (14, cstime), (17, threads), (19, start)):
            fields[index] = str(value)
        return '{} (codex (fixture)) '.format(pid) + ' '.join(fields)

    def pid(self, pid, start=123, rss=2, rchar=1000, wchar=500, read=100, write=50,
            utime=0, stime=0, cutime=0, cstime=0, threads=1):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / 'fd').mkdir(exist_ok=True)
        (directory / 'ns').mkdir(exist_ok=True)
        if not (directory / 'ns/net').is_symlink():
            (directory / 'ns/net').symlink_to('net:[1000]')
        (directory / 'stat').write_text(self.stat(pid, start, utime, stime, cutime, cstime, threads))
        (directory / 'statm').write_text('100 {} 0 0 0 0 0\n'.format(rss))
        (directory / 'io').write_text('rchar: {}\nwchar: {}\nread_bytes: {}\nwrite_bytes: {}\n'.format(rchar, wchar, read, write))
        self.thread(pid, pid, start, rchar, wchar, read, write)

    def thread(self, pid, tid, start=123, rchar=1000, wchar=500, read=100, write=50):
        directory = self.proc / str(pid) / 'task' / str(tid)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'stat').write_text(self.stat(pid, start))
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

    def test_io_includes_live_child_threads_and_initial_history_is_not_new_activity(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(11, rchar=100000)
        first = self.sample(0)[10]
        self.assertEqual(first['io_read_bytes'], 0)
        self.assertIsNone(first['io_read_per_second'])
        self.pid(10, rchar=1020, wchar=510, read=102, write=53)
        self.pid(11, rchar=200000)
        second = self.sample(1)[10]
        self.assertEqual(second['io_read_bytes'], 100020)
        self.assertEqual(second['io_write_bytes'], 10)
        self.assertEqual(second['disk_read_bytes'], 2)
        self.assertEqual(second['disk_write_bytes'], 3)
        self.assertEqual(second['io_read_per_second'], 100020)
        self.assertEqual(second['io_scope'], 'observed_process_tree_threads')
        self.assertTrue(second['io_lower_bound'])
        self.assertEqual(second['io_accounting'], 'live_threads_no_reaped_children')

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

    def test_incomplete_peer_fd_scan_does_not_prove_socket_exclusivity(self):
        self.processes[20] = (1, 'S', '/bin/claude')
        self.pid(20)
        self.fd(10); self.fd(20)
        self.probe.max_total_fds = 1
        self.output = socket_stats()
        self.sample(0, (10, 20))
        self.output = socket_stats(rx=500, tx=800)
        row = self.sample(1, (10, 20))[10]
        self.assertFalse(row['network_available'])
        self.assertFalse(row['network_complete'])
        self.assertTrue(row['network_attribution_unknown'])
        self.assertEqual(row['network_reason'], 'ownership_unknown')
        self.assertIsNone(row['network_rx_bytes'])
        self.assertIsNone(row['network_rx_per_second'])
        self.probe.max_total_fds = 4096
        (self.proc / '20/fd/5').unlink()
        resumed = self.sample(2, (10, 20))[10]
        self.assertTrue(resumed['network_complete'])
        self.assertEqual(resumed['network_rx_bytes'], 0)
        self.assertIsNone(resumed['network_rx_per_second'])

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
        (self.proc / '10/task/10/io').write_text('rchar: malformed\n')
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
        self.assertEqual(row['network_rx_bytes'], 0)
        self.assertTrue(row['network_available'])
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

    def test_darwin_legacy_ps_rows_keep_rss_and_unknown_cpu_instead_of_fake_metrics(self):
        self.output = '10 Thu Nov 20 10:00:00 2026 100\n11 Thu Nov 20 10:00:01 2026 50\n'
        self.processes[11] = (10, 'S', '/usr/bin/python')
        self.probe.platform = 'darwin'
        row = self.sample(0)[10]
        self.assertEqual(row['memory_bytes'], 100 * 1024)
        self.assertFalse(row['memory_complete'])
        self.assertEqual(self.calls[0][0], 'ps')
        self.assertEqual(self.calls[0][2], '10,11')
        self.assertIsNone(row['io_read_bytes'])
        self.assertIsNone(row['network_rx_bytes'])
        self.assertEqual(row['network_reason'], 'unsupported')

    def test_cpu_observes_tree_user_and_system_time_but_not_waited_children(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(10, utime=1000, stime=200, cutime=10000, cstime=10000)
        self.pid(11, utime=400, stime=100)
        first = self.sample(0)[10]
        self.assertEqual(first['cpu_seconds'], 0)
        self.assertIsNone(first['cpu_percent'])
        self.assertFalse(first['cpu_complete'])
        self.pid(10, utime=1050, stime=225, cutime=20000, cstime=30000)
        self.pid(11, utime=550, stime=125)
        second = self.sample(1)[10]
        self.assertEqual(second['cpu_seconds'], 2.5)
        self.assertEqual(second['cpu_percent'], 250)
        self.assertEqual(second['cpu_covered'], 2)
        self.assertTrue(second['cpu_complete'])
        self.assertEqual(second['cpu_scope'], 'process_tree')

    def test_cpu_new_child_pid_reuse_and_exit_never_replay_lifetime_time(self):
        self.pid(10, utime=100)
        self.sample(0)
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(10, utime=110)
        self.pid(11, start=200, utime=999999)
        new = self.sample(1)[10]
        self.assertEqual(new['cpu_seconds'], .1)
        self.assertEqual(new['cpu_percent'], 10)
        self.assertFalse(new['cpu_complete'])
        self.pid(10, utime=120)
        self.pid(11, start=300, utime=9999999)
        reused = self.sample(2)[10]
        self.assertEqual(reused['cpu_seconds'], .2)
        self.assertFalse(reused['cpu_complete'])
        self.processes.pop(11)
        self.pid(10, utime=130, cutime=10000000)
        exited = self.sample(3)[10]
        self.assertEqual(exited['cpu_seconds'], .3)
        self.assertFalse(exited['cpu_complete'])
        self.pid(10, utime=140)
        stable = self.sample(4)[10]
        self.assertEqual(stable['cpu_seconds'], .4)
        self.assertTrue(stable['cpu_complete'])

    def test_io_child_reaped_totals_are_not_recounted_on_root(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(10, rchar=1000)
        self.pid(11, rchar=90000)
        self.sample(0)
        self.pid(10, rchar=1010)
        self.pid(11, rchar=90040)
        active = self.sample(1)[10]
        self.assertEqual(active['io_read_bytes'], 50)
        self.processes.pop(11)
        shutil.rmtree(self.proc / '11')
        self.pid(10, rchar=1020)
        # Kernel process-wide I/O now includes the waited-for child's lifetime.
        (self.proc / '10/io').write_text('rchar: 91060\nwchar: 1000\nread_bytes: 200\nwrite_bytes: 100\n')
        exited = self.sample(2)[10]
        self.assertEqual(exited['io_read_bytes'], 60)
        self.assertEqual(exited['io_read_per_second'], 10)
        self.assertFalse(exited['io_complete'])
        self.pid(10, rchar=1030)
        self.assertEqual(self.sample(3)[10]['io_read_bytes'], 70)

    def test_io_new_thread_exit_and_tid_reuse_are_fresh_baselines(self):
        self.pid(10, threads=2)
        self.thread(10, 12, start=200, rchar=90000)
        self.sample(0)
        self.pid(10, threads=2, rchar=1010)
        self.thread(10, 12, start=200, rchar=90030)
        row = self.sample(1)[10]
        self.assertEqual(row['io_read_bytes'], 40)
        self.assertTrue(row['io_complete'])
        self.pid(10, threads=2, rchar=1020)
        self.thread(10, 12, start=201, rchar=9999999)
        row = self.sample(2)[10]
        self.assertEqual(row['io_read_bytes'], 50)
        self.assertFalse(row['io_complete'])
        shutil.rmtree(self.proc / '10/task/12')
        self.pid(10, threads=1, rchar=1030)
        row = self.sample(3)[10]
        self.assertEqual(row['io_read_bytes'], 60)
        self.assertEqual(row['io_threads_covered'], 1)
        self.assertFalse(row['io_complete'])
        self.pid(10, rchar=1040)
        self.assertTrue(self.sample(4)[10]['io_complete'])

    def test_thread_budget_bounds_io_without_disabling_cpu_memory_or_network(self):
        self.pid(10, threads=2, utime=10)
        self.thread(10, 11, start=200)
        self.probe.max_total_threads = 1
        self.sample(0)
        self.pid(10, threads=2, utime=20, rchar=1020)
        row = self.sample(1)[10]
        self.assertEqual(row['io_threads_covered'], 1)
        self.assertFalse(row['io_complete'])
        self.assertEqual(row['cpu_percent'], 10)
        self.assertTrue(row['cpu_complete'])
        self.assertTrue(row['memory_complete'])
        self.assertTrue(row['network_available'])

    def test_io_missing_task_directory_remains_unknown_not_process_counter_fallback(self):
        shutil.rmtree(self.proc / '10/task')
        row = self.sample(0)[10]
        self.assertFalse(row['io_available'])
        self.assertIsNone(row['io_read_bytes'])
        self.assertTrue(row['cpu_available'])
        self.assertTrue(row['memory_available'])

    def test_cpu_and_io_long_gap_and_permissions_never_backfill(self):
        self.pid(10, utime=100, rchar=1000)
        self.sample(0)
        self.pid(10, utime=110, rchar=1010)
        self.sample(1)
        self.pid(10, utime=999999, rchar=999999)
        gap = self.sample(10)[10]
        self.assertEqual(gap['cpu_seconds'], .1)
        self.assertIsNone(gap['cpu_percent'])
        self.assertEqual(gap['io_read_bytes'], 10)
        self.assertIsNone(gap['io_read_per_second'])
        self.pid(10, utime=1000009, rchar=1000009)
        row = self.sample(11)[10]
        self.assertAlmostEqual(row['cpu_seconds'], .2, places=5)
        self.assertEqual(row['io_read_bytes'], 20)
        (self.proc / '10/task/10/io').unlink()
        self.assertFalse(self.sample(12)[10]['io_available'])
        self.pid(10, utime=1000019, rchar=2000000)
        resumed = self.sample(13)[10]
        self.assertEqual(resumed['io_read_bytes'], 20)
        self.assertIsNone(resumed['io_read_per_second'])

    def test_changed_process_identity_during_sample_discards_all_metrics(self):
        original = self.probe._thread_io
        def race(pid, start, expected, budget):
            value = original(pid, start, expected, budget)
            self.pid(pid, start=999)
            return value
        self.probe._thread_io = race
        row = self.sample(0)[10]
        self.assertFalse(row['cpu_available'])
        self.assertFalse(row['io_available'])
        self.assertFalse(row['memory_available'])
        self.assertFalse(row['network_available'])

    def test_reparented_child_is_not_attributed_from_stale_tree(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.processes[12] = (11, 'S', '/bin/python')
        self.pid(11)
        self.pid(12)
        # Use the parser's final-parenthesis boundary, matching a reparent to init.
        path = self.proc / '11/stat'
        text = path.read_text(); closing = text.rfind(')'); tail = text[closing+1:].split(); tail[1] = '1'
        path.write_text(text[:closing+1]+' '+' '.join(tail))
        row = self.sample(0)[10]
        self.assertEqual(row['cpu_covered'], 1)
        self.assertEqual(row['io_covered'], 1)
        self.assertFalse(row['cpu_complete'])
        self.assertFalse(row['memory_complete'])

    def test_failed_ancestor_excludes_descendants_from_every_resource(self):
        self.processes.update({11: (10, 'S', '/bin/python'),
                               12: (11, 'S', '/bin/python')})
        original = self.probe._thread_io
        for failure in ('missing_stat', 'identity_changed'):
            with self.subTest(failure=failure):
                self.pid(11)
                self.pid(12, rss=999, utime=99999, rchar=999999)
                self.fd(12)
                self.output = socket_stats()
                self.probe._thread_io = original
                if failure == 'missing_stat':
                    (self.proc / '11/stat').unlink()
                else:
                    def race(pid, start, expected, budget):
                        value = original(pid, start, expected, budget)
                        if pid == 11:
                            self.pid(11, start=999)
                        return value
                    self.probe._thread_io = race
                self.probe.next_sample = -1
                row = self.sample(0)[10]
                self.assertEqual(row['cpu_covered'], 1)
                self.assertEqual(row['io_covered'], 1)
                self.assertEqual(row['memory_covered'], 1)
                self.assertEqual(row['memory_bytes'], 2 * 4096)
                self.assertFalse(row['memory_complete'])
                self.assertFalse(row['network_available'])
                self.assertIsNone(row['network_rx_bytes'])

    def test_missing_memory_or_fds_does_not_hide_independent_metrics(self):
        for failure in ('statm', 'fd'):
            with self.subTest(failure=failure):
                self.pid(10, utime=100, rchar=1000)
                self.sample(0)
                self.pid(10, utime=110, rchar=1010)
                path = self.proc / '10' / failure
                if failure == 'fd':
                    shutil.rmtree(path)
                else:
                    path.unlink()
                self.probe.next_sample = -1
                row = self.sample(1)[10]
                self.assertTrue(row['cpu_available'])
                self.assertEqual(row['cpu_percent'], 10)
                self.assertTrue(row['io_available'])
                self.assertEqual(row['io_read_per_second'], 10)
                if failure == 'statm':
                    self.assertFalse(row['memory_available'])
                    self.assertTrue(row['network_available'])
                else:
                    self.assertTrue(row['memory_available'])
                    self.assertFalse(row['network_available'])
                self.pid(10)

    def test_root_reuse_while_reading_child_discards_old_tree_and_baselines(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(11)
        original = self.probe._thread_io
        def race(pid, start, expected, budget):
            value = original(pid, start, expected, budget)
            if pid == 11:
                self.pid(10, start=999, utime=999999, rchar=999999)
            return value
        self.probe._thread_io = race
        row = self.sample(0)[10]
        self.assertIsNone(row['identity'])
        self.assertFalse(row['cpu_available'])
        self.assertFalse(row['io_available'])
        self.assertFalse(row['network_available'])
        self.assertNotIn(10, self.probe.roots)
        self.probe._thread_io = original
        row = self.sample(1)[10]
        self.assertEqual(row['cpu_seconds'], 0)
        self.assertEqual(row['io_read_bytes'], 0)

    def test_tid_reuse_during_read_discards_only_that_thread(self):
        original = self.probe._read
        reads = [0]
        def race(pid, name):
            if name == 'task/10/stat':
                reads[0] += 1
                if reads[0] == 2:
                    return self.stat(10, start=999)
            return original(pid, name)
        self.probe._read = race
        row = self.sample(0)[10]
        self.assertFalse(row['io_available'])
        self.assertTrue(row['cpu_available'])
        self.assertTrue(row['memory_available'])

    def test_shared_snapshot_index_is_reused_for_all_resource_roots(self):
        self.processes[20] = (1, 'S', '/bin/claude')
        self.pid(20)
        snapshot = ProcessSnapshot(self.processes)
        with patch('resource_probe.as_snapshot', wraps=as_snapshot) as wrapper:
            self.probe.sample_all((10, 20), snapshot, 0)
        self.assertTrue(all(call.args[0] is snapshot for call in wrapper.call_args_list))

    def test_selected_child_is_excluded_from_cpu_and_io_parent_ledger(self):
        self.processes[11] = (10, 'S', '/bin/python')
        self.pid(10, utime=100, rchar=1000)
        self.pid(11, utime=100, rchar=1000)
        self.sample(0, (10, 11))
        self.pid(10, utime=110, rchar=1010)
        self.pid(11, utime=130, rchar=1050)
        rows = self.sample(1, (10, 11))
        self.assertEqual(rows[10]['cpu_percent'], 10)
        self.assertEqual(rows[11]['cpu_percent'], 30)
        self.assertEqual(rows[10]['io_read_bytes'], 10)
        self.assertEqual(rows[11]['io_read_bytes'], 50)

    def test_darwin_cpu_is_batched_tree_time_delta_with_stable_identity(self):
        self.probe.platform = 'darwin'
        self.processes[11] = (10, 'S', '/usr/bin/python')
        self.output = '10 Thu Nov 20 10:00:00 2026 100 0:01.00 1\n11 Thu Nov 20 10:00:01 2026 50 1:02.50 10\n'
        initial = self.sample(0)[10]
        self.assertIsNone(initial['cpu_percent'])
        self.assertEqual(initial['memory_bytes'], 150*1024)
        self.output = '10 Thu Nov 20 10:00:00 2026 100 0:01.25 1\n11 Thu Nov 20 10:00:01 2026 50 1:03.25 10\n'
        row = self.sample(1)[10]
        self.assertEqual(row['cpu_percent'], 100)
        self.assertEqual(row['cpu_seconds'], 1)
        self.assertTrue(row['cpu_complete'])
        self.assertEqual(self.calls[-1], ['ps', '-p', '10,11', '-o', 'pid=,lstart=,rss=,time=,ppid='])
        self.assertIsNone(row['io_read_bytes'])
        self.output = '10 Thu Nov 20 10:00:00 2026 100 0:01.50 1\n11 Thu Nov 20 11:00:01 2026 50 999:00.00 10\n'
        reused = self.sample(2)[10]
        self.assertEqual(reused['cpu_percent'], 25)
        self.assertEqual(reused['cpu_seconds'], 1.25)
        self.assertFalse(reused['cpu_complete'])

    def test_darwin_bad_cpu_counter_does_not_hide_known_memory(self):
        self.probe.platform = 'darwin'
        self.processes[11] = (10, 'S', '/usr/bin/python')
        self.output = '10 Thu Nov 20 10:00:00 2026 100 0:01.00 1\n11 Thu Nov 20 10:00:01 2026 50 invalid 10\n'
        row = self.sample(0)[10]
        self.assertEqual(row['memory_bytes'], 150 * 1024)
        self.assertTrue(row['memory_complete'])
        self.assertEqual(row['cpu_covered'], 1)
        self.assertFalse(row['cpu_complete'])

    def test_darwin_reparented_or_missing_ancestor_excludes_its_descendants(self):
        self.probe.platform = 'darwin'
        self.processes.update({11: (10, 'S', '/usr/bin/python'),
                               12: (11, 'S', '/usr/bin/python')})
        root = '10 Thu Nov 20 10:00:00 2026 100 0:01.00 1\n'
        grandchild = '12 Thu Nov 20 10:00:02 2026 999 9:00.00 11\n'
        for parent in ('11 Thu Nov 20 10:00:01 2026 50 1:02.50 1\n', ''):
            with self.subTest(parent=parent):
                self.output = root + parent + grandchild
                self.probe.next_sample = -1
                row = self.sample(0)[10]
                self.assertEqual(row['memory_bytes'], 100 * 1024)
                self.assertEqual(row['memory_covered'], 1)
                self.assertEqual(row['cpu_covered'], 1)
                self.assertFalse(row['memory_complete'])

    def test_darwin_reparented_root_is_not_published_under_stale_owner(self):
        self.probe.platform = 'darwin'
        self.output = '10 Thu Nov 20 10:00:00 2026 100 0:01.00 99\n'
        row = self.sample(0)[10]
        self.assertIsNone(row['identity'])
        self.assertFalse(row['memory_available'])
        self.assertFalse(row['cpu_available'])

    def test_cpu_time_formats_and_invalid_counters(self):
        self.assertEqual(self.probe._cpu_time('0:00.25'), .25)
        self.assertEqual(self.probe._cpu_time('1:02:03.50'), 3723.5)
        self.assertEqual(self.probe._cpu_time('2-01:02:03'), 176523)
        for value in ('oops', '-1:00', '0:nan', '0:inf'):
            with self.assertRaises((ValueError, OverflowError)):
                self.probe._cpu_time(value)



    def test_gpu_counts_only_verified_tree_processes_across_devices(self):
        self.processes[11] = (10, 'R', '/bin/python')
        self.pid(11)
        self.gpu_output = '# gpu pid type sm mem enc dec command\n0 11 C 80 10 0 0 secret-name\n1 11 C 70 10 0 0 secret-name\n0 999 C 20 10 0 0 unrelated\n'
        row = self.sample(0)[10]
        self.assertEqual(row['gpu_utilization_percent'], 150)
        self.assertTrue(row['gpu_available'])
        self.assertTrue(row['gpu_complete'])
        self.assertEqual((row['gpu_covered'], row['gpu_process_count'], row['gpu_device_count']), (2, 1, 2))
        self.assertEqual(row['gpu_kind'], 'process_sm_point_sample')
        self.assertEqual(row['gpu_aggregation'], 'sum_process_sm_percent')
        self.assertIsNone(row['gpu_interval_seconds'])
        self.assertEqual(row['gpu_poll_interval_seconds'], 3.0)
        self.assertNotIn('secret-name', json.dumps(row))
        child = next(item for item in row['process_activity'] if item['pid'] == 11)
        self.assertEqual(child['gpu_utilization_percent'], 150)
        self.assertEqual(child['gpu_sample_id'], row['gpu_sample_id'])

    def test_gpu_independent_cache_does_not_create_new_observations(self):
        self.gpu_output = '# gpu pid type sm mem enc dec command\n0 10 C 40 10 0 0 name\n'
        first = self.sample(0)[10]
        self.gpu_output = '# gpu pid type sm mem enc dec command\n0 10 C 80 10 0 0 name\n'
        cached = self.sample(1)[10]
        self.assertEqual(cached['gpu_sample_id'], first['gpu_sample_id'])
        self.assertEqual(cached['gpu_utilization_percent'], 40)
        self.assertGreater(cached['gpu_sample_age_seconds'], first['gpu_sample_age_seconds'])
        current = self.sample(3)[10]
        self.assertGreater(current['gpu_sample_id'], first['gpu_sample_id'])
        self.assertEqual(current['gpu_utilization_percent'], 80)
        self.assertEqual(len(self.gpu_calls), 2)
        self.assertEqual(self.gpu_options[0]['timeout'], .8)

    def test_gpu_missing_sm_and_missing_driver_never_become_zero_utilization(self):
        self.gpu_output = '# gpu pid type sm mem enc dec command\n0 10 C - 90 - - resident-only\n'
        unknown = self.sample(0)[10]
        self.assertFalse(unknown['gpu_available'])
        self.assertIsNone(unknown['gpu_utilization_percent'])
        self.assertEqual(unknown['gpu_process_count'], 1)
        self.gpu_output = ''
        unavailable = self.sample(3)[10]
        self.assertFalse(unavailable['gpu_available'])
        self.assertIsNone(unavailable['gpu_utilization_percent'])
        self.assertIn(unavailable['gpu_reason'], ('unsupported', 'unavailable'))
        self.sample(5)
        self.assertEqual(len(self.gpu_calls), 2)  # Unsupported queries back off.

    def test_gpu_explicit_idle_device_is_known_zero(self):
        self.gpu_output = '# gpu pid type sm mem enc dec command\n0 - - - - - - -\n'
        row = self.sample(0)[10]
        self.assertTrue(row['gpu_available'])
        self.assertEqual(row['gpu_utilization_percent'], 0)
        self.assertEqual(row['gpu_process_count'], 0)

    def test_gpu_known_partial_coverage_is_a_lower_bound_without_filling_missing_sm(self):
        self.gpu_output = '# gpu pid type sm mem command\n0 10 C 40 10 name\n1 10 C - 90 name\n'
        row = self.sample(0)[10]
        self.assertTrue(row['gpu_available'])
        self.assertFalse(row['gpu_complete'])
        self.assertEqual(row['gpu_utilization_percent'], 40)
        self.assertEqual(row['gpu_reason'], 'partial')
        self.assertTrue(row['gpu_lower_bound'])

    def test_gpu_stale_cache_is_unknown_even_when_cpu_sample_is_current(self):
        self.probe.gpu_interval = 10
        self.gpu_output = '# gpu pid type sm mem command\n0 10 C 90 10 name\n'
        self.sample(0)
        self.pid(10, utime=100)
        row = self.sample(5)[10]
        self.assertTrue(row['cpu_available'])
        self.assertFalse(row['gpu_available'])
        self.assertIsNone(row['gpu_utilization_percent'])
        self.assertEqual(row['gpu_reason'], 'stale')

    def test_gpu_pid_reuse_and_new_tree_members_cannot_reuse_cached_utilization(self):
        self.gpu_output = '# gpu pid type sm mem enc dec command\n0 10 C 90 1 0 0 name\n'
        old = self.sample(0)[10]
        self.pid(10, start=999)
        reused = self.sample(1)[10]
        self.assertNotEqual(old['identity'], reused['identity'])
        self.assertFalse(reused['gpu_available'])
        self.assertIsNone(reused['gpu_utilization_percent'])
        self.processes[11] = (10, 'R', '/bin/python')
        self.pid(11)
        member = self.sample(2)[10]
        self.assertFalse(member['gpu_available'])
        self.assertIsNone(member['gpu_utilization_percent'])

    def test_gpu_timeout_is_isolated_from_cpu_and_disk(self):
        prior = self.probe.runner
        def fail_gpu(command, **kwargs):
            if command[0] == 'nvidia-smi':
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            return prior(command, **kwargs)
        self.probe.runner = fail_gpu
        self.sample(0)
        self.pid(10, utime=100, read=500, write=300)
        row = self.sample(1)[10]
        self.assertFalse(row['gpu_available'])
        self.assertEqual(row['cpu_percent'], 100)
        self.assertEqual(row['disk_read_per_second'], 400)

    def test_process_activity_uses_instance_deltas_and_stdout_inode_without_paths(self):
        output = self.proc / 'private-experiment-output'
        output.write_text('test')
        for fd in (1, 2):
            (self.proc / '10/fd' / str(fd)).symlink_to(output)
        first = self.sample(0)[10]['process_activity'][0]
        self.assertIsNone(first['cpu_percent'])
        self.assertIsNone(first['disk_read_per_second'])
        info = output.stat()
        self.assertEqual(first['output_files'], [dict(device=info.st_dev, inode=info.st_ino)])
        self.pid(10, utime=150, read=600, write=80)
        second = self.sample(1)[10]['process_activity'][0]
        self.assertEqual((second['pid'], second['start'], second['parent']), (10, '123', 1))
        self.assertEqual(second['cpu_percent'], 150)
        self.assertEqual(second['disk_read_per_second'], 500)
        self.assertEqual(second['disk_write_per_second'], 30)
        self.assertNotIn('private-experiment-output', json.dumps(second))
        self.pid(10, start=999, utime=900, read=9000)
        reset = self.sample(2)[10]['process_activity'][0]
        self.assertIsNone(reset['cpu_percent'])
        self.assertIsNone(reset['disk_read_per_second'])

    def test_thread_budget_is_fair_across_roots_and_processes(self):
        self.processes.update({11: (10, 'S', '/bin/python'), 20: (1, 'S', '/bin/claude')})
        for pid in (10, 11, 20):
            self.pid(pid, threads=3)
            self.thread(pid, pid + 100)
            self.thread(pid, pid + 200)
        self.probe.max_total_threads = 4
        first = self.sample(0, (10, 20))
        self.assertEqual(first[10]['io_threads_covered'], 2)
        self.assertEqual(first[20]['io_threads_covered'], 2)
        self.assertEqual(first[10]['io_covered'], 2)
        for pid in (10, 11, 20):
            self.thread(pid, pid, rchar=2000)
        next_rows = self.sample(1, (10, 20))
        self.assertIsNotNone(next_rows[10]['io_read_per_second'])
        self.assertIsNotNone(next_rows[20]['io_read_per_second'])
        self.assertFalse(next_rows[10]['io_complete'])
        self.assertLessEqual(sum(row['io_threads_covered'] for row in next_rows.values()), 4)

    def test_scarce_thread_slots_rotate_in_pairs_to_preserve_counter_deltas(self):
        self.processes[20] = (1, 'S', '/bin/claude')
        self.pid(20)
        self.probe.max_total_threads = 1
        first = self.sample(0, (10, 20))
        self.assertTrue(first[10]['io_available'])
        self.assertFalse(first[20]['io_available'])
        self.pid(10, rchar=2000)
        second = self.sample(1, (10, 20))
        self.assertEqual(second[10]['io_read_per_second'], 1000)
        third = self.sample(2, (10, 20))
        self.assertFalse(third[10]['io_available'])
        self.assertTrue(third[20]['io_available'])
        self.assertIsNone(third[20]['io_read_per_second'])
        self.pid(20, rchar=3000)
        fourth = self.sample(3, (10, 20))
        self.assertEqual(fourth[20]['io_read_per_second'], 2000)

    def detached_gpu(self, sid='session-a', pid=20, parent=1, sm=80):
        self.processes[pid] = (parent, 'R', '/bin/python')
        self.pid(pid)
        (self.proc / str(pid) / 'environ').write_bytes(('PRIVATE_SECRET=not-for-export\0CLAUDE_CODE_SESSION_ID=' + sid + '\0').encode())
        self.gpu_output = '# gpu pid type sm mem command\n0 {} C {} 10 name\n'.format(pid, sm)

    def test_detached_gpu_process_matches_exact_session_and_includes_bounded_descendants(self):
        self.detached_gpu()
        self.processes[21] = (20, 'R', '/bin/python')
        self.pid(21)
        first = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        self.assertEqual(first['scope'], 'session_processes')
        self.assertEqual(first['cpu_scope'], 'session_processes')
        self.assertEqual(first['gpu_scope'], 'session_processes')
        self.assertEqual(first['io_scope'], 'observed_session_process_threads')
        self.assertEqual(first['associated_process_count'], 2)
        self.assertEqual(first['associated_gpu_process_count'], 0)
        self.assertEqual(first['process_count'], 3)
        self.assertFalse(first['gpu_available'])
        self.assertIsNone(first['gpu_utilization_percent'])
        self.assertEqual({a['pid'] for a in first['process_activity'] if a['association'] == 'claude_session_env'}, {20, 21})
        self.assertNotIn('session-a', json.dumps(first))
        self.assertNotIn('not-for-export', json.dumps(first))
        self.pid(20, utime=100, read=900)
        self.pid(21, utime=50, write=400)
        second = self.probe.sample_all([10], self.processes, now=1, session_contexts={10: 'session-a'})[10]
        self.assertEqual(second['cpu_percent'], 150)
        self.assertEqual(second['disk_read_per_second'], 800)
        self.assertEqual(second['disk_write_per_second'], 350)
        observed = self.probe.sample_all([10], self.processes, now=3, session_contexts={10: 'session-a'})[10]
        self.assertEqual(observed['associated_gpu_process_count'], 1)
        self.assertEqual(observed['gpu_utilization_percent'], 80)

    def test_detached_gpu_wrong_or_ambiguous_session_is_unattributed(self):
        self.detached_gpu(sid='other-session')
        wrong = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        self.assertEqual(wrong['process_count'], 1)
        self.assertEqual(wrong['associated_process_count'], 0)
        self.processes[30] = (1, 'S', '/bin/claude')
        self.pid(30)
        rows = self.probe.sample_all([10, 30], self.processes, now=1, session_contexts={10: 'other-session', 30: 'other-session'})
        self.assertEqual([rows[p]['associated_process_count'] for p in (10, 30)], [0, 0])

    def test_detached_gpu_other_uid_is_rejected_before_environment_read(self):
        self.detached_gpu()
        original = Path.stat
        def different_uid(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.proc / '20':
                values = list(value)
                values[4] += 1
                return os.stat_result(values)
            return value
        with patch.object(Path, 'stat', different_uid), patch.object(self.probe, '_session_owner', side_effect=AssertionError('unrelated environment opened')):
            row = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        self.assertEqual(row['associated_process_count'], 0)

    def test_detached_gpu_process_reuse_during_environment_read_is_rejected(self):
        self.detached_gpu()
        original, calls = self.probe._stat, [0]
        def changed(pid):
            value = original(pid)
            if pid == 20:
                calls[0] += 1
                if calls[0] >= 2:
                    value['start'] = '999'
            return value
        with patch.object(self.probe, '_stat', changed):
            row = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        self.assertEqual(row['associated_process_count'], 0)

    def test_gpu_process_already_in_another_selected_tree_is_not_adopted_by_session_env(self):
        self.processes[30] = (1, 'S', '/bin/claude')
        self.pid(30)
        self.detached_gpu(parent=30)
        rows = self.probe.sample_all([10, 30], self.processes, now=0, session_contexts={10: 'session-a', 30: 'session-b'})
        self.assertEqual(rows[10]['associated_process_count'], 0)
        self.assertEqual(rows[30]['associated_process_count'], 0)
        self.assertEqual(rows[10]['gpu_utilization_percent'], 0)
        self.assertEqual(rows[30]['gpu_utilization_percent'], 80)
        self.assertEqual(sum(20 in {a['pid'] for a in row['process_activity']} for row in rows.values()), 1)

    def test_detached_pid_reuse_cannot_reuse_gpu_cache_or_cpu_history(self):
        self.detached_gpu()
        self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})
        self.pid(20, start=999, utime=50000, read=99999)
        row = self.probe.sample_all([10], self.processes, now=1, session_contexts={10: 'session-a'})[10]
        extra = next(a for a in row['process_activity'] if a['pid'] == 20)
        self.assertIsNone(extra['cpu_percent'])
        self.assertIsNone(extra['disk_read_per_second'])
        self.assertFalse(extra['gpu_available'])
        self.assertIsNone(extra['gpu_utilization_percent'])

    def test_session_association_survives_a_later_cpu_only_phase_but_zero_gpu_is_not_work(self):
        self.detached_gpu(sm=0)
        first = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        self.assertIsNone(first['gpu_utilization_percent'])  # First discovery only establishes identity.
        self.assertNotIn('state', first)
        self.gpu_output = '# gpu pid type sm mem command\n0 - - - - - - -\n'
        self.pid(20, utime=100)
        second = self.probe.sample_all([10], self.processes, now=3, session_contexts={10: 'session-a'})[10]
        self.assertEqual(second['associated_process_count'], 1)
        self.assertEqual(second['associated_gpu_process_count'], 0)
        self.assertEqual(second['gpu_utilization_percent'], 0)
        self.assertGreater(second['cpu_percent'], 0)

    def test_first_gpu_discovery_cannot_assign_old_pmon_counter_to_reused_pid(self):
        self.detached_gpu(sid='previous-session', sm=80)
        original = self.probe.runner
        def recycle_after_query(command, **kwargs):
            result = original(command, **kwargs)
            if command[0] == 'nvidia-smi':
                self.pid(20, start=999)
                (self.proc / '20/environ').write_bytes(b'CLAUDE_CODE_SESSION_ID=session-a\0')
            return result
        self.probe.runner = recycle_after_query
        first = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        item = next(row for row in first['process_activity'] if row['pid'] == 20)
        self.assertEqual(item['start'], '999')
        self.assertEqual(item['association'], 'claude_session_env')
        self.assertIsNone(item['cpu_percent'])
        self.assertFalse(item['gpu_available'])
        self.assertIsNone(item['gpu_utilization_percent'])
        self.assertIsNone(first['gpu_utilization_percent'])
        self.assertEqual(first['associated_gpu_process_count'], 0)
        self.assertNotIn(20, self.probe.gpu_cache['starts'])
        self.assertNotIn(20, self.probe.gpu_cache['selected'])
        self.probe.runner = original
        self.gpu_output = '# gpu pid type sm mem command\n0 20 C 0 10 name\n'
        observed = self.probe.sample_all([10], self.processes, now=3, session_contexts={10: 'session-a'})[10]
        item = next(row for row in observed['process_activity'] if row['pid'] == 20)
        self.assertTrue(item['gpu_available'])
        self.assertEqual(item['gpu_utilization_percent'], 0)
        self.assertEqual(observed['gpu_utilization_percent'], 0)

    def test_known_gpu_process_reused_during_next_query_is_not_relabelled(self):
        self.detached_gpu()
        self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})
        original = self.probe.runner
        def recycle_after_query(command, **kwargs):
            result = original(command, **kwargs)
            if command[0] == 'nvidia-smi':
                self.pid(20, start=999)
            return result
        self.probe.runner = recycle_after_query
        row = self.probe.sample_all([10], self.processes, now=3, session_contexts={10: 'session-a'})[10]
        item = next(row for row in row['process_activity'] if row['pid'] == 20)
        self.assertEqual(item['start'], '999')
        self.assertFalse(item['gpu_available'])
        self.assertIsNone(item['gpu_utilization_percent'])
        self.assertIsNone(row['gpu_utilization_percent'])
        self.assertNotIn(20, self.probe.gpu_cache['starts'])

    def test_session_context_changes_invalidate_resource_epoch_and_association(self):
        self.detached_gpu()
        first = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        second = self.probe.sample_all([10], self.processes, now=.1, session_contexts={10: 'session-b'})[10]
        self.assertNotEqual(first['identity'], second['identity'])
        self.assertEqual(second['associated_process_count'], 0)
        self.assertIsNone(second['cpu_percent'])

    def test_session_associated_descendants_obey_existing_process_budget(self):
        self.detached_gpu()
        self.processes[21] = (20, 'R', '/bin/python')
        self.pid(21)
        self.probe.max_processes = 2
        row = self.probe.sample_all([10], self.processes, now=0, session_contexts={10: 'session-a'})[10]
        self.assertEqual(row['process_count'], 2)
        self.assertEqual(row['associated_process_count'], 1)
        self.assertFalse(row['tree_complete'])
        self.assertNotIn(21, {item['pid'] for item in row['process_activity']})


class GPUParserTests(unittest.TestCase):
    def test_header_mapping_and_unknown_sm_are_preserved(self):
        records, devices, complete = parse_gpu_stats('# gpu pid type sm mem command\n0 123 C 40 90 private\n1 456 C - 70 private\n2 - - - - -\n')
        self.assertTrue(complete)
        self.assertEqual(devices, {0, 1, 2})
        self.assertEqual(records, {(0, 123): 40, (1, 456): None})

    def test_malformed_truncated_and_duplicate_cycles_remain_partial(self):
        self.assertFalse(parse_gpu_stats('0 123 C 50 90 private')[2])
        self.assertFalse(parse_gpu_stats('# gpu pid type sm\n0 123 C 50\n0 123 C 20')[2])
        self.assertFalse(parse_gpu_stats('# gpu pid type sm\n0 123 C 50\n1 456 C 20', max_lines=2)[2])
        self.assertFalse(parse_gpu_stats('# gpu pid type sm\nmalformed')[2])


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
        # No new worker is eligible at the initial timestamp. Reuse requires the
        # same roots because adding a selected child changes parent boundaries.
        clock[0] = .5
        rows = probe.sample_all([10, 20], {})
        self.assertEqual(set(rows), {10, 20})
        self.assertEqual(rows[10]['memory_bytes'], 1234)
        self.assertEqual(rows[10]['sample_age_seconds'], .5)
        self.assertEqual(collector.calls, 1)
        rows[10]['memory_bytes'] = -1
        self.assertEqual(probe.sample_all([10, 20], {})[10]['memory_bytes'], 1234)
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

    def test_async_reuses_immutable_snapshot_without_copying_index(self):
        snapshot = ProcessSnapshot({10: (1, 'S', '/bin/codex')})
        seen = []
        class Collector:
            def sample_all(self, roots, processes, now=None):
                seen.append(processes)
                return {10: dict(identity='fixture:10:1', sample_id=1)}
        probe = AsyncProcessResources(collector=Collector())
        self.assertEqual(probe.sample_all([10], snapshot, now=0), {})
        probe._worker.join(.1)
        self.assertIs(seen[0], snapshot)
        self.assertEqual(probe.sample_all([10], snapshot, now=.5)[10]['identity'], 'fixture:10:1')

    def test_changed_parent_or_executable_suppresses_cached_resource_ownership(self):
        class Collector:
            def sample_all(self, roots, processes, now=None):
                return {pid: dict(identity='fixture:10:1', sample_id=1) for pid in roots}
        probe = AsyncProcessResources(collector=Collector())
        original = {10: (1, 'S', '/bin/codex')}
        probe.sample_all([10], original, now=0); probe._worker.join(.1)
        self.assertIn(10, probe.sample_all([10], {10: (1, 'R', '/bin/codex')}, now=.2))
        self.assertEqual(probe.sample_all([10], {10: (20, 'S', '/bin/codex')}, now=.3), {})
        self.assertEqual(probe.sample_all([10], {10: (1, 'S', '/bin/claude')}, now=.4), {})
        self.assertEqual(probe.sample_all([10], {}, now=.5), {})

    def test_root_set_change_hides_cached_parent_before_child_boundary_refresh(self):
        class Collector:
            def sample_all(self, roots, processes, now=None):
                return {pid: dict(identity='fixture:' + str(pid), sample_id=1) for pid in roots}
        probe = AsyncProcessResources(collector=Collector())
        processes = {10: (1, 'S', '/bin/codex'), 11: (10, 'S', '/bin/claude')}
        probe.sample_all([10], processes, now=0); probe._worker.join(.1)
        self.assertIn(10, probe.sample_all([10], processes, now=.2))
        self.assertEqual(probe.sample_all([10, 11], processes, now=.3), {})

    def test_session_context_change_hides_cached_resources_even_without_process_discovery(self):
        class Collector:
            def sample_all(self, roots, processes, now=None, session_contexts=None):
                return {pid: dict(identity='fixture:' + session_contexts[pid], sample_id=1) for pid in roots}
        for processes in ({10: (1, 'S', '/bin/claude')}, None):
            with self.subTest(discovered=processes is not None):
                probe = AsyncProcessResources(collector=Collector())
                probe.sample_all([10], processes, now=0, session_contexts={10: 'session-a'})
                probe._worker.join(.1)
                self.assertEqual(probe.sample_all([10], processes, now=.2, session_contexts={10: 'session-a'})[10]['identity'], 'fixture:session-a')
                self.assertEqual(probe.sample_all([10], processes, now=.3, session_contexts={10: 'session-b'}), {})
                probe.sample_all([10], processes, now=1, session_contexts={10: 'session-b'})
                probe._worker.join(.1)
                self.assertEqual(probe.sample_all([10], processes, now=1.2, session_contexts={10: 'session-b'})[10]['identity'], 'fixture:session-b')


if __name__ == '__main__':
    unittest.main()
