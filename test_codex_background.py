import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from remote_probe import CodexBackgroundWatcher, analyse_screen, apply_codex_background, codex_terminal_count


def event(kind):
    return json.dumps(dict(type='event_msg', payload=dict(type=kind))) + '\n'


class CodexBackgroundTests(unittest.TestCase):
    def test_terminal_sessions_are_deduplicated_and_isolated_from_other_chats(self):
        processes = {1: (0, 'S', 'codex'), 2: (1, 'S', 'codex-code-mode'),
                     3: (1, 'S', 'sh'), 4: (3, 'S', 'claude'), 5: (4, 'S', 'python3'),
                     6: (1, 'S', 'sh'), 7: (1, 'Z', 'sh'), 8: (1, 'T', 'sh'),
                     20: (0, 'S', 'codex'), 21: (20, 'S', 'sh')}
        groups = {1: (1, 100), 2: (1, 100), 3: (3, 103), 4: (3, 103),
                  5: (3, 103), 6: (6, 106), 7: (7, 107), 8: (8, 108), 21: (21, 121)}
        self.assertEqual(codex_terminal_count(processes, 1, groups.__getitem__), (2, True))

    def test_pipe_shell_session_counts_once_and_code_runtime_is_excluded(self):
        processes = {1: (0, 'S', 'codex'), 2: (1, 'S', 'codex-code-mode'),
                     3: (2, 'S', 'bash'), 4: (3, 'S', 'python3')}
        groups = {1: (1, 100), 2: (1, 100), 3: (3, 0), 4: (3, 0)}
        self.assertEqual(codex_terminal_count(processes, 1, groups.__getitem__), (1, True))

    def test_process_read_failure_is_unknown_instead_of_zero(self):
        self.assertEqual(codex_terminal_count(None, 1), (0, False))
        def denied(pid): raise PermissionError('denied')
        self.assertEqual(codex_terminal_count({1: (0, 'S', 'codex')}, 1, denied), (0, False))
        self.assertEqual(codex_terminal_count({1: (0, 'S', 'codex')}, 1, lambda pid: (1, 100)), (0, True))

    def test_child_lifecycle_replay_update_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-child.jsonl'
            path.write_text(event('task_started') + event('task_complete') + event('task_started'))
            watcher = CodexBackgroundWatcher()
            self.assertIs(watcher.read_path(str(path))['foreground_running'], True)
            with path.open('a') as log: log.write(event('turn_aborted'))
            self.assertIs(watcher.read_path(str(path))['foreground_running'], False)
            replacement = Path(directory) / 'replacement.jsonl'
            replacement.write_text(event('task_started'))
            replacement.replace(path)
            self.assertIs(watcher.read_path(str(path))['foreground_running'], True)

    def test_only_owned_open_child_logs_including_nested_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for name, parent in [('root', None), ('a', 'root'), ('nested', 'a'), ('other', 'unrelated')]:
                path = Path(directory) / ('rollout-' + name + '.jsonl')
                meta = dict(id=name, source='cli' if parent is None else dict(subagent=dict(thread_spawn=dict(parent_thread_id=parent))))
                path.write_text(json.dumps(dict(type='session_meta', payload=meta)) + '\n' + event('task_started') + (event('task_complete') if name == 'a' else ''))
                paths[name] = str(path)
            watcher = CodexBackgroundWatcher()
            with patch('remote_probe.os.listdir', return_value=list(paths)), \
                 patch('remote_probe.os.readlink', side_effect=lambda fd: paths[fd.rsplit('/', 1)[1]]), \
                 patch('remote_probe.codex_terminal_count', return_value=(0, True)):
                counts = watcher.sample(1, paths['root'], {}, 0)
            self.assertEqual(counts['background_agents_total'], 2)
            self.assertEqual(counts['background_agents'], 1)
            self.assertTrue(counts['background_agents_observable'])

    def test_actual_child_work_blocks_completion_but_retained_resources_do_not(self):
        counts = dict(background_agents=1, background_terminals=0, background_checking=False,
                      background_agents_observable=True)
        row = apply_codex_background(dict(state='idle', status='等待输入'), counts)
        self.assertEqual(row['state'], 'running')
        error = apply_codex_background(dict(state='error', status='运行报错', error_kind='fatal'), counts)
        self.assertEqual(error['state'], 'error')
        counts.update(background_agents=0, background_terminals=2)
        self.assertEqual(apply_codex_background(dict(state='idle', status='等待输入'), counts)['state'], 'idle')
        counts.update(background_terminals=0, background_agents_observable=False)
        self.assertEqual(apply_codex_background(dict(state='idle'), counts)['state'], 'idle')

    def test_probe2_idle_page_with_seven_retained_terminals(self):
        screen = 'Worked for 2m 0s\n7 background terminals running · /ps to view · /stop to close\n» Ask Codex to do anything\nGPT-6.1-Sol ultra\n? for shortcuts'
        counts = dict(background_agents=0, background_agents_total=3, background_terminals=7,
                      background_checking=False, background_agents_observable=True)
        row = apply_codex_background(analyse_screen(screen), counts)
        self.assertEqual((row['state'], row['status']), ('idle', '等待输入'))
        self.assertEqual(row['background_terminals'], 7)

    def test_reconstructing_counts_does_not_override_the_input_ready_page(self):
        counts = dict(background_agents=2, background_terminals=7, background_checking=True,
                      background_agents_observable=False)
        self.assertEqual(apply_codex_background(dict(state='idle'), counts)['state'], 'idle')


if __name__ == '__main__':
    unittest.main()
