import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from completion_tracker import CompletionTracker
from desktop_bridge import FeedState
from remote_probe import ClaudeWatcher, RolloutWatcher, analyse_claude_screen, analyse_screen, apply_codex_background, find_agent, issue
from process_snapshot import ProcessSnapshot


def claude(body='● Finished.', draft='', working=''):
    return body + '\n' + working + '\n──────────────── ultracode ─\n❯ ' + draft + '\n─────────────────\n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 6 agents'


class ClaudeScreenTests(unittest.TestCase):
    def test_long_reply_anchor_outside_viewport_keeps_observing_real_output(self):
        from activity_tracker import ActivityTracker
        tracker = ActivityTracker()
        context = claude('❯ user request\n  wrapped user input\n● Long explanation\n' + '\n'.join('  response line {}'.format(i) for i in range(70)),
                         draft='private draft', working='✻ Thinking… (42s · ↓ 100 tokens)')
        viewport = '\n'.join(context.splitlines()[-36:])
        self.assertFalse(analyse_claude_screen(viewport)['dialogue_available'])
        first = analyse_claude_screen(viewport, dialogue_context=context)
        self.assertTrue(first['dialogue_available'])
        self.assertNotIn('private draft', first['dialogue_text'])
        self.assertNotIn('user input', first['dialogue_text'])
        tracker.update([dict(first, pane_id='%1', process_id=1)], 0)
        updated = context.replace('  response line 69', '  response line 69\n  NEW RESULT')
        second = analyse_claude_screen('\n'.join(updated.splitlines()[-36:]), dialogue_context=updated)
        observed = tracker.update([dict(second, pane_id='%1', process_id=1)], 1)[0]
        self.assertEqual(observed['dialogue_output']['total_chars'], len('NEWRESULT'))
        self.assertFalse(analyse_claude_screen(viewport, in_mode=True, dialogue_context=context)['dialogue_available'])
        historical = viewport.replace('  response line 69', '  response line 69\nBack to bottom')
        self.assertFalse(analyse_claude_screen(historical, dialogue_context=context)['dialogue_available'])

    def test_dialogue_excludes_tools_input_and_claude_status_chrome(self):
        result = analyse_claude_screen(claude('❯ user request\n  wrapped user request\n● GPT context explanation\n  second sentence\n● Bash(python experiment.py)\n  ⎿ many rows of tool output\n● Experiment result\n  useful conclusion\n✻ Churned for 54s · done 1:48 AM\n  2% until auto-compact',
                                             draft='private question', working='✻ Thinking… (42s · ↓ 100 tokens)'))
        self.assertTrue(result['dialogue_available'])
        self.assertEqual(result['dialogue_text'], 'GPT context explanation\n  second sentence\nExperiment result\n  useful conclusion')
        self.assertFalse(analyse_claude_screen(claude('● Previous answer'), in_mode=True)['dialogue_available'])

    def test_dot_bullet_in_an_answer_does_not_mean_claude_is_running(self):
        for bullet in ['· Further work…', '* Further work…', '* Compare results… (3 samples)']:
            result = analyse_claude_screen(claude('● Finished.\n' + bullet))
            self.assertEqual(result['state'], 'idle', bullet)
            self.assertIn(bullet, result['preview'])

    def test_every_claude_spinner_frame_is_running_and_excluded_from_activity(self):
        from activity_tracker import ActivityTracker
        tracker = ActivityTracker()
        for index, glyph in enumerate('·*.✢✳✶✻✽✱✷⠁'):
            screen = claude('● Same tool output', working=glyph + ' Drizzling… ({}s · ↓ 52.3k tokens · thinking with xhigh effort)'.format(index))
            result = analyse_claude_screen(screen)
            self.assertEqual(result['state'], 'running', glyph)
            self.assertNotIn('Drizzling', result['activity'], glyph)
            observed = tracker.update([dict(result, pane_id='%1', session='claude', process_id=1)], index)[0]
            self.assertEqual(observed['output_total'], 0, glyph)
        result = analyse_claude_screen(claude('● New real tool output', working='· Drizzling… (9s · ↓ 53k tokens)'))
        observed = tracker.update([dict(result, pane_id='%1', session='claude', process_id=1)], 9)[0]
        self.assertGreater(observed['output_total'], 0)

    def test_idle_footer_and_finished_spinner_are_not_work_or_alerts(self):
        result = analyse_claude_screen(claude('● Result\n✻ Churned for 54s · done 1:48 AM\n  2% until auto-compact'))
        self.assertEqual(result['state'], 'idle')
        self.assertEqual(result['error_kind'], '')
        self.assertEqual(result['preview'], ['● Result'])
        self.assertEqual(analyse_claude_screen(claude(draft='check progress'))['status'], '等待输入（有草稿）')

    def test_work_overrides_visible_prompt(self):
        result = analyse_claude_screen(claude(working='✻ Thinking… (42s · ↓ 100 tokens)'))
        self.assertEqual(result['state'], 'running')
        self.assertNotIn('Thinking', '\n'.join(result['preview']))

    def test_api_failures_and_retry(self):
        for code in ('502', '503', '429', '401', '403'):
            result = analyse_claude_screen(claude('  ⎿ API Error: ' + code + ' request failed'))
            self.assertEqual(result['state'], 'error')
            self.assertIn(code, result['error_summary'])
        result = analyse_claude_screen(claude('  ⎿ API Error: 503 Service Unavailable\n    Retrying in 2 seconds… (attempt 2/10)'))
        self.assertEqual(result['error_kind'], 'retrying')
        self.assertEqual(result['state'], 'running')

    def test_new_work_and_success_clear_old_errors(self):
        for rest in ('\n❯ try again\n● Done.', '\n✻ Brewed for 3s · done 2:00 AM'):
            self.assertEqual(analyse_claude_screen(claude('API Error: 503' + rest))['state'], 'idle')
        self.assertEqual(analyse_claude_screen(claude('API Error: 503', working='✻ Working…'))['error_kind'], '')
        self.assertEqual(analyse_claude_screen(claude('● Tests returned HTTP 503.'))['state'], 'idle')

    def test_interrupt_approval_and_question(self):
        self.assertEqual(analyse_claude_screen(claude('Interrupted · What should Claude do instead?'))['state'], 'interrupted')
        approval = 'Do you want to proceed?\n❯ 1. Yes\n  2. No\nEnter to confirm · Esc to cancel'
        self.assertEqual(analyse_claude_screen(approval)['state'], 'approval')
        question = 'Choose a database\n❯ PostgreSQL\n  SQLite\nEnter to select · Esc to cancel'
        self.assertEqual(analyse_claude_screen(question)['error_kind'], 'question')

    def test_process_and_history_overrides(self):
        self.assertEqual(analyse_claude_screen(claude(), agent_alive=False)['state'], 'stopped')
        self.assertEqual(analyse_claude_screen(claude(), agent_alive=True, process_state='Tl')['state'], 'paused')
        self.assertEqual(analyse_claude_screen(claude(), in_mode=True)['state'], 'history')
        self.assertEqual(analyse_claude_screen(claude('old answer\nJump to bottom (ctrl+End) ↓'))['state'], 'history')
        self.assertEqual(analyse_claude_screen(claude(), process_check=False)['state'], 'unknown')


