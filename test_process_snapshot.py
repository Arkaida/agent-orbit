import unittest
from unittest.mock import patch

from process_snapshot import ProcessSnapshot, as_snapshot


class ProcessSnapshotTests(unittest.TestCase):
    def test_input_is_detached_and_existing_snapshots_reuse_the_same_index(self):
        table = {1: (0, 'S', 'shell'), 2: [1, 'S', '/bin/codex']}
        snapshot = as_snapshot(table)
        table[3] = (2, 'S', 'python')
        table[2][0] = 99
        self.assertEqual(snapshot[2], (1, 'S', '/bin/codex'))
        self.assertNotIn(3, snapshot)
        with self.assertRaises(TypeError):
            snapshot[2] = (99, 'S', 'changed')
        with self.assertRaises(TypeError):
            snapshot.children[1] = (99,)
        with patch.object(ProcessSnapshot, '__init__', side_effect=AssertionError('index rebuilt')):
            self.assertIs(as_snapshot(snapshot), snapshot)
            self.assertEqual(snapshot.find_agent(1), (2, 'S', 'codex'))
            self.assertEqual(snapshot.descendants(1), ([1, 2], True))
        self.assertIsNone(as_snapshot(None))

    def test_live_tree_stops_at_independent_roots_and_excludes_zombie_subtrees(self):
        snapshot = ProcessSnapshot({1: (0, 'S', 'codex'), 2: (1, 'S', 'python'),
                                    3: (1, 'S', 'claude'), 4: (3, 'S', 'node'),
                                    5: (1, 'Z', 'shell'), 6: (5, 'S', 'stale-child')})
        self.assertEqual(snapshot.descendants(1, boundaries={1, 3}), ([1, 2], True))
        self.assertEqual(snapshot.descendants(3, boundaries={1, 3}), ([3, 4], True))
        self.assertEqual(snapshot.descendants(1), ([1, 2, 3, 4], True))

    def test_missing_root_cycles_and_budget_are_explicitly_partial(self):
        self.assertEqual(ProcessSnapshot().descendants(42), ([42], False))
        cycle = ProcessSnapshot({1: (2, 'S', 'shell'), 2: (1, 'S', 'worker')})
        self.assertEqual(cycle.descendants(1), ([1, 2], False))
        snapshot = ProcessSnapshot({1: (99, 'S', 'root'), 2: (1, 'S', 'child'), 3: (2, 'S', 'child')})
        self.assertEqual(snapshot.descendants(1, limit=2), ([1, 2], False))
        # An external parent does not make the known root's descendant scope incomplete.
        self.assertEqual(snapshot.descendants(1), ([1, 2, 3], True))

    def test_agent_search_never_borrows_a_worker_below_a_suspended_root(self):
        snapshot = ProcessSnapshot({1: (0, 'S', 'shell'), 2: (1, 'Tl', '/bin/claude'),
                                    3: (2, 'S', '/bin/codex'), 4: (1, 'S', '/bin/codex'),
                                    5: (4, 'S', '/bin/claude')})
        self.assertEqual(snapshot.find_agent(1), (4, 'S', 'codex'))
        self.assertEqual(snapshot.find_agent(2), (2, 'Tl', 'claude'))
        self.assertEqual(snapshot.find_agent(4), (4, 'S', 'codex'))
        self.assertEqual(snapshot.find_agent(99), (0, '', ''))

    def test_malformed_records_are_excluded_without_exposing_command_arguments(self):
        snapshot = ProcessSnapshot({1: (0, 'S', '/bin/codex', 'private ignored argument'),
                                    2: ('bad parent', 'S', 'x'), 3: (1, None, 'x'),
                                    '4': (1, 'S', 'x'), -5: (0, 'S', 'x')})
        self.assertEqual(dict(snapshot), {1: (0, 'S', '/bin/codex')})


if __name__ == '__main__':
    unittest.main()
