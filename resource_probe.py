"""Bounded, read-only process resource samples for CLI monitoring.

Linux memory is a sum of RSS, not unique physical memory. CPU is the
observed process-tree user+system time, excluding waited-for children. ``io_*``
counts observed live-thread I/O deltas across the tree, not terminal bytes;
per-thread counters exclude inherited waited-for-child accounting. Polling can
miss short-lived tasks and the final work of tasks that exit between samples.  Network counts only observed, attributable TCP payload
counter changes; it never substitutes host or namespace interface traffic.
No command lines, socket endpoints, file paths or packet contents are returned.
"""
import os
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from process_snapshot import as_snapshot


_COUNTERS = ('rchar', 'wchar', 'read_bytes', 'write_bytes')
_SOCKET = re.compile(r'^socket:\[(\d+)\]$')
_NET_NAMESPACE = re.compile(r'^net:\[\d+\]$')
_TCP_STATE = re.compile(r'^[A-Z][A-Z0-9_-]*\s+\d+\s+\d+\s+')


def parse_gpu_stats(text, max_lines=4096):
    """Read one pmon cycle without retaining command names or device totals.

    A missing SM counter (including unsupported MIG metrics) stays unknown;
    resident GPU memory is deliberately not used as an activity substitute.
    """
    records, devices, header, complete = {}, set(), None, True
    lines = text.splitlines()
    if len(lines) > max_lines:
        complete = False
    for line in lines[:max_lines]:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == '#':
            names = parts[1:]
            if names[:3] == ['gpu', 'pid', 'type'] and 'sm' in names:
                header = names
            continue
        if header is None:
            complete = False
            continue
        try:
            gpu = int(parts[header.index('gpu')])
            pid_value = parts[header.index('pid')]
            if gpu < 0:
                raise ValueError('Invalid GPU index')
            devices.add(gpu)
            if pid_value == '-':
                continue  # Explicit no-process row, not a missing SM field.
            pid = int(pid_value)
            if pid <= 0:
                raise ValueError('Invalid process id')
            raw = parts[header.index('sm')]
            sm = int(raw) if raw.isdigit() and 0 <= int(raw) <= 100 else None
            key = (gpu, pid)
            if key in records:
                complete = False
                records[key] = None
            else:
                records[key] = sm
        except (ValueError, IndexError):
            complete = False
    return records, devices, bool(header is not None and devices and complete)


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
                 page_size=None, nonce=None, clock_ticks=None,
                 max_threads=256, max_total_threads=1024, gpu_interval=3.0):
        self.proc = Path(proc_root)
        self.platform = platform_name or sys.platform
        self.runner, self.clock = runner, clock
        self.interval, self.max_gap = max(0.1, min_interval), max_gap
        self.max_processes = max(1, min(128, max_processes))
        self.max_fds, self.max_total_fds = max_fds, max_total_fds
        self.page_size = page_size or os.sysconf('SC_PAGE_SIZE')
        self.clock_ticks = clock_ticks or os.sysconf('SC_CLK_TCK')
        self.max_threads = max(1, min(256, max_threads))
        self.max_total_threads = max(0, min(4096, max_total_threads))
        self.nonce = nonce or uuid.uuid4().hex
        self.next_sample, self.last_sample = -float('inf'), None
        self.sample_id = 0
        self.roots, self.net_baselines = {}, {}
        self.cached, self.cached_roots = {}, ()
        self.gpu_interval = max(2.0, gpu_interval)
        self.gpu_cache, self.gpu_next, self.gpu_sample_id = None, -float('inf'), 0
        self.session_contexts, self.cached_contexts, self.associated = {}, (), {}

    @staticmethod
    def _empty(sample_id, reason='unavailable'):
        result = dict(scope='process_tree', identity=None, epoch=None,
                      sample_id=sample_id, process_count=0, tree_complete=False,
                      memory_kind='rss_sum', memory_bytes=None, memory_covered=0,
                      memory_available=False, memory_complete=False,
                      cpu_scope='process_tree', cpu_seconds=None, cpu_percent=None,
                      cpu_available=False, cpu_complete=False, cpu_covered=0, cpu_reason=reason,
                      cpu_kind='observed_user_system_time', cpu_lower_bound=True,
                      gpu_scope='process_tree', gpu_kind='process_sm_point_sample',
                      gpu_aggregation='sum_process_sm_percent',
                      gpu_lower_bound=True,
                      gpu_utilization_percent=None, gpu_available=False, gpu_complete=False,
                      gpu_covered=0, gpu_process_count=0, gpu_device_count=0,
                      gpu_reason=reason, gpu_sample_id=None, gpu_sample_age_seconds=None,
                      gpu_interval_seconds=None, gpu_poll_interval_seconds=None, process_activity=[],
                      associated_gpu_process_count=0, associated_process_count=0,
                      io_scope='observed_process_tree_threads', io_kind='thread_syscall_bytes',
                      io_accounting='live_threads_no_reaped_children', io_lower_bound=True,
                      io_available=False, io_complete=False, io_covered=0, io_threads_covered=0,
                      network_scope='tcp_process_tree', network_kind='tcp_payload_counters',
                      network_available=False, network_complete=False,
                      network_shared=False, network_shared_sockets=0,
                      network_attribution_unknown=False,
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
        snapshot = as_snapshot(processes)
        return snapshot.descendants(root, boundaries=roots, limit=limit) if snapshot is not None else ([root], False)

    def _read(self, pid, name):
        with (self.proc / str(pid) / name).open() as file:
            return file.read(16384)

    @staticmethod
    def _stat_fields(data):
        # comm may contain spaces and parentheses; everything after its final
        # ')' has fixed positions. CPU fields exclude cutime/cstime.
        closing = data.rfind(')')
        if closing < 0:
            raise ValueError('Malformed process identity')
        fields = data[closing + 1:].split()
        if len(fields) < 20:
            raise ValueError('Incomplete process identity')
        start = int(fields[19])
        parent, user, system, threads = (int(fields[index]) for index in (1, 11, 12, 17))
        if min(start, parent, user, system) < 0 or threads < 1:
            raise ValueError('Invalid process counters')
        return dict(start=str(start), parent=parent, ticks=user + system, threads=threads)

    def _stat(self, pid):
        return self._stat_fields(self._read(pid, 'stat'))

    def _start(self, pid):
        return self._stat(pid)['start']

    def _rss(self, pid):
        fields = self._read(pid, 'statm').split()
        return int(fields[1]) * self.page_size

    @staticmethod
    def _io_values(text):
        values = {}
        for line in text.splitlines():
            key, _, value = line.partition(':')
            if key in _COUNTERS:
                values[key] = int(value.strip())
        if any(name not in values or values[name] < 0 for name in _COUNTERS):
            raise ValueError('Incomplete I/O counters')
        return tuple(values[name] for name in _COUNTERS)

    def _thread_io(self, pid, start, expected_threads, budget):
        # /proc/PID/io adds signal->ioac, which includes reaped children. Reading
        # each live task's own /task/TID/io avoids counting that work again after
        # wait(). No process-wide fallback is mixed into this ledger.
        current, complete, examined = {}, True, 0
        try:
            with os.scandir(self.proc / str(pid) / 'task') as entries:
                for entry in entries:
                    if examined >= self.max_threads or budget[0] <= 0:
                        complete = False
                        break
                    examined += 1
                    if not entry.name.isdigit():
                        complete = False
                        continue
                    budget[0] -= 1
                    tid = int(entry.name)
                    try:
                        before = self._stat_fields(self._read(pid, 'task/{}/stat'.format(tid)))
                        values = self._io_values(self._read(pid, 'task/{}/io'.format(tid)))
                        after = self._stat_fields(self._read(pid, 'task/{}/stat'.format(tid)))
                        if before['start'] != after['start']:
                            complete = False
                            continue
                        current[(pid, start, tid, before['start'])] = values
                    except (OSError, ValueError, IndexError):
                        complete = False
        except OSError:
            complete = False
        return current, complete and len(current) == expected_threads

    def _delta(self, saved, channel, current, now, width):
        previous, last = saved[channel], saved[channel + '_time']
        ready = previous is not None and last is not None and 0 < now - last <= self.max_gap
        changes, compared = [0] * width, 0
        for identity, values in current.items():
            old = previous.get(identity) if ready else None
            if old is not None and len(old) == width and all(x >= y for x, y in zip(values, old)):
                changes = [total + value - before for total, value, before in zip(changes, values, old)]
                compared += 1
        saved[channel] = current
        saved[channel + '_time'] = now
        return changes, compared, ready and set(current) == set(previous), now - last if ready else None

    def _cpu(self, row, saved, current, now, complete):
        changes, compared, unchanged, elapsed = self._delta(saved, 'cpu', current, now, 1)
        saved['cpu_total'] += changes[0]
        covered = len(current)
        row.update(cpu_available=covered > 0, cpu_covered=covered,
                   cpu_seconds=round(saved['cpu_total'], 6) if covered else None,
                   cpu_percent=round(changes[0] / elapsed * 100, 3) if compared and elapsed else None,
                   cpu_complete=bool(complete and covered and unchanged and compared == covered),
                   cpu_reason='' if complete and covered and unchanged and compared == covered else 'initializing' if complete and covered and not compared else 'partial')

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
        sid = self.session_contexts.get(root)
        context = ':' + hashlib.sha256(sid.encode()).hexdigest()[:16] if sid else ''
        identity = '{}:{}:{}{}'.format(self.nonce, root, start, context)
        record = self.roots.get(root)
        if record is None or record['identity'] != identity:
            record = dict(identity=identity, io=None, io_time=None,
                          io_totals=[0] * 4, cpu=None, cpu_time=None, cpu_total=0.0,
                          net_time=None, net_totals=[0, 0])
            self.roots[root] = record
        return record

    @staticmethod
    def _session_contexts(contexts, roots):
        return {root: sid for root, sid in (contexts.items() if isinstance(contexts, dict) else ())
                if root in roots and isinstance(sid, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', sid)}

    def _session_owner(self, pid, allowed_uids):
        """Read one permitted provenance key, never return other environment."""
        before = self._stat(pid)
        owner = (self.proc / str(pid)).stat().st_uid
        if owner not in allowed_uids:
            raise ValueError('Unrelated process owner')
        with (self.proc / str(pid) / 'environ').open('rb') as environment:
            data = environment.read(131072)
        session = None
        for entry in data.split(b'\0'):
            if entry.startswith(b'CLAUDE_CODE_SESSION_ID='):
                value = entry.split(b'=', 1)[1]
                try:
                    text = value.decode('ascii')
                except UnicodeDecodeError:
                    break
                if re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', text):
                    session = text
                break
        after = self._stat(pid)
        if before['start'] != after['start'] or (self.proc / str(pid)).stat().st_uid != owner:
            raise ValueError('Process changed during provenance read')
        return session, owner, after['start']

    def _session_processes(self, roots, processes, now):
        """Extend only from GPU-discovered, exact same-user Claude provenance.

        Existing selected trees win. Previously verified GPU instances remain
        candidates while alive, so a later CPU-only phase retains provenance;
        this never scans arbitrary /proc environments or adopts by recency.
        """
        trees = {root: self._tree(root, processes, roots, self.max_processes) for root in roots}
        extras, anchors = {root: [] for root in roots}, {root: {} for root in roots}
        if not self.session_contexts:
            self.associated = {}
            return trees, extras, anchors
        discovery = {}
        for root, (pids, complete) in trees.items():
            row = self._empty(self.sample_id)
            row['tree_complete'] = complete
            for pid in pids:
                try:
                    row['process_activity'].append(dict(pid=pid, start=self._start(pid)))
                except (OSError, ValueError, IndexError):
                    pass
            for pid, (owner_root, start, _) in self.associated.items():
                if owner_root == root and pid not in pids:
                    try:
                        if self._start(pid) == start:
                            row['process_activity'].append(dict(pid=pid, start=start))
                    except (OSError, ValueError, IndexError):
                        pass
            discovery[root] = row
        self._gpu(discovery, now)  # Same independently cached query is reused below.
        gpu_pids = {pid for _, pid in (self.gpu_cache or {}).get('records', {})}
        root_uids, sid_roots = {}, {}
        for root, sid in self.session_contexts.items():
            try:
                root_uids[root] = (self.proc / str(root)).stat().st_uid
            except OSError:
                continue
            sid_roots.setdefault(sid, []).append(root)
        normal = {pid for pids, _ in trees.values() for pid in pids}
        claimed, retained = set(normal), {}
        candidates = sorted(gpu_pids | set(self.associated))[:128]
        for pid in candidates:
            if pid in claimed:
                continue
            try:
                # Check UID before opening the process environment.
                owner = (self.proc / str(pid)).stat().st_uid
                if owner not in root_uids.values():
                    continue
                sid, owner, start = self._session_owner(pid, set(root_uids.values()))
                matches = sid_roots.get(sid, ())
                if len(matches) != 1:
                    continue
                root = matches[0]
                if owner != root_uids[root]:
                    continue
                previous = self.associated.get(pid)
                if pid not in gpu_pids and previous != (root, start, sid):
                    continue
                remaining = self.max_processes - len(trees[root][0]) - len(extras[root])
                if remaining <= 0:
                    trees[root] = (trees[root][0], False)
                    continue
                branch, complete = self._tree(pid, processes, roots, remaining)
                if not complete:
                    trees[root] = (trees[root][0], False)
                accepted = []
                rejected = set()
                for child in branch:
                    parent = processes.get(child, (None,))[0] if processes is not None else None
                    if child in claimed or parent in rejected:
                        rejected.add(child)
                        continue
                    try:
                        same_owner = (self.proc / str(child)).stat().st_uid == owner
                    except OSError:
                        same_owner = False
                    if not same_owner:
                        rejected.add(child)
                        trees[root] = (trees[root][0], False)
                        continue
                    accepted.append(child)
                if pid not in accepted:
                    continue
                anchors[root][pid] = start
                extras[root].extend(accepted)
                claimed.update(accepted)
                retained[pid] = (root, start, sid)
                # Provenance can establish membership, but cannot establish
                # the identity that produced an already-returned pmon sample.
                # A newly discovered instance becomes GPU-observable only
                # after a later query brackets its known start identity.
            except (OSError, ValueError, IndexError):
                continue
        self.associated = retained
        return trees, extras, anchors

    def _output_files(self, pid):
        files, observable = set(), True
        for descriptor in (1, 2):
            try:
                value = os.stat(self.proc / str(pid) / 'fd' / str(descriptor))
                files.add((value.st_dev, value.st_ino))
            except OSError:
                observable = False
        return [dict(device=device, inode=inode) for device, inode in sorted(files)], observable

    def _activities(self, root, saved, cpu, io, parents, outputs, now):
        """Per-instance evidence for explicit task-output bindings, never state."""
        cpu_before, cpu_last = saved['cpu'], saved['cpu_time']
        io_before, io_last = saved['io'], saved['io_time']
        cpu_ready = cpu_before is not None and cpu_last is not None and 0 < now - cpu_last <= self.max_gap
        io_ready = io_before is not None and io_last is not None and 0 < now - io_last <= self.max_gap
        disk = {}
        if io_ready:
            for identity, values in io.items():
                old = io_before.get(identity)
                if old is not None and all(x >= y for x, y in zip(values, old)):
                    entry = disk.setdefault(identity[0], [0, 0])
                    entry[0] += values[2] - old[2]
                    entry[1] += values[3] - old[3]
        rows = []
        for (pid, start), values in cpu.items():
            old = cpu_before.get((pid, start)) if cpu_ready else None
            percent = (values[0] - old[0]) / (now - cpu_last) * 100 if old is not None and values[0] >= old[0] else None
            activity = dict(pid=pid, start=start, parent=parents.get(pid), is_root=pid == root,
                            cpu_percent=round(percent, 3) if percent is not None else None,
                            disk_read_per_second=round(disk[pid][0] / (now - io_last), 3) if pid in disk else None,
                            disk_write_per_second=round(disk[pid][1] / (now - io_last), 3) if pid in disk else None,
                            output_files=outputs.get(pid, ([], False))[0],
                            output_files_observable=outputs.get(pid, ([], False))[1], gpu_available=False,
                            gpu_utilization_percent=None, gpu_sample_age_seconds=None)
            rows.append(activity)
        return rows

    def _thread_shares(self, total, members):
        """Fair bounded budgets; hold scarce extra slots for two samples.

        Two adjacent observations permit a real counter delta. Rotating every
        sample would leave scarce-budget members perpetually initializing.
        Stable lower subsets retain their baselines while extras rotate.
        """
        if not members:
            return {}
        base, extra = divmod(max(0, total), len(members))
        phase = ((self.sample_id - 1) // 2) % len(members)
        return {member: base + (((index - phase) % len(members)) < extra)
                for index, member in enumerate(members)}

    def _gpu(self, rows, now):
        """One bounded, independently throttled GPU query for selected trees."""
        selected = {item['pid']: item['start'] for row in rows.values() for item in row['process_activity']}
        if not selected:
            return
        if now >= self.gpu_next or (self.gpu_cache is not None and now < self.gpu_cache['at']):
            self.gpu_sample_id += 1
            at = self.clock()
            cache = dict(at=now, id=self.gpu_sample_id, records={}, devices=set(), starts={}, selected=dict(selected), complete=False, reason='unavailable')
            try:
                command = ['nvidia-smi', 'pmon', '-c', '1', '-s', 'u', '-d', '1']
                value = self.runner(command, text=True, capture_output=True, timeout=0.8,
                                    env=dict(os.environ, LC_ALL='C'))
                if value.returncode == 0 and len(value.stdout) <= 1048576:
                    records, devices, complete = parse_gpu_stats(value.stdout)
                    starts = {}
                    for pid in {pid for _, pid in records if pid in selected}:
                        try:
                            start = self._start(pid)
                            if start == selected[pid]:
                                starts[pid] = start
                        except (OSError, ValueError, IndexError):
                            pass
                    cache.update(records=records, devices=devices, starts=starts, complete=complete,
                                 reason='' if complete else 'partial' if devices else 'unsupported')
                elif value.returncode:
                    cache['reason'] = 'unavailable'
            except (OSError, subprocess.TimeoutExpired, ValueError):
                cache['reason'] = 'unsupported'
            # The worker remains independent of lifecycle. Do not present an
            # unusually slow query as a current instantaneous observation.
            cache['duration'] = max(0.0, self.clock() - at)
            self.gpu_cache = cache
            self.gpu_next = now + (self.gpu_interval if cache['complete'] else 30.0)
        cache = self.gpu_cache
        if cache is None:
            return
        age = max(0.0, now - cache['at']) + cache['duration']
        for row in rows.values():
            row.update(gpu_sample_id=cache['id'], gpu_sample_age_seconds=round(age, 3),
                       gpu_interval_seconds=None, gpu_poll_interval_seconds=self.gpu_interval)
            activities = row['process_activity']
            if not activities or age > 4.0 or not cache['devices']:
                row['gpu_reason'] = 'stale' if age > 4.0 else cache['reason']
                continue
            metrics, matched, devices, covered, known = [], set(), set(), 0, cache['complete']
            for item in activities:
                pid, start = item['pid'], item['start']
                values = [(gpu, sm) for (gpu, owner), sm in cache['records'].items() if owner == pid]
                valid = cache['selected'].get(pid) == start and (not values or cache['starts'].get(pid) == start)
                if valid:
                    try:
                        valid = self._start(pid) == start
                    except (OSError, ValueError, IndexError):
                        valid = False
                complete = bool(cache['complete'] and valid and all(sm is not None for _, sm in values))
                available = bool(valid and (any(sm is not None for _, sm in values) or not values and cache['complete']))
                total = sum(sm for _, sm in values if sm is not None) if valid else None
                item.update(gpu_available=available, gpu_utilization_percent=total if available else None,
                            gpu_complete=complete, gpu_sample_age_seconds=round(age, 3), gpu_sample_id=cache['id'])
                if values:
                    matched.add(pid)
                    devices.update(gpu for gpu, _ in values)
                    covered += sum(sm is not None for _, sm in values) if valid else 0
                known &= complete
                if available:
                    metrics.append(total)
            available = bool(covered or not matched and known and metrics)
            complete = bool(available and row['tree_complete'] and known)
            row.update(gpu_available=available, gpu_complete=complete,
                       gpu_utilization_percent=sum(metrics) if available else None,
                       gpu_covered=covered, gpu_process_count=len(matched), gpu_device_count=len(devices),
                       gpu_reason='' if complete else 'partial' if available else cache['reason'] or 'identity_or_counter_unknown')

    def _linux(self, roots, processes, now, elapsed):
        result, fd_sets, fd_complete, fd_reasons = {}, {}, {}, {}
        budget = [self.max_total_fds]
        root_thread_budgets = self._thread_shares(self.max_total_threads, roots)
        trees, associated, session_anchors = self._session_processes(roots, processes, now)
        collector_namespace = self._net_namespace('self')
        for root in roots:
            row = self._empty(self.sample_id)
            result[root] = row
            original, tree_complete = trees[root]
            pids = original + associated[root]
            process_thread_budgets = self._thread_shares(root_thread_budgets[root], pids)
            row.update(process_count=len(pids), tree_complete=tree_complete, interval_seconds=elapsed)
            if associated[root]:
                row.update(scope='session_processes', cpu_scope='session_processes', gpu_scope='session_processes',
                           io_scope='observed_session_process_threads', network_scope='tcp_session_processes',
                           associated_process_count=len(associated[root]),
                           associated_gpu_process_count=0)
            if not pids:
                self.roots.pop(root, None)
                continue
            try:
                root_stat = self._stat(root)
                start = root_stat['start']
            except (OSError, ValueError, IndexError):
                self.roots.pop(root, None)
                continue
            saved = self._record(root, start)
            row.update(identity=saved['identity'], epoch=saved['identity'])
            rss, inodes, memory_covered, all_fds = 0, set(), 0, tree_complete
            io_current, cpu_current, io_covered = {}, {}, 0
            parents, outputs = {}, {}
            verified_pids = set()
            io_complete = tree_complete
            fd_reason = '' if tree_complete else 'partial'
            for pid in pids:
                expected_parent = processes.get(pid, (None,))[0] if processes is not None else None
                if pid != root and expected_parent not in verified_pids and pid not in session_anchors[root]:
                    # Traversal uses an earlier discovery snapshot. If an
                    # ancestor moved, vanished or changed identity, its whole
                    # stale branch no longer proves ownership by this root.
                    io_complete = all_fds = False
                    continue
                try:
                    before = root_stat if pid == root else self._stat(pid)
                    if pid in session_anchors[root] and before['start'] != session_anchors[root][pid]:
                        raise ValueError('Associated process changed')
                except (OSError, ValueError, IndexError):
                    io_complete = all_fds = False
                    continue
                resident, owned, readable = None, set(), False
                try:
                    resident = self._rss(pid)
                except (OSError, ValueError, IndexError):
                    pass
                namespace = self._net_namespace(pid)
                if namespace is None or collector_namespace is None:
                    fd_reason = 'namespace_unavailable'
                elif namespace != collector_namespace:
                    fd_reason = 'namespace_mismatch'
                else:
                    owned, readable = self._fds(pid, budget)
                threads, threads_complete = self._thread_io(pid, before['start'], before['threads'],
                                                            [process_thread_budgets[pid]])
                output_files = self._output_files(pid)
                try:
                    after = self._stat(pid)
                    if (before['start'] != after['start'] or (pid == root and after['start'] != start)
                            or (expected_parent is not None and after['parent'] != expected_parent)):
                        raise ValueError('Process changed')
                except (OSError, ValueError, IndexError):
                    io_complete = all_fds = False
                    continue
                verified_pids.add(pid)
                parents[pid], outputs[pid] = after['parent'], output_files
                cpu_current[(pid, before['start'])] = (before['ticks'] / self.clock_ticks,)
                if resident is not None and resident >= 0:
                    rss += resident; memory_covered += 1
                inodes.update(owned); all_fds &= readable
                io_current.update(threads)
                io_covered += bool(threads)
                io_complete &= threads_complete and before['threads'] == after['threads']
            try:
                if self._start(root) != start:
                    raise ValueError('Root process changed')
            except (OSError, ValueError, IndexError):
                # A root can exit or be reused while its children are sampled.
                # Do not publish the old root's ledger under the new PID.
                self.roots.pop(root, None)
                result[root] = self._empty(self.sample_id, 'process_changed')
                continue
            row.update(memory_bytes=rss if memory_covered else None,
                       memory_covered=memory_covered, memory_available=memory_covered > 0,
                       memory_complete=tree_complete and memory_covered == len(pids),
                       memory_reason='' if tree_complete and memory_covered == len(pids) else 'partial')
            row['process_activity'] = self._activities(root, saved, cpu_current, io_current, parents, outputs, now)
            for activity in row['process_activity']:
                activity['association'] = 'claude_session_env' if activity['pid'] in associated[root] else 'process_tree'
            if associated[root] and self.gpu_cache is not None:
                observed_gpu = {pid for _, pid in self.gpu_cache['records']}
                row['associated_gpu_process_count'] = sum(
                    item['pid'] in observed_gpu and self.gpu_cache['starts'].get(item['pid']) == item['start']
                    for item in row['process_activity'] if item['association'] == 'claude_session_env')
            self._cpu(row, saved, cpu_current, now, tree_complete and len(cpu_current) == len(pids))
            changes, compared, unchanged, delta_time = self._delta(saved, 'io', io_current, now, 4)
            saved['io_totals'] = [x + y for x, y in zip(saved['io_totals'], changes)]
            available = bool(io_current)
            full = io_complete and available and unchanged and compared == len(io_current)
            row.update(io_available=available, io_complete=bool(full), io_covered=io_covered,
                       io_threads_covered=len(io_current),
                       io_reason='' if full else 'initializing' if io_complete and available and not compared else 'partial')
            if available:
                for index, name in enumerate(('io_read', 'io_write', 'disk_read', 'disk_write')):
                    row[name + '_bytes'] = saved['io_totals'][index]
                    row[name + '_per_second'] = round(changes[index] / delta_time, 3) if compared and delta_time else None
            fd_sets[root], fd_complete[root], fd_reasons[root] = inodes, all_fds, fd_reason

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
            ownership_unknown = any(not fd_complete.get(other, False) for other in roots if other != root)
            # An unreadable peer tree may hold a dup/inherited descriptor for
            # this same socket. Absence from its partial FD set proves nothing.
            exclusive = [(key, data) for key, data in owned
                         if len(owners[key[0]]) == 1 and not ownership_unknown]
            uncertain = ownership_unknown and any(len(owners[key[0]]) == 1 for key, _ in owned)
            row.update(network_socket_count=len(owned), network_shared=bool(shared),
                       network_shared_sockets=len(shared), network_attribution_unknown=uncertain)
            covered = [sum(data[direction] is not None for _, data in exclusive) for direction in ('rx', 'tx')]
            row.update(network_rx_covered_sockets=covered[0], network_tx_covered_sockets=covered[1])
            available = (not owned and network_complete) or any(covered)
            complete = network_complete and not shared and not uncertain and all(count == len(owned) for count in covered)
            row.update(network_available=available, network_complete=complete,
                       network_reason='' if complete else 'ownership_unknown' if uncertain else 'shared' if shared else 'partial')
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
        self._gpu(result, now)
        return result

    @staticmethod
    def _cpu_time(value):
        # macOS ps TIME is [[dd-]hh:]mm:ss[.fraction]. Never use %cpu's
        # lifetime-decayed estimate as an instantaneous resource rate.
        days = 0
        if '-' in value:
            day, value = value.split('-', 1)
            days = int(day)
        parts = value.split(':')
        if len(parts) not in (2, 3):
            raise ValueError('Malformed CPU time')
        seconds = float(parts[-1])
        minutes = int(parts[-2])
        hours = int(parts[-3]) if len(parts) == 3 else 0
        result = days * 86400 + hours * 3600 + minutes * 60 + seconds
        if min(days, hours, minutes, seconds) < 0 or not result < float('inf'):
            raise ValueError('Invalid CPU time')
        return result

    def _darwin(self, roots, processes, now, elapsed):
        trees = {root: self._tree(root, processes, roots, self.max_processes) for root in roots}
        selected = sorted({pid for pids, _ in trees.values() for pid in pids})
        values = {}
        if selected:
            try:
                command = ['ps', '-p', ','.join(map(str, selected)), '-o', 'pid=,lstart=,rss=,time=,ppid=']
                process = self.runner(command, text=True, capture_output=True, timeout=0.35,
                                      env=dict(os.environ, LC_ALL='C'))
                if process.returncode == 0:
                    for line in process.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 7:
                            try:
                                pid, resident = int(parts[0]), int(parts[6]) * 1024
                                try:
                                    cpu = self._cpu_time(parts[7]) if len(parts) in (8, 9) else None
                                except (ValueError, OverflowError):
                                    cpu = None
                                parent = int(parts[8]) if len(parts) == 9 else None
                                if resident >= 0:
                                    values[pid] = (' '.join(parts[1:6]), resident, cpu, parent)
                            except ValueError:
                                continue
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        result = {}
        for root, (pids, complete) in trees.items():
            row = self._empty(self.sample_id, 'unsupported')
            row.update(process_count=len(pids), tree_complete=complete, interval_seconds=elapsed)
            verified, parent_complete = set(), complete
            for pid in pids:
                value = values.get(pid)
                expected = processes.get(pid, (None,))[0] if processes is not None else None
                if value is None or (pid != root and expected not in verified):
                    parent_complete = False
                    continue
                if value[3] is None:
                    parent_complete = False
                    if pid != root:
                        continue
                elif expected is not None and value[3] != expected:
                    parent_complete = False
                    continue
                verified.add(pid)
            if root in verified:
                saved = self._record(root, values[root][0])
                resident = [values[pid][1] for pid in pids if pid in verified]
                row.update(identity=saved['identity'], epoch=saved['identity'],
                           memory_bytes=sum(resident), memory_covered=len(resident),
                           memory_available=True, memory_complete=parent_complete and len(resident) == len(pids),
                           memory_reason='' if parent_complete and len(resident) == len(pids) else 'partial')
                current = {(pid, values[pid][0]): (values[pid][2],) for pid in pids
                           if pid in verified and values[pid][2] is not None}
                row['process_activity'] = self._activities(root, saved, current, {},
                                                          {pid: values[pid][3] for pid in verified}, {}, now)
                self._cpu(row, saved, current, now, parent_complete and len(current) == len(pids))
            else:
                self.roots.pop(root, None)
                row['memory_reason'] = row['cpu_reason'] = 'unavailable'
            result[root] = row
        return result

    def sample_all(self, root_pids, processes, now=None, session_contexts=None):
        now = self.clock() if now is None else now
        processes = as_snapshot(processes)
        roots = tuple(sorted({pid for pid in root_pids if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0}))
        self.session_contexts = self._session_contexts(session_contexts, roots)
        contexts = tuple(sorted(self.session_contexts.items()))
        if roots == self.cached_roots and contexts == self.cached_contexts and self.last_sample is not None and self.last_sample <= now < self.next_sample:
            return {pid: dict(row) for pid, row in self.cached.items()}
        elapsed = now - self.last_sample if self.last_sample is not None and now > self.last_sample else None
        if elapsed is None or elapsed > self.max_gap:
            self.net_baselines.clear()
            for record in self.roots.values():
                record.update(io=None, io_time=None, cpu=None, cpu_time=None, net_time=None)
        self.sample_id += 1
        if not roots:
            rows = {}
        elif self.platform.startswith('linux'):
            rows = self._linux(roots, processes, now, elapsed)
        elif self.platform == 'darwin':
            rows = self._darwin(roots, processes, now, elapsed)
        else:
            rows = {pid: self._empty(self.sample_id, 'unsupported') for pid in roots}
        self.roots = {pid: row for pid, row in self.roots.items() if pid in roots}
        self.cached, self.cached_roots, self.cached_contexts = rows, roots, contexts
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
        self._cache_roots, self._cache_context = (), {}
        self._worker = None

    def _collect(self, roots, processes, started_at, session_contexts):
        try:
            samples = (self.collector.sample_all(roots, processes, now=started_at, session_contexts=session_contexts)
                       if session_contexts else self.collector.sample_all(roots, processes, now=started_at))
            rows = {pid: dict(row) for pid, row in samples.items()
                    if pid in roots and isinstance(row, dict)}
        except Exception:
            rows = {}  # Resource failure does not fail the CLI lifecycle snapshot.
        with self._lock:
            self._cache, self._cache_at = rows, started_at
            self._cache_roots = roots
            self._cache_context = self._contexts(roots, processes, session_contexts)
            self._busy = False

    @staticmethod
    def _contexts(roots, processes, session_contexts=None):
        # Discovery tables do not claim to know start times. This cheap check
        # still rejects changed owners/executables or vanished roots while a
        # fresh worker verifies actual kernel start identities asynchronously.
        return {pid: (processes[pid][0], processes[pid][2], (session_contexts or {}).get(pid))
                if processes is not None and pid in processes else (None, None, (session_contexts or {}).get(pid)) for pid in roots}

    def sample_all(self, root_pids, processes, now=None, session_contexts=None):
        now = self.clock() if now is None else now
        processes = as_snapshot(processes)
        roots = tuple(sorted({pid for pid in root_pids
                              if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0}))
        session_contexts = ProcessResources._session_contexts(session_contexts, roots)
        contexts = self._contexts(roots, processes, session_contexts)
        worker = None
        with self._lock:
            age = now - self._cache_at if self._cache_at is not None else None
            rows = ({pid: dict(row, sample_age_seconds=round(age, 3))
                     for pid, row in self._cache.items()
                     if pid in roots and contexts[pid] == self._cache_context.get(pid)}
                    if age is not None and 0 <= age <= self.max_age and roots == self._cache_roots else {})
            eligible = (self._last_started is None or now < self._last_started or
                        now - self._last_started >= self.interval)
            if roots and not self._busy and eligible:
                self._busy, self._last_started = True, now
                copied = as_snapshot(processes)
                worker = threading.Thread(target=self._collect, args=(roots, copied, now, dict(session_contexts)),
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
