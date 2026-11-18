import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from configuration import atomic_json, data_dir, defaults, load_settings, read_follow, save_follow, validate


class ConfigurationTests(unittest.TestCase):
    def test_renamed_app_preserves_existing_personal_directory(self):
        with tempfile.TemporaryDirectory() as folder, patch('configuration.Path.home', return_value=Path(folder)), patch('configuration.sys.platform', 'darwin'), patch.dict(os.environ, {'AGENT_ORBIT_DATA_DIR': '', 'CODEX_REMOTE_DATA_DIR': ''}):
            parent = Path(folder) / 'Library/Application Support'
            self.assertEqual(data_dir(), parent / 'Agent Orbit')
            (parent / 'Codex Remote').mkdir(parents=True)
            self.assertEqual(data_dir(), parent / 'Codex Remote')
            (parent / 'Agent Orbit').mkdir()
            self.assertEqual(data_dir(), parent / 'Agent Orbit')

    def test_clean_install_only_enables_local_and_legacy_keeps_remote(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CODEX_REMOTE_DATA_DIR=folder + '/personal'):
            root = Path(folder) / 'project'; root.mkdir()
            self.assertFalse(load_settings(root)['remote_enabled'])
            self.assertTrue(load_settings(root)['local_enabled'])
            (root / 'follow.json').write_text('["work1"]')
            self.assertTrue(load_settings(root)['remote_enabled'])
            self.assertEqual(read_follow(root), ['work1'])
            save_follow(['new', 'new'])
            self.assertEqual(read_follow(root), ['new'])
            self.assertEqual(json.loads((root / 'follow.json').read_text()), ['work1'])

    def test_saved_settings_override_defaults_without_writing_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CODEX_REMOTE_DATA_DIR=folder + '/personal'):
            root = Path(folder) / 'project'; root.mkdir()
            settings = defaults(root); settings.update(theme='aurora', interval=1.25)
            atomic_json(Path(folder) / 'personal/settings.json', settings)
            self.assertEqual(load_settings(root)['theme'], 'aurora')
            self.assertEqual(load_settings(root)['interval'], 1.25)
            self.assertEqual(list(root.iterdir()), [])

    def test_invalid_connection_or_ranges_are_rejected_before_use(self):
        for change in [dict(remote_enabled=False, local_enabled=False), dict(remote_enabled=True, host='-oProxyCommand=bad'),
                       dict(remote_enabled=True, host='host; command'), dict(interval=0), dict(interval=True),
                       dict(theme='missing'), dict(local_limit=1.5), dict(local_followed='abc')]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError): validate(dict(defaults(), **change))
        self.assertEqual(validate(dict(defaults(), remote_enabled=True, host='user@my-host'))['host'], 'user@my-host')