class AgentTests(unittest.TestCase):
    def test_nearest_active_root_excludes_subagent_and_old_suspended_agent(self):
        processes = {1: (0, 'S', 'zsh'), 2: (1, 'Tl', 'claude'), 3: (1, 'Sl', 'codex'), 4: (3, 'Sl', 'claude')}
        self.assertEqual(find_agent(processes, 1), (3, 'Sl', 'codex'))
        self.assertEqual(find_agent({1: (0, 'S', 'bash'), 2: (1, 'Sl', 'claude')}, 1)[2], 'claude')
        self.assertEqual(find_agent(None, 1), (None, '', ''))
        self.assertEqual(find_agent({1: (0, 'S', 'zsh')}, 1), (0, '', ''))

    def test_remote_alias_owner_is_shared_even_when_other_session_is_not_selected(self):
        import remote_probe
        table = ProcessSnapshot({1: (0, 'S', 'zsh'), 42: (1, 'S', 'codex')})
        captured = []
        listing = ('watched\t0\t0\t%1\tcodex\t/work\t0\t1\t\t0\t6\n'
                   'unwatched\t0\t0\t%2\tcodex\t/work\t0\t1\t\t0\t6\n')
        def run(args, **kwargs):
            if 'list-panes' in args:
                output = listing
            elif 'capture-pane' in args:
                self.assertIn('-S', args)
                self.assertIn('-256', args)
                captured.append(args[-1])
                output = '■ API Error: 503\nBack to bottom\n● Old assistant anchor\n  previous result\n' + '\n' * 10 + '» Ask Codex to do anything\nGPT-6\n'
            else:
                raise AssertionError('Unexpected external command')
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr='')
        with patch.object(remote_probe, 'read_processes', return_value=table), \
                patch.object(remote_probe.subprocess, 'run', side_effect=run), \
                patch.object(remote_probe, 'find_agent', wraps=remote_probe.find_agent) as finder, \
                patch.object(remote_probe.ROLLOUTS, 'sample', return_value=([], None, False, None)), \
                patch.object(remote_probe.ROLLOUTS, 'root_log', return_value=None), \
                patch.object(remote_probe.CODEX_BACKGROUND, 'sample', return_value={}), \
                patch.object(remote_probe, 'apply_codex_background', side_effect=lambda result, background: result), \
                patch.object(remote_probe.CODEX_COMPLETIONS, 'apply', side_effect=lambda result, *args: result), \
                patch.object(remote_probe.USAGE_LOGS, 'sample', return_value={}), \
                patch.object(remote_probe.RESULT_LOGS, 'sample', return_value={}), \
                patch.object(remote_probe.RESOURCE_SAMPLES, 'sample_all', return_value={42: {'identity': 'fixture'}}) as resources:
            rows = remote_probe.snapshot('', {'watched'})['panes']
        self.assertEqual(captured, ['%1'])
        self.assertEqual(finder.call_count, 1)
        self.assertIs(resources.call_args.args[1], table)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'idle')
        self.assertEqual(rows[0]['error_kind'], '')
        self.assertNotIn('Old assistant', rows[0]['screen'])
        self.assertNotIn('503', '\n'.join(rows[0]['preview']))
        self.assertTrue(rows[0]['dialogue_available'])
        self.assertEqual(rows[0]['resources']['scope'], 'shared_process')
        self.assertEqual(rows[0]['resources']['shared_sessions'], 2)
        self.assertEqual(rows[0]['resources']['attribution'], 'shared')
        self.assertEqual(rows[0]['resources']['attribution_source'], 'tmux_process_tree')

    def test_queued_followup_residue_does_not_request_another_answer(self):
        screen = '• Working (27m • esc to interrupt)\n• Queued follow-up inputs\n  ? 1 question\n    shift+← to answer\n» Ask Codex to do anything\nGPT-6.1-Sol'
        result = analyse_screen(screen)
        self.assertEqual(result['state'], 'running')
        self.assertEqual(result['error_kind'], '')
        self.assertNotIn('question', '\n'.join(result['preview']))

    def test_new_process_cannot_complete_work_from_old_process(self):
        tracker = CompletionTracker()
        def sample(state, pid, now):
            return tracker.update([dict(pane_id='%1', session='x', state=state, process_id=pid)], now, now)
        sample('running', 100, 1)
        sample('idle', 200, 2)
        self.assertEqual(sample('idle', 200, 4)[1], [])


