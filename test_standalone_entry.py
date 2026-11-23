"""Exercise the shipped shell entry points without a build or SSH connection."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent


class StandaloneEntryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='orbit entry ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.engine = self.root / 'Moved Agent Orbit.app/Contents/Resources/engine'
        self.engine.mkdir(parents=True)
        # Run the real packaging copy loop, without invoking Swift or downloading Python.
        script = (ROOT / 'build_desktop.sh').read_text()
        loop = re.search(r'(?ms)^for source_file in [^\n]+; do\n.*?^done$', script)
        self.assertIsNotNone(loop, 'build script must declare its engine files')
        subprocess.run(['/bin/sh', '-c', loop.group(0)], check=True,
                       env=dict(os.environ, project_dir=str(ROOT), engine_dir=str(self.engine)),
                       capture_output=True, text=True, timeout=5)
        self.bin = self.root / 'isolated bin'
        self.bin.mkdir()
        # The shell launchers need dirname; no system Python is on this PATH.
        dirname = shutil.which('dirname')
        self.assertIsNotNone(dirname)
        (self.bin / 'dirname').symlink_to(dirname)
        recorder = self.root / 'record ssh.py'
        recorder.write_text(
            'import json,os,sys\n'
            'print(json.dumps({"argv":sys.argv[1:], "runtime":os.environ.get("ORBIT_TEST_RUNTIME"),\n'
            ' "pythonhome":os.environ.get("PYTHONHOME"), "pythonpath":os.environ.get("PYTHONPATH"),\n'
            ' "no_site":os.environ.get("PYTHONNOUSERSITE"),\n'
            ' "no_bytecode":os.environ.get("PYTHONDONTWRITEBYTECODE")}))\n')
        fake_ssh = self.bin / 'ssh'
        fake_ssh.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                            shlex.quote(str(recorder)) + ' "$@"\n')
        fake_ssh.chmod(0o755)
        self.settings = self.root / 'new personal data'
        self.env = dict(os.environ, PATH=str(self.bin),
                        AGENT_ORBIT_DATA_DIR=str(self.settings),
                        PYTHONHOME='/nonexistent/contaminated-python-home',
                        PYTHONPATH='/nonexistent/contaminated-python-path')
        for name in ('CODEX_REMOTE_HOST', 'CODEX_TMUX_SOCKET', 'CODEX_REMOTE_DATA_DIR',
                     'ORBIT_TEST_RUNTIME'):
            self.env.pop(name, None)

    def runtime(self, path, label):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nexport ORBIT_TEST_RUNTIME=' + shlex.quote(label) +
                        '\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        path.chmod(0o755)

    def run_entry(self, entry, arguments):
        return subprocess.run(['/bin/sh', str(entry)] + arguments, env=self.env,
                              cwd=self.root, capture_output=True, text=True, timeout=5)

    def assert_attach(self, entry, host_args, runtime, socket='', session='synthetic work'):
        arguments = host_args + ['--socket', socket, 'attach', session]
        result = self.run_entry(entry, arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['runtime'], runtime)
        self.assertEqual(payload['argv'][:2], ['-t', 'orbit-test'])
        expected = ['tmux'] + (['-S', socket] if socket else []) + ['attach-session', '-t', '=' + session]
        self.assertEqual(shlex.split(payload['argv'][2]), expected)
        self.assertEqual(len(payload['argv']), 3)
        self.assertIsNone(payload['pythonhome'])
        self.assertIsNone(payload['pythonpath'])
        self.assertEqual(payload['no_site'], '1')
        self.assertEqual(payload['no_bytecode'], '1')
        # A clean first launch must not need or create a legacy personal configuration.
        self.assertFalse(self.settings.exists())
        self.assertFalse((entry.parent / 'follow.json').exists())
        return payload

    def test_packaged_engine_contains_attach_entry_points_and_import_dependencies(self):
        for name in ('codex-remote', 'agent-orbit', 'agent_orbit_cli.py',
                     'configuration.py', 'remote_bundle.py', 'resource_probe.py', 'remote_probe.py'):
            self.assertTrue((self.engine / name).is_file(), name)
        self.assertFalse((self.engine / 'settings.json').exists())
        self.assertFalse((self.engine / 'follow.json').exists())

    def test_standalone_attach_uses_sibling_runtime_without_system_python(self):
        self.assertIsNone(shutil.which('python3', path=self.env['PATH']))
        self.runtime(self.engine.parent / 'python/bin/python3', 'standalone')
        self.assert_attach(self.engine / 'codex-remote', ['--host', 'orbit-test'], 'standalone',
                           socket="/tmp/socket with 'quotes'; $(echo ignored)",
                           session="work 'quotes'; $(echo ignored)")

    def test_standalone_host_equals_override_and_default_socket(self):
        self.runtime(self.engine.parent / 'python/bin/python3', 'standalone')
        self.assert_attach(self.engine / 'agent-orbit', ['--host=orbit-test'], 'standalone')

    def test_source_entry_keeps_existing_app_runtime_priority(self):
        source = self.root / 'Source Project'
        shutil.copytree(self.engine, source)
        self.runtime(source / 'Agent Orbit.app/Contents/Resources/python/bin/python3', 'source-app')
        self.runtime(source.parent / 'python/bin/python3', 'sibling')
        self.assert_attach(source / 'codex-remote', ['--host', 'orbit-test'], 'source-app')

    def test_source_entry_preserves_system_python_fallback(self):
        source = self.root / 'Source Project'
        shutil.copytree(self.engine, source)
        self.runtime(self.bin / 'python3', 'path-fallback')
        self.runtime(source.parent / 'python/bin/python3', 'unrelated-sibling')
        self.assert_attach(source / 'agent-orbit', ['--host', 'orbit-test'], 'path-fallback')

    def test_shell_entry_preserves_session_validation(self):
        self.runtime(self.engine.parent / 'python/bin/python3', 'standalone')
        for session in ('', '-not-a-session', 'window:1'):
            with self.subTest(session=session):
                result = self.run_entry(self.engine / 'codex-remote',
                                        ['--host', 'orbit-test', 'attach', '--', session])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('完整的 tmux 会话名', result.stderr)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
