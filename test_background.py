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


if __name__ == '__main__':
    unittest.main()