class ReminderTests(unittest.TestCase):
    def test_answered_prompt_removed_even_if_another_warning_remains(self):
        feed = FeedState()
        def accept(kind, now):
            row = dict(pane_id='%1', session='x', state='running', **(issue('请处理', kind, 'warning') if kind else {}))
            feed.accept(dict(panes=[row]), ['x'], 1, now, now)
        accept('', 0)
        accept('question', .5)
        accept('question', 1.6)
        self.assertEqual([e['kind'] for e in feed.error_events], ['question'])
        accept('goal', 2)
        self.assertEqual(feed.error_events, [])
        accept('goal', 3.1)
        self.assertEqual([e['kind'] for e in feed.error_events], ['goal'])
        accept('', 4)
        self.assertEqual(feed.error_events, [])

    def test_resolved_goal_and_http_error_disappear_but_sound_delivery_is_retained(self):
        for kind in ('goal', 'fatal', 'retrying', 'interrupted', 'paused', 'stopped'):
            with self.subTest(kind=kind):
                feed = FeedState()
                def accept(active, now):
                    row = dict(pane_id='%1', session='x', state='error' if active else 'running',
                               **(issue('Goal paused (/goal resume)' if kind == 'goal' else '503 服务暂不可用', kind) if active else {}))
                    feed.accept(dict(panes=[row]), ['x'], 1, now, now)
                accept(False, 0)
                accept(True, 1)
                accept(True, 2.1)
                self.assertEqual(len(feed.error_events), 1)
                accept(False, 3)
                self.assertEqual(feed.error_events, [])
                self.assertEqual(len(feed.notification_events), 1)

    def test_old_error_is_removed_when_a_different_error_is_active(self):
        feed = FeedState()
        for now, message in ((0, ''), (1, '503'), (2, '429')):
            row = dict(pane_id='%1', session='x', state='error' if message else 'running',
                       **(issue(message) if message else {}))
            feed.accept(dict(panes=[row]), ['x'], 1, now, now)
        self.assertEqual([e['summary'] for e in feed.error_events], ['429'])

    def test_outage_reminder_clears_only_when_connection_recovers(self):
        feed = FeedState()
        feed.connection(True, 0, 0, '')
        feed.connection(False, 1, 1, '断开')
        feed.connection(False, 5, 5, '断开')
        self.assertEqual(len(feed.error_events), 1)
        feed.connection(True, 6, 6, '')
        self.assertEqual(feed.error_events, [])
        self.assertEqual(len(feed.notification_events), 1)

    def test_approved_prompt_clears_and_a_new_request_can_alert(self):
        feed = FeedState()
        for now, kind in ((0, ''), (1, 'approval'), (2.1, 'approval'), (3, ''), (4, 'approval'), (5.1, 'approval')):
            row = dict(pane_id='%1', session='x', state='approval' if kind else 'running', **(issue('请批准', kind, 'warning') if kind else {}))
            feed.accept(dict(panes=[row]), ['x'], 1, now, now)
            if now == 2.1:
                old_id = feed.error_events[0]['id']
            if now == 3:
                self.assertEqual(feed.error_events, [])
        self.assertEqual(len(feed.error_events), 1)
        self.assertNotEqual(feed.error_events[0]['id'], old_id)


