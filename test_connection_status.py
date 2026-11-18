import unittest
from types import SimpleNamespace

from desktop_bridge import FeedState, connection_status


class ConnectionStatusTests(unittest.TestCase):
    def stream(self, packet=10, error='', process=True):
        return SimpleNamespace(host='myserver', last_packet=packet, error=error,
                               process=object() if process else None)

    def model(self, error=''):
        return SimpleNamespace(remote_error=error)

    def test_fresh_sample_is_live(self):
        result = connection_status(self.stream(), self.model(), '', 10.7)
        self.assertTrue(result['connected'])
        self.assertEqual(result['connection_state'], 'live')

    def test_late_sample_is_unconfirmed_but_not_disconnected(self):
        result = connection_status(self.stream(), self.model(), '', 13)
        self.assertFalse(result['connected'])
        self.assertEqual(result['connection_state'], 'delayed')
        self.assertIn('等待远端更新', result['message'])
        self.assertNotIn('断开', result['message'])
        self.assertEqual(result['received_age'], 3)

    def test_exited_ssh_is_reconnecting_even_with_a_recent_old_sample(self):
        result = connection_status(self.stream(error='Connection reset by peer', process=False), self.model(), '', 10.2)
        self.assertFalse(result['connected'])
        self.assertEqual(result['connection_state'], 'reconnecting')
        self.assertIn('重连', result['message'])

    def test_missing_process_cannot_be_live(self):
        result = connection_status(self.stream(process=False), self.model(), '', 10.2)
        self.assertEqual(result['connection_state'], 'reconnecting')

    def test_initial_connection_has_no_old_task_state(self):
        result = connection_status(self.stream(packet=None), self.model(), '', 10)
        self.assertEqual(result['connection_state'], 'connecting')
        self.assertIsNone(result['received_age'])
        self.assertFalse(result['connected'])

    def test_probe_and_configuration_errors_are_distinct_from_transport_loss(self):
        self.assertEqual(connection_status(self.stream(), self.model('cannot read tmux'), '', 10.2)['connection_state'], 'remote_error')
        self.assertEqual(connection_status(self.stream(), self.model(), 'invalid follow list', 10.2)['connection_state'], 'config_error')

    def test_delay_breaks_completion_continuity_without_false_outage_sound(self):
        state = FeedState()
        state.accept(dict(panes=[dict(session='x', pane_id='%1', state='running', process_id=1, screen='same', preview=[])]), ['x'], 1, 0, 0)
        state.connection(True, 0, 0, 'live')
        state.connection(False, 3, 3, 'waiting', waiting=True)
        state.connection(False, 8, 8, 'waiting', waiting=True)
        self.assertFalse(state.continuous)
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.error_events, [])
        # A real failure still alerts once and clears on confirmed recovery.
        state.connection(False, 9, 9, 'SSH closed')
        state.connection(False, 12.1, 12.1, 'SSH closed')
        self.assertEqual(len(state.error_events), 1)
        self.assertEqual(state.error_events[0]['kind'], 'connection')
        state.connection(True, 13, 13, 'live')
        self.assertEqual(state.error_events, [])


if __name__ == '__main__':
    unittest.main()
