import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from desktop_bridge import FeedState, aggregate_connection_status, connection_status, run
from configuration import defaults


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

    def test_ssh_failure_is_actionable_and_not_initializing(self):
        health = connection_status(self.stream(packet=None, error='ssh: connect to host 0.0.0.1 port 22: No route to host', process=False), self.model(), '', 10)
        result = aggregate_connection_status([dict(id='remote', label='远端', **health)], '')
        self.assertFalse(result['connected'])
        self.assertEqual(result['connection_state'], 'reconnecting')
        self.assertIn('检查主机设置和网络', result['message'])
        self.assertNotIn('0.0.0.1', result['message'])

    def test_config_error_is_visible_without_any_running_sources(self):
        result = aggregate_connection_status([], '请至少启用一种监控来源')
        self.assertFalse(result['connected'])
        self.assertEqual(result['connection_state'], 'config_error')
        self.assertIn('打开设置', result['message'])

    def test_partial_connection_retains_failed_source_reason(self):
        result = aggregate_connection_status([
            dict(label='本机', connected=True, connection_state='live', message='实时监控'),
            dict(label='远端', connected=False, connection_state='reconnecting', message='SSH 认证失败，请检查密钥与主机设置')], '')
        self.assertTrue(result['connected'])
        self.assertEqual(result['connection_state'], 'partial')
        self.assertIn('SSH 认证失败', result['message'])

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


class BridgeStartupTests(unittest.TestCase):
    def test_remote_failure_does_not_enable_disabled_local_source(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(defaults(), local_enabled=False, remote_enabled=True, host='1')
            Path(folder, 'settings.json').write_text(json.dumps(settings))
            stream = SimpleNamespace(host='1', last_packet=None, error='No route to host', process=None,
                                     connections=0, total_frames=0, cursors={}, interval=.5,
                                     subscribe=lambda _: None, poll=lambda: [], close=lambda: None)
            output = io.StringIO()
            with patch.dict(os.environ, AGENT_ORBIT_DATA_DIR=folder), patch('desktop_bridge.Stream', return_value=stream), patch('desktop_bridge.LocalStream') as local, patch('desktop_bridge.signal.signal'), patch('desktop_bridge.time.sleep', side_effect=KeyboardInterrupt), contextlib.redirect_stdout(output):
                with self.assertRaises(KeyboardInterrupt):
                    run(argparse.Namespace(host=None, socket=None))
            local.assert_not_called()
            packet = json.loads(output.getvalue().splitlines()[0])
            self.assertEqual(packet['connection_state'], 'reconnecting')
            self.assertEqual([c['id'] for c in packet['connections']], ['remote'])
            self.assertFalse(json.loads(Path(folder, 'settings.json').read_text())['local_enabled'])

    def test_invalid_settings_emit_first_frame_and_recover_after_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            home = data / 'empty-codex-home'; home.mkdir()
            settings_file = data / 'settings.json'
            settings_file.write_text(json.dumps(dict(defaults(), local_enabled=False, remote_enabled=False)))
            process = subprocess.Popen([sys.executable, '-B', '-u', str(Path(__file__).with_name('desktop_bridge.py'))],
                                       env={'PATH': '/usr/bin:/bin', 'AGENT_ORBIT_DATA_DIR': folder, 'CODEX_HOME': str(home)},
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ)
            def packet():
                self.assertTrue(selector.select(3), 'bridge did not produce a first frame')
                return json.loads(process.stdout.readline())
            try:
                initial = packet()
                self.assertEqual(initial['connection_state'], 'config_error')
                self.assertEqual(initial['connections'], [])
                self.assertIsNone(process.poll())
                settings_file.write_text(json.dumps(dict(defaults(), local_enabled=True, remote_enabled=False, local_codex_home=str(home))))
                deadline = time.monotonic() + 3
                latest = initial
                while time.monotonic() < deadline and latest['connection_state'] == 'config_error':
                    latest = packet()
                self.assertNotEqual(latest['connection_state'], 'config_error')
                self.assertEqual([c['id'] for c in latest['connections']], ['local'])
            finally:
                process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
                selector.close(); process.stdout.close(); process.stderr.close()


if __name__ == '__main__':
    unittest.main()