class LifecycleTests(unittest.TestCase):
    @staticmethod
    def codex_event(kind):
        return json.dumps(dict(type='event_msg', payload=dict(type=kind))) + '\n'

    def test_codex_initial_attach_recovers_latest_state_without_alert_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-root.jsonl'
            for latest in ('task_started', 'task_complete', 'turn_started', 'turn_complete'):
                with self.subTest(latest=latest):
                    path.write_text(self.codex_event('error') + self.codex_event('task_started') + self.codex_event(latest))
                    watcher = RolloutWatcher()
                    self.assertEqual(watcher.read_path(str(path)), ([], latest))
                    self.assertEqual(watcher.read_path(str(path)), ([], latest))

    def test_codex_new_turn_completion_replacement_and_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-root.jsonl'
            path.write_text(self.codex_event('task_complete'))
            watcher = RolloutWatcher()
            self.assertEqual(watcher.read_path(str(path)), ([], 'task_complete'))
            with path.open('a') as log: log.write(self.codex_event('task_started'))
            self.assertEqual(watcher.read_path(str(path)), ([], 'task_started'))
            with path.open('a') as log: log.write(self.codex_event('task_complete'))
            self.assertEqual(watcher.read_path(str(path)), ([], 'task_complete'))
            replacement = Path(directory) / 'replacement'
            replacement.write_text(self.codex_event('turn_started'))
            replacement.replace(path)
            self.assertEqual(watcher.read_path(str(path)), ([], 'turn_started'))
            with path.open('a') as log: log.write(' ' * 200)
            watcher.read_path(str(path))
            path.write_text(self.codex_event('turn_complete'))
            self.assertEqual(watcher.read_path(str(path)), ([], 'turn_complete'))

    def test_codex_resume_at_eof_recovers_started_turn_without_alert_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-root.jsonl'
            path.write_text(self.codex_event('error') + self.codex_event('task_started'))
            info = path.stat()
            cursor = dict(path=str(path), device=info.st_dev, inode=info.st_ino, offset=info.st_size)
            watcher = RolloutWatcher()
            self.assertEqual(watcher.read_path(str(path), cursor), ([], 'task_started'))
            with path.open('a') as log: log.write(json.dumps(dict(type='response_item', payload=dict(type='message'))) + '\n')
            self.assertEqual(watcher.read_path(str(path)), ([], 'task_started'))

    def test_codex_gap_discards_stale_boundary_and_only_recovers_bounded_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-root.jsonl'
            watcher = RolloutWatcher()
            path.write_text(self.codex_event('task_complete'))
            self.assertEqual(watcher.read_path(str(path)), ([], 'task_complete'))
            filler = json.dumps(dict(type='response_item', payload=dict(type='message', role='assistant', content='x' * 1100000))) + '\n'
            with path.open('a') as log: log.write(self.codex_event('task_started') + filler)
            self.assertEqual(watcher.read_path(str(path)), ([], ''))
            with path.open('a') as log: log.write(filler + self.codex_event('turn_started'))
            self.assertEqual(watcher.read_path(str(path)), ([], 'turn_started'))
            with path.open('a') as log: log.write(filler + self.codex_event('turn_complete'))
            self.assertEqual(watcher.read_path(str(path)), ([], 'turn_complete'))

    def test_codex_missing_boundary_or_partial_record_does_not_recover_old_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-root.jsonl'
            path.write_text(json.dumps(dict(type='response_item', payload=dict(type='message'))) + '\n')
            self.assertEqual(RolloutWatcher().read_path(str(path)), ([], ''))
            path.write_text(self.codex_event('task_complete') + '{"type":"event_msg","payload":{"type":"task_sta')
            watcher = RolloutWatcher()
            self.assertEqual(watcher.read_path(str(path)), ([], ''))
            with path.open('a') as log: log.write('rted"}}\n')
            self.assertEqual(watcher.read_path(str(path)), ([], 'task_started'))

    def test_claude_replacement_and_truncation_keep_skipping_historical_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            historical = json.dumps(dict(type='assistant', isApiErrorMessage=True, message=dict(content='API Error: 503'))) + '\n'
            path.write_text(historical)
            watcher = ClaudeWatcher()
            self.assertEqual(watcher.read_path(str(path)), ([], ''))
            replacement = Path(directory) / 'replacement'
            replacement.write_text(historical)
            replacement.replace(path)
            self.assertEqual(watcher.read_path(str(path)), ([], ''))
            with path.open('a') as log: log.write(json.dumps(dict(type='api-request')) + '\n')
            self.assertEqual(watcher.read_path(str(path)), ([], 'turn_started'))
            path.write_text(historical)
            self.assertEqual(watcher.read_path(str(path)), ([], ''))

    def test_old_codex_completion_and_retained_terminals_stay_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-root.jsonl'
            path.write_text(self.codex_event('task_started') + self.codex_event('task_complete'))
            watcher = RolloutWatcher()
            for _ in range(12):
                self.assertEqual(watcher.read_path(str(path)), ([], 'task_complete'))
                result = analyse_screen('• Completed\n› Ask Codex to do anything\nGPT-6\n', codex_alive=True)
                apply_codex_background(result, dict(background_agents=0, background_terminals=7,
                                                   background_agents_observable=True, background_checking=False))
                self.assertEqual(result['state'], 'idle')

    def test_claude_final_text_end_turn_is_lifecycle_but_thinking_only_is_not(self):
        watcher = ClaudeWatcher()
        thinking = dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='thinking', thinking='hidden')]))
        final = dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='text', text='Synthetic final')]))
        self.assertIsNone(watcher.decode(thinking))
        self.assertEqual(watcher.decode(final), ('turn_complete', '', ''))
        self.assertIsNone(watcher.decode(dict(final, isSidechain=True)))
        self.assertIsNone(watcher.decode(dict(type='api-request', querySource='prompt_suggestion')))
        self.assertEqual(watcher.decode(dict(type='api-request', querySource='repl_main_thread')), ('turn_started', '', ''))

    def test_claude_incremental_events_skip_history_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(json.dumps(dict(type='assistant', isApiErrorMessage=True, message=dict(content='API Error: 503'))) + '\n')
            watcher = ClaudeWatcher()
            self.assertEqual(watcher.read_path(str(path)), ([], ''))
            with path.open('a') as log:
                for item in (dict(type='api-request'), dict(type='assistant', isSidechain=True, isApiErrorMessage=True, message=dict(content='429')),
                             dict(type='system', subtype='api_error', error=dict(message='503 Service Unavailable'), retryAttempt=1)):
                    log.write(json.dumps(item) + '\n')
            events, last = watcher.read_path(str(path))
            self.assertEqual(last, 'stream_error')
            self.assertEqual(len(events), 1)
            self.assertIn('503', events[0]['summary'])
            saved = watcher.files[str(path)]
            cursor = dict(path=str(path), device=saved['key'][0], inode=saved['key'][1], offset=saved['offset'])
            with path.open('a') as log:
                log.write(json.dumps(dict(type='user', message=dict(content='[Request interrupted by user]'))) + '\n')
                log.write(json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
            new = ClaudeWatcher()
            events, last = new.read_path(str(path), cursor)
            self.assertEqual([e['kind'] for e in events], ['interrupted'])
            self.assertEqual(last, 'turn_complete')

    def test_codex_decoder_retains_existing_lifecycle_support(self):
        watcher = RolloutWatcher()
        self.assertEqual(watcher.decode(dict(type='event_msg', payload=dict(type='turn_aborted', reason='interrupted'))), ('turn_aborted', '', 'interrupted'))


if __name__ == '__main__':
    unittest.main()
