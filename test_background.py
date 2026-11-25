import json
from pathlib import Path
import tempfile
import unittest

from remote_probe import ClaudeBackgroundWatcher, ClaudeCompletionGuard, analyse_claude_screen, apply_claude_background


def launch(tool='Bash', task='bg1'):
    tool_id = 'tool-' + task
    return [dict(type='assistant', message=dict(content=[dict(type='tool_use', name=tool, id=tool_id, input=dict(run_in_background=True))])),
            dict(type='user', toolUseResult={('backgroundTaskId' if tool == 'Bash' else 'agentId'): task},
                 message=dict(content=[dict(type='tool_result', tool_use_id=tool_id, content='Background task started')]))]


def finished(task='bg1', status='completed', queue=False):
    content = '<task-notification><task-id>' + task + '</task-id><status>' + status + '</status></task-notification>'
    return dict(type='queue-operation', operation='enqueue', content=content) if queue else dict(type='user', message=dict(content=content))


class BackgroundTests(unittest.TestCase):
    def test_live_background_agent_footer_beats_completed_foreground_without_double_counting(self):
        screen = ('● Synthetic final answer\nYou will be notified when complete.\n\n'
                  '* Waiting for 1 background agent to finish\n'
                  '✔ Update installed · restart to apply\n──────────────── ultracode\n❯ \n'
                  '────────────────\n⏵⏵ bypass permissions on\n● main\n○ general-purpose Inspecting…')
        row = analyse_claude_screen(screen)
        self.assertEqual(row['background_ui_agents'], 1)
        self.assertEqual(row['state'], 'running')
        for checking, agents in ((False, 0), (True, 0), (False, 1), (False, 2)):
            with self.subTest(checking=checking, agents=agents):
                background = dict(background_count=agents, background_agents=agents, background_terminals=0,
                                  background_checking=checking, background_observable=True,
                                  foreground_running=False, foreground_event='turn_complete', foreground_observable=True)
                result = apply_claude_background(dict(row), background)
                self.assertEqual(result['state'], 'running')
                self.assertEqual(result['background_agents'], max(1, agents))
                self.assertEqual(result['background_count'], max(1, agents))
        completed = dict(background_count=0, background_agents=0, background_terminals=0,
                         background_checking=False, background_observable=True, foreground_running=False,
                         foreground_event='turn_complete', foreground_observable=True)
        self.assertEqual(apply_claude_background(analyse_claude_screen('● All done\n────────────────\n❯ '), completed)['state'], 'idle')
        failed = analyse_claude_screen('API Error: 503 unavailable\n' + screen[screen.index('* Waiting'):])
        self.assertEqual(apply_claude_background(failed, completed)['state'], 'error')

    def test_agent_footer_must_be_live_input_status_not_prose_or_history(self):
        waiting = '* Waiting for 2 background agents to finish'
        rejected = [
            '● ' + waiting + '\n────────────────\n❯ ',
            '> ' + waiting + '\n────────────────\n❯ ',
            '```\n' + waiting + '\n────────────────\n❯ ',
            waiting + '\n● Newer final answer\n────────────────\n❯ ',
            waiting + '\n❯ Earlier question\n● New final\n────────────────\n❯ ',
            waiting + '\n❯ ',
            waiting + '\n────────────────\n❯ \nBack to bottom',
            waiting + '\n' + '\n'.join('Synthetic old prose' for _ in range(7)) + '\n────────────────\n❯ '
        ]
        for screen in rejected:
            with self.subTest(screen=screen):
                self.assertEqual(analyse_claude_screen(screen)['background_ui_agents'], 0)
        current = waiting + '\n────────────────\n❯ '
        self.assertEqual(analyse_claude_screen(current)['background_ui_agents'], 2)
        self.assertEqual(analyse_claude_screen(current, in_mode=True)['background_ui_agents'], 0)

    def test_agent_result_async_flag_without_background_argument_survives_root_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Agent', id='synthetic-tool', input=dict(effort='high'))])),
                       dict(type='user', toolUseResult=dict(agentId='synthetic-agent', isAsync=True, status='async_launched'),
                            message=dict(content=[dict(type='tool_result', tool_use_id='synthetic-tool', content='Synthetic asynchronous launch')])),
                       dict(type='system', subtype='turn_duration')]
            path.write_text(''.join(json.dumps(item) + '\n' for item in records))
            watcher = ClaudeBackgroundWatcher()
            active = watcher.read_path(str(path))
            self.assertEqual(active['background_agents'], 1)
            self.assertIs(active['foreground_running'], False)
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Foreground done\n❯ '), active)['state'], 'running')
            with path.open('a') as log:
                log.write(json.dumps(finished('synthetic-agent', queue=True)) + '\n')
            done = watcher.read_path(str(path))
            self.assertEqual(done['background_agents'], 0)
            self.assertEqual(apply_claude_background(analyse_claude_screen('● All done\n❯ '), done)['state'], 'idle')

    def test_async_launch_status_is_explicit_evidence_but_sync_agent_id_is_not(self):
        for name in ('Agent', 'Task'):
            for flags, expected in ((dict(status='async_launched'), 1), (dict(isAsync=True), 1),
                                    (dict(status='completed', isAsync=False), 0), ({}, 0)):
                with self.subTest(name=name, flags=flags), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'session.jsonl'
                    records = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name=name, id='synthetic-tool', input={})])),
                               dict(type='user', toolUseResult=dict(agentId='synthetic-agent', **flags),
                                    message=dict(content=[dict(type='tool_result', tool_use_id='synthetic-tool', content='Synthetic result')]))]
                    path.write_text(''.join(json.dumps(item) + '\n' for item in records))
                    self.assertEqual(ClaudeBackgroundWatcher().read_path(str(path))['background_agents'], expected)

    def test_latest_foreground_tail_wins_before_large_background_replay_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            start = json.dumps(dict(type='api-request', querySource='repl_main_thread')) + '\n'
            filler = json.dumps(dict(type='api-request-blob', data='x' * 4096)) + '\n'
            final = dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='text', text='Synthetic final')]))
            prefix = start + filler * 2300
            path.write_text(prefix + json.dumps(final) + '\n' + json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
            watcher = ClaudeBackgroundWatcher()
            first = watcher.read_path(str(path))
            self.assertTrue(first['background_checking'])
            self.assertIs(first['foreground_running'], False)
            self.assertTrue(first['foreground_observable'])
            self.assertEqual(first['foreground_event'], 'turn_complete')
            self.assertGreaterEqual(first['foreground_event_offset'], len(prefix.encode()))
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Finished\n❯ '), first)['state'], 'unknown')
            second = watcher.read_path(str(path))
            self.assertFalse(second['background_checking'])
            self.assertIs(second['foreground_running'], False)
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Finished\n❯ '), second)['state'], 'idle')
            with path.open('a') as log:
                log.write(json.dumps(dict(type='api-request', querySource='repl_main_thread')) + '\n')
            resumed = watcher.read_path(str(path))
            self.assertIs(resumed['foreground_running'], True)
            self.assertGreater(resumed['foreground_event_offset'], second['foreground_event_offset'])
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Previous final\n❯ '), resumed)['state'], 'running')

    def test_text_end_turn_is_final_but_thinking_fragment_and_auxiliary_request_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = [dict(type='api-request', querySource='repl_main_thread'),
                       dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='thinking', thinking='hidden')])),
                       dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='text', text='Final answer')]))]
            path.write_text(''.join(json.dumps(item) + '\n' for item in records))
            watcher = ClaudeBackgroundWatcher()
            finished_state = watcher.read_path(str(path))
            self.assertIs(finished_state['foreground_running'], False)
            with path.open('a') as log:
                log.write(json.dumps(dict(type='api-request', querySource='prompt_suggestion')) + '\n')
            self.assertIs(watcher.read_path(str(path))['foreground_running'], False)
            with path.open('a') as log:
                log.write(json.dumps(dict(type='user', message=dict(content='New synthetic request'))) + '\n')
            self.assertIs(watcher.read_path(str(path))['foreground_running'], True)

    def test_verified_completion_corrects_stale_spinner_but_a_new_request_stays_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
            watcher = ClaudeBackgroundWatcher()
            stale_screen = '● Finished\n✻ Thinking… (42s · esc to interrupt)\n❯ '
            self.assertEqual(analyse_claude_screen(stale_screen)['state'], 'running')
            finished_state = watcher.read_path(str(path))
            self.assertEqual(apply_claude_background(analyse_claude_screen(stale_screen), finished_state)['state'], 'idle')
            with path.open('a') as log:
                log.write(json.dumps(dict(type='api-request', querySource='repl_main_thread')) + '\n')
            current = watcher.read_path(str(path))
            self.assertEqual(apply_claude_background(analyse_claude_screen(stale_screen), current)['state'], 'running')

    def test_partial_foreground_tail_cannot_reuse_old_complete_as_confirmed_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
            watcher = ClaudeBackgroundWatcher()
            self.assertIs(watcher.read_path(str(path))['foreground_running'], False)
            request = (json.dumps(dict(type='api-request', querySource='repl_main_thread')) + '\n').encode()
            with path.open('ab') as log:
                log.write(request[:20])
            partial = watcher.read_path(str(path))
            self.assertFalse(partial['foreground_observable'])
            self.assertIsNone(partial['foreground_running'])
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Old final\n❯ '), partial)['state'], 'unknown')
            # Even callers retaining a cached False must honor its explicit
            # unobservable marker rather than falling through to idle.
            cached = dict(partial, foreground_running=False, background_checking=False)
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Old final\n❯ '), cached)['state'], 'unknown')
            with path.open('ab') as log:
                log.write(request[20:])
            self.assertIs(watcher.read_path(str(path))['foreground_running'], True)

    def test_real_active_agent_survives_foreground_completion_but_workflow_text_is_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = launch('Agent', 'active-child') + [dict(type='system', subtype='turn_duration')]
            path.write_text(''.join(json.dumps(item) + '\n' for item in records))
            watcher = ClaudeBackgroundWatcher()
            active = watcher.read_path(str(path))
            self.assertEqual(active['background_agents'], 1)
            self.assertEqual(apply_claude_background(analyse_claude_screen('● Foreground done\n❯ '), active)['state'], 'running')
            with path.open('a') as log:
                log.write(json.dumps(finished('active-child')) + '\n')
            done = watcher.read_path(str(path))
            self.assertEqual(apply_claude_background(analyse_claude_screen('● All done\n❯ '), done)['state'], 'idle')
            workflow = '● Old workflow message\n* Waiting for 1 dynamic workflow to finish\n❯ '
            self.assertEqual(apply_claude_background(analyse_claude_screen(workflow), done)['state'], 'unknown')

    def test_visible_dynamic_workflow_overrides_idle_prompt_and_finished_foreground(self):
        screen = '● 工作流完成后我会继续\n* Waiting for 1 dynamic workflow to finish\n─────\n❯ \n─────\n⏵⏵ bypass permissions on · ← 6 agents\n○ environment-analysis ▰▰ 0/4 · 1m53s'
        row = analyse_claude_screen(screen)
        info = dict(background_count=0, background_terminals=0, background_agents=0, background_checking=False,
                    background_observable=True, foreground_running=False)
        result = apply_claude_background(row, info)
        self.assertEqual(result['state'], 'running')
        self.assertEqual(result['background_count'], 1)
        self.assertIn('工作流', result['status'])

    def test_main_request_remains_running_despite_a_visible_input_box(self):
        result = apply_claude_background(analyse_claude_screen('● 之前的答复\n❯ '),
                                        dict(background_count=0, background_checking=False, foreground_running=True))
        self.assertEqual(result['state'], 'running')

    def test_thinking_end_turn_is_not_the_foreground_completion_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = [dict(type='api-request', querySource='repl_main_thread'),
                       dict(type='assistant', message=dict(stop_reason='end_turn', content=[dict(type='thinking', thinking='hidden')]))]
            path.write_text(''.join(json.dumps(x) + '\n' for x in records))
            watcher = ClaudeBackgroundWatcher()
            self.assertIs(watcher.read_path(str(path))['foreground_running'], True)
            with path.open('a') as log: log.write(json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
            self.assertIs(watcher.read_path(str(path))['foreground_running'], False)

    def test_transient_idle_cannot_flicker_or_complete_a_workflow(self):
        guard = ClaudeCompletionGuard()
        def sample(state, now):
            return guard.apply(dict(state=state, status=state), '%1', 1, now)['state']
        self.assertEqual(sample('running', 0), 'running')
        self.assertEqual(sample('idle', .5), 'running')
        self.assertEqual(sample('running', 1), 'running')
        self.assertEqual(sample('idle', 1.5), 'running')
        self.assertEqual(sample('idle', 3), 'running')
        self.assertEqual(sample('idle', 3.5), 'idle')

    def test_finished_foreground_waits_for_input_despite_retained_bash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(''.join(json.dumps(x) + '\n' for x in launch() + [dict(type='system', subtype='turn_duration')]))
            watcher = ClaudeBackgroundWatcher()
            info = watcher.read_path(str(path))
            row = apply_claude_background(analyse_claude_screen('● 主对话完成\n❯ \n ⏵⏵ bypass permissions on'), info)
            self.assertEqual(row['state'], 'idle')
            self.assertEqual(row['background_terminals'], 1)
            self.assertEqual(row['status'], '等待输入')
            with path.open('a') as log: log.write(json.dumps(finished(queue=True)) + '\n')
            info = watcher.read_path(str(path))
            row = apply_claude_background(analyse_claude_screen('● 已完成\n❯ '), info)
            self.assertEqual(row['state'], 'idle')
            self.assertEqual(info['background_count'], 0)

    def test_done_page_with_shell_still_running_is_input_ready(self):
        screen = '● 已完成\n✻ Cogitated for 1m 29s · done 3:44 AM · 1 shell still running\n─────\n❯ \n─────\n⏵⏵ bypass permissions on · 1 shell · ← 6 agents'
        background = dict(background_count=1, background_agents=0, background_terminals=1,
                          background_checking=False, background_observable=True, foreground_running=False)
        result = apply_claude_background(analyse_claude_screen(screen), background)
        self.assertEqual((result['state'], result['status']), ('idle', '等待输入'))
        self.assertEqual(result['background_terminals'], 1)

    def test_pending_foreground_with_retained_terminal_remains_running(self):
        background = dict(background_count=1, background_agents=0, background_terminals=1,
                          background_checking=False, background_observable=True, foreground_running=True)
        result = apply_claude_background(analyse_claude_screen('● 上一条回复\n❯ '), background)
        self.assertEqual(result['state'], 'running')

    def test_verified_in_progress_child_is_not_just_an_online_agent_count(self):
        background = dict(background_count=1, background_agents=1, background_terminals=0,
                          background_checking=False, background_observable=True, foreground_running=False)
        self.assertEqual(apply_claude_background(analyse_claude_screen('● 后台工作尚未结束\n❯ '), background)['state'], 'running')

    def test_input_confirmation_requires_unchanged_prose(self):
        guard = ClaudeCompletionGuard()
        guard.apply(dict(state='running', activity='a'), '%1', 1, 0)
        self.assertEqual(guard.apply(dict(state='idle', activity='a'), '%1', 1, .5)['state'], 'running')
        self.assertEqual(guard.apply(dict(state='idle', activity='b'), '%1', 1, 2)['state'], 'running')
        self.assertEqual(guard.apply(dict(state='idle', activity='b'), '%1', 1, 3.5)['state'], 'running')
        self.assertEqual(guard.apply(dict(state='idle', activity='b'), '%1', 1, 4)['state'], 'idle')

    def test_silent_spinner_never_completes_due_to_inactivity(self):
        guard = ClaudeCompletionGuard()
        for now in (0, 1, 30, 600):
            self.assertEqual(guard.apply(dict(state='running', activity='unchanged'), '%1', 1, now)['state'], 'running')

    def test_existing_completed_task_and_duplicate_tool_record_do_not_reappear(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = launch() + [finished()] + launch()
            path.write_text(''.join(json.dumps(x) + '\n' for x in records))
            self.assertEqual(ClaudeBackgroundWatcher().read_path(str(path))['background_count'], 0)

    def test_subagent_counts_and_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = launch('Agent', 'a1') + launch('Bash', 'b1') + [dict(type='progress', data=dict(type='agent_progress'))]
            path.write_text(''.join(json.dumps(x) + '\n' for x in records))
            info = ClaudeBackgroundWatcher().read_path(str(path))
            self.assertEqual((info['background_agents'], info['background_terminals']), (1, 1))
            self.assertEqual(info['background_revision'], 1)

    def test_foreground_agent_counts_while_tool_is_pending_then_clears(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            call = dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Agent', id='a1', input={})]))
            path.write_text(json.dumps(call) + '\n')
            watcher = ClaudeBackgroundWatcher()
            self.assertEqual(watcher.read_path(str(path))['background_agents'], 1)
            result = dict(type='user', toolUseResult=dict(agentId='finished-agent'),
                          message=dict(content=[dict(type='tool_result', tool_use_id='a1', content='done')]))
            with path.open('a') as log: log.write(json.dumps(result) + '\n')
            self.assertEqual(watcher.read_path(str(path))['background_agents'], 0)

    def test_async_agent_does_not_count_twice_when_result_arrives(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            records = launch('Agent', 'a1')
            path.write_text(json.dumps(records[0]) + '\n')
            watcher = ClaudeBackgroundWatcher()
            self.assertEqual(watcher.read_path(str(path))['background_agents'], 1)
            with path.open('a') as log: log.write(json.dumps(records[1]) + '\n')
            self.assertEqual(watcher.read_path(str(path))['background_agents'], 1)
            with path.open('a') as log: log.write(json.dumps(finished('a1')) + '\n')
            self.assertEqual(watcher.read_path(str(path))['background_agents'], 0)

    def test_task_stop_and_task_output_completed_clear_active_task(self):
        for tool in ('TaskStop', 'TaskOutput'):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'session.jsonl'
                records = launch() + [dict(type='assistant', message=dict(content=[dict(type='tool_use', name=tool, id='stop', input=dict(task_id='bg1'))])),
                                      dict(type='user', toolUseResult=dict(task=dict(status='completed')),
                                           message=dict(content=[dict(type='tool_result', tool_use_id='stop', content='completed')]))]
                path.write_text(''.join(json.dumps(x) + '\n' for x in records))
                self.assertEqual(ClaudeBackgroundWatcher().read_path(str(path))['background_count'], 0)

    def test_old_prose_and_always_present_agent_footer_are_not_background_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'session.jsonl'
            path.write_text(json.dumps(dict(type='assistant', message=dict(content='M4 正在执行，8/48 episodes，6 agents'))) + '\n')
            info = ClaudeBackgroundWatcher().read_path(str(path))
            row = apply_claude_background(analyse_claude_screen('● M4 正在执行\n❯ \n bypass permissions on · ← 6 agents'), info)
            self.assertEqual(row['state'], 'idle')

    def test_reconstructing_background_state_never_emits_completion(self):
        result = apply_claude_background(dict(state='idle', error_kind=''),
                                        dict(background_count=0, background_checking=True))
        self.assertEqual(result['state'], 'unknown')

class BackgroundExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'session.jsonl'
        self.output = self.folder / 'bg1.output'
        self.output.write_text('existing experiment history\n')
        records = launch()
        records[1]['toolUseResult']['outputFile'] = str(self.output)
        records += [dict(type='system', subtype='turn_duration')]
        self.path.write_text(''.join(json.dumps(item) + '\n' for item in records))
        self.now = 0.0
        self.watcher = ClaudeBackgroundWatcher(clock=lambda: self.now)

    def sample(self, at):
        self.now = at
        return self.watcher.read_path(str(self.path))

    def row(self, background):
        return apply_claude_background(analyse_claude_screen('● Foreground complete\n❯ '), background)

    def resources(self, sample_id, cpu=0, disk=0, gpu=None, gpu_id=None, match=True, **extra):
        info = self.output.stat()
        files = [dict(device=info.st_dev, inode=info.st_ino if match else info.st_ino + 1)]
        rows = [dict(pid=10, parent=1, start='root', cpu_percent=999, output_files=[]),
                dict(pid=11, parent=10, start='shell', cpu_percent=0, output_files=files),
                dict(pid=12, parent=11, start='experiment', cpu_percent=cpu,
                     disk_write_per_second=disk, gpu_available=gpu is not None,
                     gpu_utilization_percent=gpu, gpu_sample_age_seconds=0,
                     gpu_sample_id=gpu_id, output_files=[]),
                dict(pid=13, parent=10, start='unrelated', cpu_percent=999, gpu_available=True,
                     gpu_utilization_percent=100, gpu_sample_age_seconds=0, gpu_sample_id=sample_id, output_files=[])]
        return dict(identity='synthetic-root', sample_id=sample_id, sample_age_seconds=0,
                    process_activity=rows, **extra)

    def process_sample(self, at, sample_id, **metrics):
        background = self.sample(at)
        return self.watcher.with_process_activity(str(self.path), background, self.resources(sample_id, **metrics),
                                                 now=at, root_pid=10)

    def test_structured_output_growth_keeps_completed_foreground_background_running(self):
        baseline = self.sample(0)
        self.assertEqual((baseline['background_active_terminals'], baseline['background_output_observed_bytes']), (0, 0))
        self.assertEqual(self.row(baseline)['state'], 'idle')
        with self.output.open('a') as file:
            file.write('episode complete\n')
        progress = self.sample(1)
        row = self.row(progress)
        self.assertEqual((row['state'], row['background_activity_source']), ('running', 'output'))
        self.assertTrue(row['foreground_input_ready'])
        self.assertIn('后台实验', row['status'])
        self.assertEqual(row['background_active_terminals'], 1)
        self.assertEqual(progress['background_output_observed_bytes'], len('episode complete\n'))
        self.assertEqual(self.sample(5)['background_active_terminals'], 1)
        self.assertEqual(self.row(self.sample(9.1))['state'], 'idle')

    def test_file_replacement_reset_missing_and_gap_do_not_replay_old_bytes(self):
        self.sample(0)
        with self.output.open('a') as file: file.write('new\n')
        self.assertEqual(self.sample(1)['background_active_terminals'], 1)
        self.output.rename(self.folder / 'previous.output')
        self.output.write_text('old replacement history\n' * 100)
        replaced = self.sample(2)
        self.assertEqual(replaced['background_active_terminals'], 0)
        self.assertEqual(replaced['background_output_observed_bytes'], 4)
        self.output.unlink()
        self.assertEqual(self.sample(3)['background_active_terminals'], 0)
        self.output.write_text('reappeared history\n' * 100)
        self.assertEqual(self.sample(4)['background_active_terminals'], 0)
        with self.output.open('a') as file: file.write('after gap\n')
        self.assertEqual(self.sample(20)['background_active_terminals'], 0)
        with self.output.open('a') as file: file.write('current\n')
        self.assertEqual(self.sample(21)['background_active_terminals'], 1)

    def test_task_completion_clears_recent_output_activity_immediately(self):
        self.sample(0)
        with self.output.open('a') as file: file.write('new output\n')
        self.assertEqual(self.sample(1)['background_active_terminals'], 1)
        with self.path.open('a') as log: log.write(json.dumps(finished(queue=True)) + '\n')
        complete = self.sample(2)
        self.assertEqual((complete['background_terminals'], complete['background_active_terminals']), (0, 0))
        self.assertEqual(self.row(complete)['state'], 'idle')

    def test_background_history_replay_cannot_turn_output_backlog_into_progress(self):
        records = launch()
        records[1]['toolUseResult']['outputFile'] = str(self.output)
        prefix = ''.join(json.dumps(item) + '\n' for item in records)
        filler = json.dumps(dict(type='opaque-history', data='x' * 4096)) + '\n'
        self.path.write_text(prefix + filler * 2100 + json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
        first = self.sample(0)
        self.assertTrue(first['background_checking'])
        with self.output.open('a') as file: file.write('work during replay\n')
        complete = self.sample(1)
        self.assertFalse(complete['background_checking'])
        self.assertEqual(complete['background_active_terminals'], 0)
        self.assertEqual(complete['background_output_observed_bytes'], 0)
        with self.output.open('a') as file: file.write('confirmed current work\n')
        self.assertEqual(self.sample(2)['background_active_terminals'], 1)

    def test_error_approval_exit_and_history_remain_higher_priority(self):
        background = dict(self.sample(0), background_active_terminals=1)
        for state in ('error', 'approval', 'input', 'stopped', 'paused', 'history', 'interrupted'):
            with self.subTest(state=state):
                row = apply_claude_background(dict(state=state, status=state, error_kind=''), background)
                self.assertEqual(row['state'], state)
                self.assertFalse(row['foreground_input_ready'])
        row = apply_claude_background(dict(state='running', status='retrying', error_kind='retrying'), background)
        self.assertEqual(row['status'], 'retrying')
        self.assertFalse(row['foreground_input_ready'])

    def test_bound_descendant_cpu_requires_continuous_real_samples(self):
        for at, expected in ((0, 0), (1, 0), (2, 1)):
            info = self.process_sample(at, at + 1, cpu=100)
            self.assertEqual(info['background_active_terminals'], expected)
        self.assertEqual(info['background_activity_sources'], ['cpu'])
        self.assertEqual(info['background_bound_terminals'], 1)
        self.assertEqual(self.row(info)['state'], 'running')
        self.assertTrue(self.row(info)['foreground_input_ready'])

    def test_unbound_cpu_gpu_and_root_cpu_never_promote_retained_terminal(self):
        for at in range(4):
            info = self.process_sample(at, at + 1, cpu=.5)
            self.assertEqual(info['background_active_terminals'], 0)
        self.assertEqual(self.row(info)['state'], 'idle')
        for at in range(4, 8):
            info = self.process_sample(at, at + 1, cpu=100, gpu=100, gpu_id=at, match=False)
            self.assertEqual(info['background_active_terminals'], 0)
            self.assertEqual(info['background_bound_terminals'], 0)

    def test_repeated_resource_cache_and_stale_resource_do_not_extend_activity(self):
        for at in range(3): info = self.process_sample(at, at + 1, cpu=100)
        self.assertEqual(info['background_active_terminals'], 1)
        for at in (5, 9):
            info = self.process_sample(at, 3, cpu=100)
            self.assertEqual(info['background_active_terminals'], 1)
        self.assertEqual(self.process_sample(10.1, 3, cpu=100)['background_active_terminals'], 0)
        background = self.sample(11)
        resource = self.resources(4, cpu=100)
        resource['sample_age_seconds'] = 4.1
        self.assertEqual(self.watcher.with_process_activity(str(self.path), background, resource, now=11)['background_active_terminals'], 0)

    def test_new_process_identity_and_new_collector_establish_baselines(self):
        for at in range(3): info = self.process_sample(at, at + 1, cpu=100)
        for change in ('pid_start', 'epoch'):
            resource = self.resources(4, cpu=100)
            if change == 'pid_start': resource['process_activity'][1]['start'] = 'reused-shell'
            else: resource['identity'] = 'new-collector-root'
            current = self.watcher.with_process_activity(str(self.path), self.sample(3), resource, now=3, root_pid=10)
            self.assertEqual(current['background_active_terminals'], 0)

    def test_disk_uses_real_disk_counters_and_requires_continuous_samples(self):
        for at, expected in ((0, 0), (1, 0), (2, 1)):
            info = self.process_sample(at, at + 1, disk=65536)
            self.assertEqual(info['background_active_terminals'], expected)
        self.assertEqual(info['background_activity_sources'], ['disk'])

    def test_gpu_cache_is_not_a_new_observation_and_memory_is_not_activity(self):
        self.assertEqual(self.process_sample(0, 1, gpu=56, gpu_id=7)['background_active_terminals'], 0)
        self.assertEqual(self.process_sample(1, 2, gpu=56, gpu_id=7)['background_active_terminals'], 0)
        info = self.process_sample(2, 3, gpu=56, gpu_id=8)
        self.assertEqual(info['background_activity_sources'], ['gpu'])
        for at in (4, 8):
            self.assertEqual(self.process_sample(at, at + 1, gpu=56, gpu_id=8)['background_active_terminals'], 1)
        self.assertEqual(self.process_sample(10.1, 12, gpu=56, gpu_id=8)['background_active_terminals'], 0)

    def test_completed_task_cannot_be_reactivated_by_old_process_metrics(self):
        for at in range(3): self.process_sample(at, at + 1, cpu=100)
        with self.path.open('a') as log: log.write(json.dumps(finished(queue=True)) + '\n')
        info = self.process_sample(3, 4, cpu=100)
        self.assertEqual(info['background_active_terminals'], 0)
        self.assertEqual(self.row(info)['state'], 'idle')

    def test_structured_persistent_bash_task_id_does_not_become_an_agent(self):
        records = launch()
        records[1]['toolUseResult'] = dict(persistent=True, taskId='bg1', timeoutMs=1000, output_file=str(self.output))
        self.path.write_text(''.join(json.dumps(item) + '\n' for item in records + [dict(type='system', subtype='turn_duration')]))
        initial = self.sample(0)
        self.assertEqual((initial['background_terminals'], initial['background_agents']), (1, 0))
        self.assertEqual(self.row(initial)['state'], 'idle')
        with self.output.open('a') as file: file.write('current episode\n')
        self.assertEqual(self.row(self.sample(1))['state'], 'running')

    def test_explicit_workflow_lifecycle_and_transcript_progress_are_not_agents(self):
        transcripts = self.folder / 'workflow-transcripts'
        transcripts.mkdir()
        child = transcripts / 'child.jsonl'
        child.write_text('existing history\n')
        records = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Workflow', id='workflow-tool', input={})])),
                   dict(type='user', toolUseResult=dict(taskId='wf1', runId='run1', taskType='local_workflow', status='async_launched', transcriptDir=str(transcripts)),
                        message=dict(content=[dict(type='tool_result', tool_use_id='workflow-tool', content='launched')])),
                   dict(type='system', subtype='turn_duration')]
        self.path.write_text(''.join(json.dumps(item) + '\n' for item in records))
        initial = self.sample(0)
        self.assertEqual((initial['background_agents'], initial['background_terminals'], initial['background_log_workflows']), (0, 0, 1))
        row = self.row(initial)
        self.assertEqual((row['state'], row['background_count'], row['background_workflows']), ('running', 1, 1))
        with child.open('a') as file: file.write('new workflow progress\n')
        progress = self.sample(1)
        self.assertEqual(progress['background_output_observed_bytes'], len('new workflow progress\n'))
        (transcripts / 'new-child.jsonl').write_text('preexisting child history\n' * 1000)
        (transcripts / 'configuration.json').write_text('configuration growth is not work\n')
        self.assertEqual(self.sample(1.5)['background_output_observed_bytes'], progress['background_output_observed_bytes'])
        ui = analyse_claude_screen('● Workflow launched\n* Waiting for 1 dynamic workflow to finish\n❯ ')
        self.assertEqual(apply_claude_background(ui, progress)['background_count'], 1)
        with self.path.open('a') as log: log.write(json.dumps(finished('wf1', queue=True)) + '\n')
        self.assertEqual(self.row(self.sample(2))['state'], 'idle')

    def test_unknown_async_task_is_not_classified_as_agent(self):
        records = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Other', id='tool1', input={})])),
                   dict(type='user', toolUseResult=dict(taskId='unknown1', status='async_launched'),
                        message=dict(content=[dict(type='tool_result', tool_use_id='tool1', content='launched')])),
                   dict(type='system', subtype='turn_duration')]
        self.path.write_text(''.join(json.dumps(item) + '\n' for item in records))
        info = self.sample(0)
        self.assertEqual((info['background_agents'], info['background_terminals'], info['background_log_workflows']), (0, 0, 0))
        self.assertEqual(self.row(info)['state'], 'idle')

    def test_workflow_task_output_and_stop_clear_explicit_lifecycle(self):
        for tool in ('TaskOutput', 'TaskStop'):
            with self.subTest(tool=tool):
                records = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Workflow', id='workflow-tool', input={})])),
                           dict(type='user', toolUseResult=dict(taskId='wf1', taskType='local_workflow', status='async_launched'),
                                message=dict(content=[dict(type='tool_result', tool_use_id='workflow-tool', content='launched')])),
                           dict(type='assistant', message=dict(content=[dict(type='tool_use', name=tool, id='finish-tool', input=dict(taskId='wf1'))])),
                           dict(type='user', toolUseResult=dict(task=dict(taskId='wf1', status='completed')),
                                message=dict(content=[dict(type='tool_result', tool_use_id='finish-tool', content='completed')])),
                           dict(type='system', subtype='turn_duration')]
                self.path.write_text(''.join(json.dumps(item) + '\n' for item in records))
                watcher = ClaudeBackgroundWatcher()
                info = watcher.read_path(str(self.path))
                self.assertEqual(info['background_log_workflows'], 0)
                self.assertEqual(self.row(info)['state'], 'idle')

    def test_snapshot_applies_bound_resource_evidence_before_final_state_and_guard(self):
        from contextlib import ExitStack
        from types import SimpleNamespace
        from unittest.mock import patch
        import remote_probe
        from process_snapshot import ProcessSnapshot
        session_id = '12345678-1234-1234-1234-123456789abc'
        renamed = self.folder / (session_id + '.jsonl')
        self.path.rename(renamed)
        self.path = renamed
        processes = ProcessSnapshot({9: (1, 'S', 'bash'), 10: (9, 'S', 'claude'),
                                     11: (10, 'S', 'bash'), 12: (11, 'R', 'python')})
        def tmux(command, **kwargs):
            if 'list-panes' in command:
                return SimpleNamespace(returncode=0, stdout='synthetic\t0\t0\t%synthetic\tclaude\t/tmp\t0\t9\t\t0\n')
            if 'capture-pane' in command:
                return SimpleNamespace(returncode=0, stdout='● Foreground complete\n❯ ')
            raise AssertionError('Unexpected command')
        with ExitStack() as stack:
            stack.enter_context(patch.object(remote_probe, 'read_processes', return_value=processes))
            stack.enter_context(patch.object(remote_probe.subprocess, 'run', side_effect=tmux))
            stack.enter_context(patch.object(remote_probe, 'CLAUDE_BACKGROUND', self.watcher))
            stack.enter_context(patch.object(remote_probe, 'CLAUDE_COMPLETIONS', ClaudeCompletionGuard()))
            stack.enter_context(patch.object(remote_probe, 'KNOWN_AGENTS', {}))
            stack.enter_context(patch.object(remote_probe.CLAUDE_LOGS, 'sample', return_value=([], 'turn_complete', True, {})))
            stack.enter_context(patch.object(remote_probe.CLAUDE_LOGS, 'root_log', return_value=str(self.path)))
            stack.enter_context(patch.object(remote_probe.USAGE_LOGS, 'sample', return_value={}))
            stack.enter_context(patch.object(remote_probe.RESULT_LOGS, 'sample', return_value={}))
            resources = stack.enter_context(patch.object(remote_probe.RESOURCE_SAMPLES, 'sample_all'))
            clock = stack.enter_context(patch.object(remote_probe.time, 'monotonic'))
            for at in range(3):
                self.now = at
                clock.return_value = at
                resources.return_value = {10: self.resources(at + 1, cpu=100)}
                row = remote_probe.snapshot('/synthetic-socket', ['synthetic'])['panes'][0]
                self.assertEqual(resources.call_args.kwargs, dict(session_contexts={10: session_id}))
                if at < 2:
                    self.assertEqual(row['state'], 'unknown')
                    self.assertIn('核对后台实验', row['status'])
            self.assertEqual(row['state'], 'running')
            self.assertIn('后台实验', row['status'])
            self.assertTrue(row['foreground_input_ready'])
            self.assertEqual(row['background_activity_sources'], ['cpu'])

    def session_process_sample(self, at, sample_id, cpu=0, gpu=None, gpu_id=None, **extra):
        self.path.write_text(json.dumps(dict(type='system', subtype='turn_duration')) + '\n')
        background = self.sample(at)
        resource = self.resources(sample_id)
        row = dict(pid=50, parent=1, start='detached-experiment', association='claude_session_env', cpu_percent=cpu,
                   gpu_available=gpu is not None, gpu_utilization_percent=gpu,
                   gpu_sample_id=gpu_id, gpu_sample_age_seconds=0, output_files=[])
        row.update(extra)
        resource['process_activity'].append(row)
        return self.watcher.with_process_activity(str(self.path), background, resource, now=at, root_pid=10)

    def test_explicit_session_gpu_process_survives_foreground_and_completed_tool(self):
        first = self.session_process_sample(0, 1, gpu=56, gpu_id=7)
        self.assertEqual(first['background_active_session_processes'], 0)
        self.assertTrue(first['background_process_checking'])
        second = self.session_process_sample(1, 2, gpu=56, gpu_id=7)
        self.assertEqual(second['background_active_session_processes'], 0)
        active = self.session_process_sample(2, 3, gpu=56, gpu_id=8)
        self.assertEqual((active['background_active_terminals'], active['background_active_session_processes']), (0, 1))
        self.assertEqual(active['background_activity_sources'], ['gpu'])
        row = self.row(active)
        self.assertEqual((row['state'], row['status']), ('running', '后台实验运行中'))
        self.assertEqual(row['background_count'], 0)  # A GPU PID is not a task.
        self.assertTrue(row['foreground_input_ready'])

    def test_explicit_session_cpu_needs_continuous_samples_and_presence_is_not_work(self):
        for at in range(3):
            idle = self.session_process_sample(at, at + 1, cpu=0, gpu=0, gpu_id=at, gpu_memory_bytes=9999999999)
            self.assertEqual(self.row(idle)['state'], 'idle')
        for at, expected in ((3, 0), (4, 1)):
            active = self.session_process_sample(at, at + 1, cpu=100, gpu=0, gpu_id=at)
            self.assertEqual(active['background_active_session_processes'], expected)
        self.assertEqual(active['background_activity_sources'], ['cpu'])

    def test_session_gpu_cache_memory_and_unconfirmed_association_are_not_progress(self):
        self.session_process_sample(0, 1, gpu=56, gpu_id=7)
        for at in range(1, 4):
            cached = self.session_process_sample(at, at + 1, gpu=56, gpu_id=7)
            self.assertEqual(cached['background_active_session_processes'], 0)
        for at in range(4, 7):
            unconfirmed = self.session_process_sample(at, at + 1, cpu=100, gpu=100, gpu_id=at, association='process_tree')
            self.assertEqual(unconfirmed['background_active_session_processes'], 0)
            self.assertEqual(self.row(unconfirmed)['state'], 'idle')

    def test_session_zero_activity_expires_and_missing_or_reused_pid_clears_proof(self):
        self.session_process_sample(0, 1, gpu=56, gpu_id=1)
        active = self.session_process_sample(1, 2, gpu=56, gpu_id=2)
        self.assertEqual(active['background_active_session_processes'], 1)
        for at in (3, 5, 7, 9.1):
            quiet = self.session_process_sample(at, int(at) + 3, gpu=0, gpu_id=at)
        self.assertEqual(self.row(quiet)['state'], 'idle')
        resumed = self.session_process_sample(10, 14, gpu=56, gpu_id=10)
        self.assertEqual(resumed['background_active_session_processes'], 1)
        reused = self.session_process_sample(11, 15, gpu=56, gpu_id=11, start='reused-pid')
        self.assertEqual(reused['background_active_session_processes'], 0)
        background = self.sample(12)
        missing = self.watcher.with_process_activity(str(self.path), background, self.resources(16), now=12, root_pid=10)
        self.assertEqual(missing['background_active_session_processes'], 0)

    def test_missing_and_stale_resources_do_not_complete_previously_active_background(self):
        for at in range(3): active = self.process_sample(at, at + 1, cpu=100)
        self.assertEqual(self.row(active)['state'], 'running')
        for at in (3, 4):
            missing = self.watcher.with_process_activity(str(self.path), self.sample(at), None, now=at, root_pid=10)
            self.assertEqual(self.row(missing)['state'], 'unknown')
            self.assertTrue(missing['background_process_unobserved'])
            self.assertTrue(self.row(missing)['foreground_input_ready'])
            self.assertEqual(missing['background_active_terminals'], 0)
        stale_resource = self.resources(4, cpu=100)
        stale_resource['sample_age_seconds'] = 4.1
        stale = self.watcher.with_process_activity(str(self.path), self.sample(5), stale_resource, now=5, root_pid=10)
        self.assertEqual(self.row(stale)['state'], 'unknown')
        for at in (6, 8, 10.1): quiet = self.process_sample(at, int(at) + 5, cpu=0)
        self.assertEqual(self.row(quiet)['state'], 'idle')
        self.assertFalse(quiet['background_process_unobserved'])

    def test_gpu_observation_gap_cannot_be_finished_by_unrelated_cpu_zero(self):
        self.session_process_sample(0, 1, gpu=56, gpu_id=1)
        self.assertEqual(self.row(self.session_process_sample(1, 2, gpu=56, gpu_id=2))['state'], 'running')
        for at in (3, 5, 7, 9.1):
            gap = self.session_process_sample(at, int(at) + 3, cpu=0, gpu=None)
        self.assertEqual(gap['background_active_session_processes'], 0)
        self.assertEqual(self.row(gap)['state'], 'unknown')
        self.assertTrue(gap['background_process_unobserved'])
        restored = self.session_process_sample(10, 14, cpu=0, gpu=0, gpu_id=10)
        self.assertEqual(self.row(restored)['state'], 'idle')
        self.assertFalse(restored['background_process_unobserved'])

    def test_fresh_snapshot_confirms_missing_active_pid_without_reusing_old_evidence(self):
        self.session_process_sample(0, 1, gpu=56, gpu_id=1)
        self.session_process_sample(1, 2, gpu=56, gpu_id=2)
        missing = self.watcher.with_process_activity(str(self.path), self.sample(2), None, now=2, root_pid=10)
        self.assertEqual(self.row(missing)['state'], 'unknown')
        exited = self.watcher.with_process_activity(str(self.path), self.sample(3), self.resources(4),
                                                   now=3, root_pid=10, processes={10: (1, 'S', 'claude')})
        self.assertEqual(self.row(exited)['state'], 'idle')
        self.assertEqual(exited['background_active_session_processes'], 0)

    def test_missing_initial_resource_and_plain_retained_shell_are_not_unknown(self):
        initial = self.watcher.with_process_activity(str(self.path), self.sample(0), None, now=0, root_pid=10)
        self.assertEqual(self.row(initial)['state'], 'idle')
        for at in range(1, 4):
            idle = self.process_sample(at, at + 1, cpu=0)
        gap = self.watcher.with_process_activity(str(self.path), self.sample(4), None, now=4, root_pid=10)
        self.assertEqual(self.row(gap)['state'], 'idle')
        self.assertFalse(gap['background_process_unobserved'])

    def test_new_output_and_explicit_task_stop_take_priority_over_resource_gap(self):
        for at in range(3): self.process_sample(at, at + 1, cpu=100)
        unknown = self.watcher.with_process_activity(str(self.path), self.sample(3), None, now=3, root_pid=10)
        self.assertEqual(self.row(unknown)['state'], 'unknown')
        with self.output.open('a') as output: output.write('new confirmed experiment progress\n')
        progress = self.watcher.with_process_activity(str(self.path), self.sample(4), None, now=4, root_pid=10)
        self.assertEqual(self.row(progress)['state'], 'running')
        self.assertIn('output', progress['background_activity_sources'])
        with self.path.open('a') as log: log.write(json.dumps(finished(status='stopped', queue=True)) + '\n')
        stopped = self.watcher.with_process_activity(str(self.path), self.sample(5), None, now=5, root_pid=10)
        self.assertEqual(self.row(stopped)['state'], 'idle')
        self.assertFalse(stopped['background_process_unobserved'])

    def test_epoch_and_child_pid_reuse_require_confirmation_without_old_activity(self):
        for at in range(3): self.process_sample(at, at + 1, cpu=100)
        resource = self.resources(4, cpu=0)
        resource['identity'] = 'new-collector'
        resource['process_activity'][2]['cpu_percent'] = None
        changed = self.watcher.with_process_activity(str(self.path), self.sample(3), resource, now=3, root_pid=10)
        self.assertEqual(changed['background_active_terminals'], 0)
        self.assertEqual(self.row(changed)['state'], 'unknown')
        # A new instance of a child beneath the same retained shell cannot use
        # the former child's CPU signal, even when the shell binding is stable.
        self.watcher = ClaudeBackgroundWatcher(clock=lambda: self.now)
        for at in range(4, 7): self.process_sample(at, at + 1, cpu=100)
        reused = self.resources(8, cpu=0)
        reused['process_activity'][2].update(start='reused-child', cpu_percent=None)
        current = self.watcher.with_process_activity(str(self.path), self.sample(7), reused, now=7, root_pid=10)
        self.assertEqual(current['background_active_terminals'], 0)
        self.assertEqual(self.row(current)['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
