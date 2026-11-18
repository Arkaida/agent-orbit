import unittest
from error_tracker import ErrorTracker


def row(state='running', signature='', pane='%1'):
    return dict(pane_id=pane, session='test', state=state,
                error_signature=signature, error_summary='服务暂不可用')


class ErrorTrackerTests(unittest.TestCase):
    def test_existing_error_silent_new_error_once(self):
        tracker = ErrorTracker()
        self.assertEqual(tracker.update([row('error', '503')], 1), [])
        tracker.update([row()], 2)
        events = tracker.update([row('error', '503')], 3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['occurred_at'], 3)
        self.assertEqual(tracker.update([row('error', '503')], 4), [])

    def test_reconnect_or_history_does_not_replay_error(self):
        tracker = ErrorTracker()
        tracker.update([row()], 1)
        self.assertEqual(tracker.update([row('error', '503')], 2, epoch=1), [])
        tracker.update([row('history')], 3, epoch=1)
        self.assertEqual(tracker.update([row('error', '503')], 4, epoch=1), [])

    def test_distinct_error_and_next_run(self):
        tracker = ErrorTracker()
        tracker.update([row()], 1)
        first = tracker.update([row('error', '503')], 2)[0]
        second = tracker.update([row('error', '401')], 3)[0]
        self.assertNotEqual(first['id'], second['id'])
        tracker.update([row()], 4)
        self.assertEqual(len(tracker.update([row('error', '401')], 5)), 1)


if __name__ == '__main__':
    unittest.main()
