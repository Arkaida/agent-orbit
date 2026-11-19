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

    def test_legacy_settings_gain_theme_defaults_without_changing_monitor_choices_or_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, AGENT_ORBIT_DATA_DIR=folder, CODEX_REMOTE_DATA_DIR=''):
            path = Path(folder) / 'settings.json'
            original = '{"version": 1, "remote_enabled": true, "host": "myserver", "local_enabled": false, "theme": "aurora"}\n'
            path.write_text(original)
            loaded = load_settings()
            self.assertEqual((loaded['orbit_label_color'], loaded['wave_style'], loaded['wave_palette']),
                             ('theme', 'theme', 'theme'))
            self.assertEqual((loaded['wave_height'], loaded['wave_density']), (1.0, 1.0))
            self.assertFalse(loaded['local_enabled'])
            self.assertTrue(loaded['remote_enabled'])
            self.assertEqual((loaded['host'], loaded['theme']), ('myserver', 'aurora'))
            self.assertEqual(path.read_text(), original)

    def test_custom_orbit_and_wave_preferences_survive_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, AGENT_ORBIT_DATA_DIR=folder, CODEX_REMOTE_DATA_DIR=''):
            preferences = dict(orbit_label_color='status', wave_style='dots', wave_palette='ember',
                               wave_height=1.4, wave_density=.8)
            settings = validate(dict(defaults(), **preferences))
            atomic_json(Path(folder) / 'settings.json', settings)
            loaded = load_settings()
            self.assertEqual({key: loaded[key] for key in preferences}, preferences)
            self.assertTrue(loaded['local_enabled'])
            self.assertFalse(loaded['remote_enabled'])

    def test_wave_controls_reject_invalid_values_even_when_waves_are_off(self):
        invalid = [dict(orbit_label_color='purple'), dict(orbit_label_color=True),
                   dict(wave_style='sparkle'), dict(wave_style=None),
                   dict(wave_palette='neon'), dict(wave_palette=False)]
        for key, values in [('wave_height', [.49, 1.51, True, '1', float('nan'), float('inf')]),
                            ('wave_density', [.59, 1.61, False, None, float('nan'), -float('inf')])]:
            invalid.extend({key: value} for value in values)
        for change in invalid:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    validate(dict(dict(defaults(), wave_style='off'), **change))
        for height, density in [(.5, .6), (1.5, 1.6)]:
            settings = validate(dict(defaults(), orbit_label_color='status', wave_style='off',
                                     wave_palette='status', wave_height=height, wave_density=density))
            self.assertEqual((settings['wave_height'], settings['wave_density']), (height, density))

    def test_invalid_connection_or_ranges_are_rejected_before_use(self):
        for change in [dict(remote_enabled=False, local_enabled=False), dict(remote_enabled=True, host='-oProxyCommand=bad'),
                       dict(remote_enabled=True, host='host; command'), dict(interval=0), dict(interval=True),
                       dict(theme='missing'), dict(local_limit=1.5), dict(local_followed='abc')]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError): validate(dict(defaults(), **change))
        self.assertEqual(validate(dict(defaults(), remote_enabled=True, host='user@my-host'))['host'], 'user@my-host')
