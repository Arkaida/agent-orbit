"""Immutable process tables with one reusable parent/child index per sample.

Entries deliberately contain no command arguments. Process identity counters
are verified by the resource sampler using platform start times; this table is
only a bounded traversal aid, never proof of conversation ownership by itself.
"""
from collections import deque
from collections.abc import Mapping
import os
import re
from types import MappingProxyType


class ProcessSnapshot(Mapping):
    """Read-only ``pid -> (ppid, state, executable)`` plus a shared index."""
    def __init__(self, processes=()):
        values, children = {}, {}
        for pid, entry in dict(processes).items():
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or
                    not isinstance(entry, (tuple, list)) or len(entry) < 3):
                continue
            parent, state, executable = entry[:3]
            if (not isinstance(parent, int) or isinstance(parent, bool) or parent < 0 or
                    not isinstance(state, str) or not isinstance(executable, str)):
                continue
            values[pid] = (parent, state, executable)
            children.setdefault(parent, []).append(pid)
        self._processes = MappingProxyType(values)
        self.children = MappingProxyType({parent: tuple(sorted(pids)) for parent, pids in children.items()})

    def __getitem__(self, pid):
        return self._processes[pid]

    def __iter__(self):
        return iter(self._processes)

    def __len__(self):
        return len(self._processes)

    def descendants(self, root, boundaries=(), limit=128):
        """Return a bounded live tree, stopping before other selected roots.

        A missing process table cannot establish a complete subtree. Returning
        the requested root still allows a caller to read its own /proc record.
        Cycles and limits explicitly make the scope partial instead of hanging.
        """
        if root not in self:
            return [root], False
        boundaries = set(boundaries)
        limit = max(1, int(limit))
        pending, seen, found, complete = deque([root]), set(), [], True
        while pending:
            pid = pending.popleft()
            if pid in seen:
                complete = False
                continue
            seen.add(pid)
            if pid != root and pid in boundaries:
                continue
            entry = self.get(pid)
            if entry is None:
                complete = False
                continue
            if 'Z' in entry[1]:
                continue
            if len(found) >= limit:
                complete = False
                break
            found.append(pid)
            pending.extend(self.children.get(pid, ()))
        return found, complete

    def find_agent(self, root, agents=('codex', 'claude')):
        """Nearest active agent; a suspended agent is a subtree boundary."""
        agents = frozenset(agents)
        pending, seen, stopped = deque([root]), set(), []
        while pending:
            pid = pending.popleft()
            if pid in seen:
                continue
            seen.add(pid)
            entry = self.get(pid)
            if entry:
                agent = os.path.basename(entry[2])
                if agent in agents and 'Z' not in entry[1]:
                    if not re.search('[Tt]', entry[1]):
                        return pid, entry[1], agent
                    stopped.append((pid, entry[1], agent))
                    continue
            pending.extend(self.children.get(pid, ()))
        return stopped[0] if stopped else (0, '', '')


def as_snapshot(processes):
    """Keep existing immutable samples intact, including failed discovery."""
    if processes is None or isinstance(processes, ProcessSnapshot):
        return processes
    return ProcessSnapshot(processes)
