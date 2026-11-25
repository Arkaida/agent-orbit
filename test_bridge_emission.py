"""Display throttling must never throttle collector or notification accounting."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from configuration import defaults
from desktop_bridge import FrameEmissionGate, run, source_payload
from manual_tracker import ManualRunningTracker


def frame(state='idle', **fields):
    row = dict(session='alpha', pane_id='%1', process_id=42, state=state,
               screen='synthetic ' + state, preview=[], output_total=0)
    row.update(fields)
    return dict(panes=[row])


class EmissionGateTests(unittest.TestCase):
    def test_quiet_heartbeats_have_one_second_cadence(self):
        gate = FrameEmissionGate()
        self.assertTrue(gate.should_emit(10))
        for offset in (.1, .2, .5, .999):
            self.assertFalse(gate.should_emit(10 + offset))
        self.assertTrue(gate.should_emit(11))
        self.assertFalse(gate.should_emit(11.1))
        self.assertTrue(gate.should_emit(12))

    def test_data_configuration_and_status_edges_are_immediate(self):
        gate = FrameEmissionGate()
        self.assertTrue(gate.should_emit(0))
        for instant in (.01, .02, .021, .1):
            self.assertTrue(gate.should_emit(instant, changed=True))
        self.assertFalse(gate.should_emit(1.099))
        self.assertTrue(gate.should_emit(1.1))

    def test_monotonic_clock_reset_cannot_silence_delivery(self):
        gate = FrameEmissionGate()
        self.assertTrue(gate.should_emit(100))
        self.assertTrue(gate.should_emit(0))
        self.assertFalse(gate.should_emit(.5))


class DialogueDeliveryTests(unittest.TestCase):
    def test_desktop_receives_metrics_without_duplicate_body_and_offline_is_unavailable(self):
        output = dict(epoch='synthetic-output', sample_id=2, total_chars=12, available=True,
                      complete=False, sample_age_seconds=0, source='assistant_screen')
        raw = dict(session='alpha', pane_id='%1', state='running',
                   dialogue_text='private body ' * 1000, dialogue_source='assistant_screen',
                   dialogue_identity='raw-identity', dialogue_available=True,
                   dialogue_complete=False, dialogue_sample_id='raw-sample', dialogue_output=output)
        state = SimpleNamespace(rows=[raw], events=[], notification_events=[], error_events=[])
        live = source_payload('remote', state, dict(connected=True), [])['rows'][0]
        self.assertEqual(live['dialogue_output'], output)
        self.assertFalse(any(key.startswith('dialogue_') and key != 'dialogue_output' for key in live))
        self.assertNotIn('private body', json.dumps(live))
        offline = source_payload('remote', state, dict(connected=False), [])['rows'][0]
        self.assertIsNone(offline['dialogue_output'])
        self.assertEqual(raw['dialogue_output'], output)


class BridgeEmissionTests(unittest.TestCase):
    def run_bridge(self, scheduled_frames, duration, configure=None, transport=None):
        clock = SimpleNamespace(now=0.0)
        settings = dict(defaults(), local_enabled=False, remote_enabled=True, host='synthetic-host')
        stream = SimpleNamespace(host='synthetic-host', last_packet=None, error='', process=object(),
                                 connections=1, total_frames=0, cursors={}, interval=.5,
                                 subscribe=Mock(), close=Mock())

        def poll():
            tick = round(clock.now * 10)
            if transport:
                transport(tick, stream)
            values = scheduled_frames.get(tick, [])
            if values:
                stream.last_packet = clock.now
                stream.total_frames += len(values)
            return values

        stream.poll = Mock(side_effect=poll)

        def sleep(_):
            clock.now = round(clock.now + .1, 8)
            if clock.now > duration + .00001:
                raise KeyboardInterrupt

        def configuration(_):
            value = dict(settings)
            if configure:
                configure(round(clock.now * 10), value)
            return value

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            manual = ManualRunningTracker(Path(folder) / 'manual-overrides.json')
            manual.poll = Mock(wraps=manual.poll)
            with patch('desktop_bridge.load_settings', side_effect=configuration) as load, \
                    patch('desktop_bridge.read_follow', return_value=[]), \
                    patch('desktop_bridge.data_dir', return_value=Path(folder)), \
                    patch('desktop_bridge.Stream', return_value=stream), \
                    patch('desktop_bridge.LocalStream') as local, \
                    patch('desktop_bridge.ManualRunningTracker', return_value=manual), \
                    patch('desktop_bridge.signal.signal'), \
                    patch('desktop_bridge.time.monotonic', side_effect=lambda: clock.now), \
                    patch('desktop_bridge.time.time', side_effect=lambda: 1000 + clock.now), \
                    patch('desktop_bridge.time.sleep', side_effect=sleep), \
                    contextlib.redirect_stdout(output):
                with self.assertRaises(KeyboardInterrupt):
                    run(argparse.Namespace(host=None, socket=None))
            local.assert_not_called()
            return ([json.loads(line) for line in output.getvalue().splitlines()],
                    stream.poll.call_count, manual.poll.call_count, load.call_count)

    def test_quiet_loop_still_polls_commands_configuration_and_ages_wait(self):
        packets, polls, commands, loads = self.run_bridge({0: [frame()]}, 1.9)
        self.assertEqual([round(p['emitted_at'] - 1000, 2) for p in packets], [0, 1])
        self.assertEqual(polls, 20)
        self.assertEqual(commands, polls)
        self.assertGreaterEqual(loads, 4)
        self.assertEqual(len({p['rows'][0]['response']['episode_id'] for p in packets}), 1)
        self.assertEqual(packets[-1]['rows'][0]['response']['elapsed_seconds'], 1)
        self.assertTrue(all(not p['events'] and not p['error_events'] for p in packets))
        self.assertEqual(packets[-1]['received_frames'], 1)

    def test_real_frames_and_brief_error_delivery_are_not_coalesced_away(self):
        error = frame('error', error_kind='fatal', error_signature='synthetic-incident',
                      error_summary='Synthetic failure', attention_level='error')
        packets, _, _, _ = self.run_bridge({
            0: [frame('running')],
            2: [error, frame('running')],  # One transport read can contain both.
            5: [frame()],
            15: [frame()],
        }, 1.6)
        incident = next(p for p in packets if round(p['emitted_at'] - 1000, 2) == .2)
        self.assertEqual(incident['rows'][0]['state'], 'running')
        self.assertEqual(len(incident['error_events']), 1)
        self.assertEqual(incident['error_events'][0]['kind'], 'fatal')
        self.assertEqual(incident['active_error_events'], [])
        self.assertEqual(len(packets[-1]['events']), 1)
        self.assertEqual(packets[-1]['received_frames'], 5)
        self.assertEqual(len({event['id'] for p in packets for event in p['events']}), 1)

    def test_configuration_change_is_delivered_between_heartbeats(self):
        def configure(tick, value):
            value['sound'] = tick < 5
        packets, _, _, _ = self.run_bridge({0: [frame()]}, .6, configure=configure)
        self.assertEqual([round(p['emitted_at'] - 1000, 2) for p in packets], [0, .5])

    def test_transport_edge_is_delivered_without_waiting_for_heartbeat(self):
        def transport(tick, stream):
            if tick >= 2:
                stream.error = 'Connection reset by peer'
                stream.process = None
        packets, _, _, _ = self.run_bridge({0: [frame()]}, .3, transport=transport)
        self.assertEqual([round(p['emitted_at'] - 1000, 2) for p in packets], [0, .2])
        self.assertFalse(packets[-1]['connected'])
        self.assertEqual(packets[-1]['connection_state'], 'reconnecting')


if __name__ == '__main__':
    unittest.main()
