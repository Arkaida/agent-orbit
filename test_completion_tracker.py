import unittest

from completion_tracker import CompletionTracker


def row(state, pane_id='%1', session='UISG'):
    return dict(pane_id=pane_id, session=session, state=state)


class CompletionTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = CompletionTracker()

    def sample(self, state, mono, wall=None, epoch=0, pane_id='%1'):
        return self.tracker.update([row(state, pane_id)], mono,
                                   1000 + mono if wall is None else wall, epoch)

    def test_initial_idle_never_notifies(self):
        self.sample('idle', 0)
        rows, events = self.sample('idle', 30)
        self.assertEqual(events, [])
        self.assertIsNone(rows[0]['completed_at'])

    def test_running_idle_stable_once_and_timestamp_first_idle(self):
        self.sample('running', 0)
        self.assertEqual(self.sample('idle', 1)[1], [])
        self.assertEqual(self.sample('idle', 1.9)[1], [])
        rows, events = self.sample('idle', 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['completed_at'], 1001)
        self.assertEqual(rows[0]['completed_at'], 1001)
        self.assertEqual(rows[0]['completion_id'], events[0]['id'])
        self.assertEqual(self.sample('idle', 10)[1], [])

    def test_brief_idle_does_not_emit(self):
        self.sample('running', 0)
        self.sample('idle', 1)
        self.sample('running', 1.7)
        self.assertEqual(self.sample('idle', 2)[1], [])
        rows, events = self.sample('idle', 3)
        self.assertEqual(len(events), 1)
        self.assertEqual(rows[0]['completed_at'], 1002)

    def test_new_work_can_finish_again_without_losing_previous_finish(self):
        self.sample('running', 0)
        self.sample('idle', 1)
        first = self.sample('idle', 2)[1][0]
        rows, events = self.sample('running', 3)
        self.assertEqual(events, [])
        self.assertEqual(rows[0]['completed_at'], first['completed_at'])
        self.sample('running', 4)
        self.sample('idle', 5)
        rows, events = self.sample('idle', 6)
        self.assertEqual(len(events), 1)
        self.assertNotEqual(first['id'], events[0]['id'])
        self.assertEqual(rows[0]['completed_at'], 1005)

    def test_uncertain_state_breaks_continuity(self):
        for state in ['approval', 'other', 'history', 'unknown', 'stopped']:
            with self.subTest(state=state):
                self.tracker = CompletionTracker()
                self.sample('running', 0)
                self.sample('idle', 1)
                self.sample(state, 1.5)
                self.sample('idle', 2)
                self.assertEqual(self.sample('idle', 5)[1], [])

    def test_reconnect_idle_cannot_finish_old_running_work(self):
        self.sample('running', 0)
        self.sample('idle', 1)
        self.sample('idle', 2, epoch=1)
        self.assertEqual(self.sample('idle', 5, epoch=1)[1], [])

    def test_reconnect_keeps_old_completion_and_history(self):
        self.sample('running', 0)
        self.sample('idle', 1)
        event = self.sample('idle', 2)[1][0]
        rows, events = self.sample('idle', 3, epoch=1)
        self.assertEqual(events, [])
        self.assertEqual(rows[0]['completed_at'], event['completed_at'])
        self.assertEqual(self.tracker.events, [event])

    def test_missing_pane_breaks_continuity(self):
        self.sample('running', 0)
        self.tracker.update([], 1, 1001)
        self.sample('idle', 2)
        self.assertEqual(self.sample('idle', 5)[1], [])

    def test_different_pane_in_same_session_is_new(self):
        self.sample('running', 0)
        rows, events = self.sample('idle', 3, pane_id='%2')
        self.assertEqual(events, [])
        self.assertIsNone(rows[0]['completed_at'])

    def test_input_rows_are_not_modified(self):
        original = row('running')
        self.tracker.update([original], 0, 1000)
        self.assertNotIn('completed_at', original)


if __name__ == '__main__':
    unittest.main()
