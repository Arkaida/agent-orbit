import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from remote_bundle import build_remote_source


class RemoteBundleTests(unittest.TestCase):
    def test_remote_import_needs_no_installed_project_and_preserves_stream_input(self):
        source = '''import json,sys
from resource_probe import ProcessResources
from process_snapshot import ProcessSnapshot
print(json.dumps({'argv':sys.argv[1:], 'subscription':json.loads(sys.stdin.readline()),
                  'resource':ProcessResources.__name__, 'process_snapshot':ProcessSnapshot.__name__, 'file':__file__}))
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-c', build_remote_source(source),
                                     'socket with spaces', '--stream'], cwd=directory,
                                    input='{"selected":["synthetic"]}\n', capture_output=True,
                                    text=True, timeout=3, check=True)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['argv'], ['socket with spaces', '--stream'])
        self.assertEqual(payload['subscription'], {'selected': ['synthetic']})
        self.assertEqual(payload['resource'], 'ProcessResources')
        self.assertEqual(payload['process_snapshot'], 'ProcessSnapshot')
        self.assertEqual(payload['file'], 'remote_probe.py')

    def test_once_off_stdin_source_keeps_arguments(self):
        source = 'import json,sys; print(json.dumps(sys.argv[1:]))'
        result = subprocess.run([sys.executable, '-I', '-', 'socket', '["a"]'],
                                input=build_remote_source(source), capture_output=True,
                                text=True, timeout=3, check=True)
        self.assertEqual(json.loads(result.stdout), ['socket', '["a"]'])

    def test_full_bundle_fits_a_single_remote_argument(self):
        bundle = build_remote_source()
        self.assertLess(len(bundle.encode()), 120000)
        compile(bundle, '<remote-bundle>', 'exec')


if __name__ == '__main__':
    unittest.main()
