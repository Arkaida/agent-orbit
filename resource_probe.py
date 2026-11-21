"""Bounded, read-only process resource samples for CLI monitoring.

Linux memory is a sum of RSS, not unique physical memory.  ``io_*`` counts
observed root-process rchar/wchar changes (including reaped children), not
terminal bytes.  Network counts only observed, attributable TCP payload
counter changes; it never substitutes host or namespace interface traffic.
No command lines, socket endpoints, file paths or packet contents are returned.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid


_COUNTERS = ('rchar', 'wchar', 'read_bytes', 'write_bytes')
_SOCKET = re.compile(r'^socket:\[(\d+)\]$')
_NET_NAMESPACE = re.compile(r'^net:\[\d+\]$')
_TCP_STATE = re.compile(r'^[A-Z][A-Z0-9_-]*\s+\d+\s+\d+\s+')


def parse_tcp_stats(text, max_lines=30000):
    """Parse ss -tinaeH output, retaining inode/cookie/counters only.

    Explicit missing counters remain unknown.  Some older iproute2 builds do
    not expose them, and a missing field must not be presented as zero traffic.
    """
    records, current = {}, None
    lines = text.splitlines()
    complete = len(lines) <= max_lines
    for line in lines[:max_lines]:
        stripped = line.strip()
        if _TCP_STATE.match(stripped):
            inode = re.search(r'\bino:(\d+)\b', stripped)
            cookie = re.search(r'\bsk:([0-9a-fA-F]+)\b', stripped)
            current = None
            if inode and int(inode.group(1)) > 0 and cookie:
                current = (int(inode.group(1)), cookie.group(1).lower())
                records[current] = dict(rx=None, tx=None, tx_kind=None)
            elif inode and int(inode.group(1)) > 0:
                complete = False  # A reusable inode alone is not a stable socket identity.
        if current is None:
            continue
        values = {name: int(value) for name, value in re.findall(
            r'\b(bytes_received|bytes_sent|bytes_acked):(\d+)\b', stripped)}
        record = records[current]
        if 'bytes_received' in values:
            record['rx'] = values['bytes_received']
        if 'bytes_sent' in values:
            record.update(tx=values['bytes_sent'], tx_kind='sent')
        elif 'bytes_acked' in values and record['tx_kind'] != 'sent':
            record.update(tx=values['bytes_acked'], tx_kind='acked')
    return records, complete


class ProcessResources:
    """One shared sample per second for all selected root processes.

    A fresh collector nonce is part of identities, so reconnecting cannot turn
    previous observed counters into new activity.  Injected proc/runner/clock
    inputs keep tests independent of the machine's processes and sockets.
    """
    def __init__(self, proc_root='/proc', platform_name=None, runner=subprocess.run,
                 clock=time.monotonic, min_interval=1.0, max_gap=5.0,
                 max_processes=128, max_fds=256, max_total_fds=4096,
                 page_size=None, nonce=None):
        self.proc = Path(proc_root)
        self.platform = platform_name or sys.platform
        self.runner, self.clock = runner, clock
        self.interval, self.max_gap = max(0.1, min_interval), max_gap
        self.max_processes = max(1, min(128, max_processes))
        self.max_fds, self.max_total_fds = max_fds, max_total_fds
        self.page_size = page_size or os.sysconf('SC_PAGE_SIZE')
        self.nonce = nonce or uuid.uuid4().hex
        self.next_sample, self.last_sample = -float('inf'), None
        self.sample_id = 0
        self.roots, self.net_baselines = {}, {}
        self.cached, self.cached_roots = {}, ()

    @staticmethod
    def _empty(sample_id, reason='unavailable'):
        result = dict(scope='process_tree', identity=None, epoch=None,
                      sample_id=sample_id, process_count=0, tree_complete=False,
                      memory_kind='rss_sum', memory_bytes=None, memory_covered=0,
                      memory_available=False, memory_complete=False,
                      io_scope='root_process_with_reaped_children', io_kind='syscall_bytes',
                      io_available=False, io_complete=False,
                      network_scope='tcp_process_tree', network_kind='tcp_payload_counters',
                      network_available=False, network_complete=False,
                      network_shared=False, network_shared_sockets=0,
                      network_socket_count=0, network_rx_covered_sockets=0,
                      network_tx_covered_sockets=0, memory_reason=reason,
                      io_reason=reason, network_reason=reason,
                      interval_seconds=None)
        for name in ('io_read', 'io_write', 'disk_read', 'disk_write',
                     'network_rx', 'network_tx'):
            result[name + '_bytes'] = None
            result[name + '_per_second'] = None
        return result

    @staticmethod
    def _tree(root, processes, roots, limit):
        if not processes or root not in processes:
            return [root], False
        children = {}
        for pid, entry in processes.items():
            if isinstance(pid, int) and len(entry) >= 3:
                children.setdefault(entry[0], []).append(pid)
        pending, seen, found, complete = [root], set(), [], True
        while pending:
            pid = pending.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            entry = processes.get(pid)
            if entry is None or 'Z' in entry[1]:
                continue
            if pid != root and pid in roots:
                continue
            if len(found) >= limit:
                complete = False
                break
            found.append(pid)
            pending.extend(sorted(children.get(pid, [])))
        return found, complete

    def _read(self, pid, name):
        with (self.proc / str(pid) / name).open() as file:
            return file.read(16384)

    def _start(self, pid):
        data = self._read(pid, 'stat')
        # comm may contain spaces and parentheses; fields after its final ')' are fixed.
        fields = data[data.rfind(')') + 1:].split()
        return fields[19] if len(fields) > 19 and fields[19].isdigit() else None

    def _rss(self, pid):
        fields = self._read(pid, 'statm').split()
        return int(fields[1]) * self.page_size

    def _io(self, pid):
        values = {}
        for line in self._read(pid, 'io').splitlines():
            key, _, value = line.partition(':')
            if key in _COUNTERS:
                values[key] = int(value.strip())
        if any(name not in values or values[name] < 0 for name in _COUNTERS):
            raise ValueError('Incomplete I/O counters')
        return values

    def _fds(self, pid, budget):
        inodes, complete, count = set(), True, 0
        try:
            with os.scandir(self.proc / str(pid) / 'fd') as entries:
                for entry in entries:
                    if count >= self.max_fds or budget[0] <= 0:
                        complete = False
                        break
                    count += 1; budget[0] -= 1
                    try:
                        link = os.readlink(entry.path)
                    except FileNotFoundError:
                        continue  # The descriptor closed during this read-only sample.
                    except OSError:
                        complete = False
                        continue
                    match = _SOCKET.match(link)
                    if match:
                        inodes.add(int(match.group(1)))
            return inodes, complete
        except OSError:
            return inodes, False

    def _net_namespace(self, pid):
        try:
            value = os.readlink(self.proc / str(pid) / 'ns/net')
            return value if _NET_NAMESPACE.fullmatch(value) else None
        except OSError:
            return None

    def _tcp(self):
        try:
            command = ['ss', '-tinaeH']
            result = self.runner(command, text=True, capture_output=True, timeout=0.35,
                                 env=dict(os.environ, LC_ALL='C'))
            if result.returncode or len(result.stdout) > 4194304:
                return {}, False, 'unavailable'
            records, complete = parse_tcp_stats(result.stdout)
            return records, complete, '' if complete else 'partial'
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return {}, False, 'unsupported'

    def _record(self, root, start):
        identity = '{}:{}:{}'.format(self.nonce, root, start)
        record = self.roots.get(root)
        if record is None or record['identity'] != identity:
            record = dict(identity=identity, io=None, io_time=None,
                          io_totals=[0] * 4, net_time=None, net_totals=[0, 0])
            self.roots[root] = record
        return record

    def _linux(self, roots, processes, now, elapsed):
        result, fd_sets, fd_complete, fd_reasons = {}, {}, {}, {}
        budget = [self.max_total_fds]
        collector_namespace = self._net_namespace('self')
        for root in roots:
            row = self._empty(self.sample_id)
            result[root] = row
            pids, tree_complete = self._tree(root, processes, roots, self.max_processes)
            row.update(process_count=len(pids), tree_complete=tree_complete, interval_seconds=elapsed)
            if not pids:
                self.roots.pop(root, None)
                continue
            try:
                start = self._start(root)
                if start is None:
                    continue
            except (OSError, ValueError, IndexError):
                self.roots.pop(root, None)
                continue
            saved = self._record(root, start)
            row.update(identity=saved['identity'], epoch=saved['identity'])
            rss, inodes, memory_covered, all_fds = 0, set(), 0, tree_complete
            fd_reason = '' if tree_complete else 'partial'
            for pid in pids:
                try:
                    before = self._start(pid)
                    resident = self._rss(pid)
                    namespace = self._net_namespace(pid)
                    if namespace is None or collector_namespace is None:
                        owned, readable = set(), False
                        fd_reason = 'namespace_unavailable'
                    elif namespace != collector_namespace:
                        owned, readable = set(), False
                        fd_reason = 'namespace_mismatch'
                    else:
                        owned, readable = self._fds(pid, budget)
                    after = self._start(pid)
                    if before is None or before != after or (pid == root and before != start):
                        all_fds = False
                        continue
                    if resident >= 0:
                        rss += resident; memory_covered += 1
                    inodes.update(owned); all_fds &= readable
                except (OSError, ValueError, IndexError):
                    all_fds = False
            row.update(memory_bytes=rss if memory_covered else None,
                       memory_covered=memory_covered, memory_available=memory_covered > 0,
                       memory_complete=tree_complete and memory_covered == len(pids),
                       memory_reason='' if tree_complete and memory_covered == len(pids) else 'partial')
            fd_sets[root], fd_complete[root], fd_reasons[root] = inodes, all_fds, fd_reason
            try:
                counters = self._io(root)
                if self._start(root) != start:
                    raise ValueError('Process changed')
                values = [counters[name] for name in _COUNTERS]
                previous, last = saved['io'], saved['io_time']
                ready = (previous is not None and last is not None and
                         0 < now - last <= self.max_gap and all(x >= y for x, y in zip(values, previous)))
                changes = [x - y for x, y in zip(values, previous)] if ready else [0] * 4
                saved['io_totals'] = [x + y for x, y in zip(saved['io_totals'], changes)]
                saved.update(io=values, io_time=now)
                row.update(io_available=True, io_complete=True, io_reason='')
                for index, name in enumerate(('io_read', 'io_write', 'disk_read', 'disk_write')):
                    row[name + '_bytes'] = saved['io_totals'][index]
                    row[name + '_per_second'] = round(changes[index] / (now - last), 3) if ready else None
            except (OSError, ValueError, IndexError):
                saved.update(io=None, io_time=None)

        sockets, network_complete, network_reason = self._tcp()
        owners = {}
        for root, inodes in fd_sets.items():
            for inode in inodes:
                owners.setdefault(inode, set()).add(root)
        next_baselines = {}
        for root, row in result.items():
            if row['identity'] is None:
                continue
            saved = self.roots[root]
            if network_reason in ('unavailable', 'unsupported') or not fd_complete.get(root):
                saved['net_time'] = None
                row['network_reason'] = network_reason or fd_reasons.get(root) or 'partial'
                continue
            owned = [(key, data) for key, data in sockets.items() if root in owners.get(key[0], ())]
            shared = [(key, data) for key, data in owned if len(owners[key[0]]) > 1]
            exclusive = [(key, data) for key, data in owned if len(owners[key[0]]) == 1]
            row.update(network_socket_count=len(owned), network_shared=bool(shared),
                       network_shared_sockets=len(shared))
            covered = [sum(data[direction] is not None for _, data in exclusive) for direction in ('rx', 'tx')]
            row.update(network_rx_covered_sockets=covered[0], network_tx_covered_sockets=covered[1])
            available = (not owned and network_complete) or any(covered)
            complete = network_complete and not shared and all(count == len(owned) for count in covered)
            row.update(network_available=available, network_complete=complete,
                       network_reason='' if complete else 'shared' if shared else 'partial')
            last = saved['net_time']
            ready = last is not None and 0 < now - last <= self.max_gap
            changes = [0, 0]
            compared = [0, 0]
            for key, data in exclusive:
                baseline_key = (row['identity'], key)
                prior = self.net_baselines.get(baseline_key)
                next_baselines[baseline_key] = data
                if not ready or prior is None:
                    continue
                for index, direction in enumerate(('rx', 'tx')):
                    value, old = data[direction], prior[direction]
                    same_kind = direction != 'tx' or data['tx_kind'] == prior['tx_kind']
                    if value is not None and old is not None and value >= old and same_kind:
                        changes[index] += value - old
                        compared[index] += 1
            if available:
                saved['net_totals'] = [x + y for x, y in zip(saved['net_totals'], changes)]
                for index, direction in enumerate(('rx', 'tx')):
                    if not owned or covered[index]:
                        row['network_' + direction + '_bytes'] = saved['net_totals'][index]
                        direction_ready = ready and (not owned or compared[index] > 0)
                        row['network_' + direction + '_per_second'] = round(changes[index] / (now - last), 3) if direction_ready else None
                saved['net_time'] = now
            else:
                saved['net_time'] = None
        self.net_baselines = next_baselines
        return result

    def _darwin(self, roots, processes, elapsed):
        trees = {root: self._tree(root, processes, roots, self.max_processes) for root in roots}
        selected = sorted({pid for pids, _ in trees.values() for pid in pids})
        values = {}
        if selected:
            try:
                command = ['ps', '-p', ','.join(map(str, selected)), '-o', 'pid=,lstart=,rss=']
                process = self.runner(command, text=True, capture_output=True, timeout=0.35,
                                      env=dict(os.environ, LC_ALL='C'))
                if process.returncode == 0:
                    for line in process.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 7:
                            try:
                                pid, resident = int(parts[0]), int(parts[-1]) * 1024
                                if resident >= 0:
                                    values[pid] = (' '.join(parts[1:-1]), resident)
                            except ValueError:
                                continue
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        result = {}
        for root, (pids, complete) in trees.items():
            row = self._empty(self.sample_id, 'unsupported')
            row.update(process_count=len(pids), tree_complete=complete, interval_seconds=elapsed)
            if root in values:
                saved = self._record(root, values[root][0])
                resident = [values[pid][1] for pid in pids if pid in values]
                row.update(identity=saved['identity'], epoch=saved['identity'],
                           memory_bytes=sum(resident), memory_covered=len(resident),
                           memory_available=True, memory_complete=complete and len(resident) == len(pids),
                           memory_reason='' if complete and len(resident) == len(pids) else 'partial')
            else:
                self.roots.pop(root, None)
                row['memory_reason'] = 'unavailable'
            result[root] = row
        return result

    def sample_all(self, root_pids, processes, now=None):
        now = self.clock() if now is None else now
        roots = tuple(sorted({pid for pid in root_pids if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0}))
        if roots == self.cached_roots and self.last_sample is not None and self.last_sample <= now < self.next_sample:
            return {pid: dict(row) for pid, row in self.cached.items()}
        elapsed = now - self.last_sample if self.last_sample is not None and now > self.last_sample else None
        if elapsed is None or elapsed > self.max_gap:
            self.net_baselines.clear()
            for record in self.roots.values():
                record.update(io=None, io_time=None, net_time=None)
        self.sample_id += 1
        if not roots:
            rows = {}
        elif self.platform.startswith('linux'):
            rows = self._linux(roots, processes, now, elapsed)
        elif self.platform == 'darwin':
            rows = self._darwin(roots, processes, elapsed)
        else:
            rows = {pid: self._empty(self.sample_id, 'unsupported') for pid in roots}
        self.roots = {pid: row for pid, row in self.roots.items() if pid in roots}
        self.cached, self.cached_roots = rows, roots
        self.last_sample, self.next_sample = now, now + self.interval
        return {pid: dict(row) for pid, row in rows.items()}


class AsyncProcessResources:
    """Non-blocking snapshot facade; slow resource commands run off the state path.

    One lazy daemon worker may run at a time.  A caller gets only recently
    completed results for roots it currently requested, never a wait on ps/ss.
    """
    def __init__(self, collector=None, clock=time.monotonic, min_interval=1.0,
                 max_age=4.0):
        self.clock = clock
        self.interval, self.max_age = max(0.1, min_interval), max_age
        self.collector = collector if collector is not None else ProcessResources(
            clock=clock, min_interval=self.interval)
        self._lock = threading.Lock()
        self._busy, self._last_started = False, None
        self._cache, self._cache_at = {}, None
        self._worker = None

    def _collect(self, roots, processes, started_at):
        try:
            samples = self.collector.sample_all(roots, processes, now=started_at)
            rows = {pid: dict(row) for pid, row in samples.items()
                    if pid in roots and isinstance(row, dict)}
        except Exception:
            rows = {}  # Resource failure does not fail the CLI lifecycle snapshot.
        with self._lock:
            self._cache, self._cache_at = rows, started_at
            self._busy = False

    def sample_all(self, root_pids, processes, now=None):
        now = self.clock() if now is None else now
        roots = tuple(sorted({pid for pid in root_pids
                              if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0}))
        worker = None
        with self._lock:
            age = now - self._cache_at if self._cache_at is not None else None
            rows = ({pid: dict(row, sample_age_seconds=round(age, 3))
                     for pid, row in self._cache.items() if pid in roots}
                    if age is not None and 0 <= age <= self.max_age else {})
            eligible = (self._last_started is None or now < self._last_started or
                        now - self._last_started >= self.interval)
            if roots and not self._busy and eligible:
                self._busy, self._last_started = True, now
                copied = dict(processes) if processes is not None else None
                worker = threading.Thread(target=self._collect, args=(roots, copied, now),
                                          name='agent-orbit-resources', daemon=True)
                self._worker = worker
        if worker is not None:
            try:
                worker.start()
            except Exception:
                with self._lock:
                    self._busy = False
                    self._cache, self._cache_at = {}, None
        return rows
