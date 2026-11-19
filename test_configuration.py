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
            self.assertEqual((loaded['wave_height'], loaded['wave_density']), (1.3, 1.0))
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

    def test_reminder_preferences_migrate_without_rewriting_and_round_trip(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, AGENT_ORBIT_DATA_DIR=folder, CODEX_REMOTE_DATA_DIR=''):
            path = Path(folder) / 'settings.json'
            original = '{"version":1,"local_enabled":true,"theme":"graphite"}\n'
            path.write_text(original)
            loaded = load_settings()
            self.assertEqual((loaded['wait_reminder_enabled'], loaded['wait_reminder_minutes'], loaded['wait_reminder_sound']), (True, 5, True))
            self.assertEqual(path.read_text(), original)
            loaded.update(wait_reminder_enabled=False, wait_reminder_minutes=12, wait_reminder_sound=False)
            atomic_json(path, loaded)
            restored = load_settings()
            self.assertEqual((restored['wait_reminder_enabled'], restored['wait_reminder_minutes'], restored['wait_reminder_sound']), (False, 12, False))

    def test_reminder_controls_reject_invalid_types_and_thresholds(self):
        for field in ['wait_reminder_enabled', 'wait_reminder_sound']:
            for invalid in [None, 0, 1, 'true']:
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError):
                        validate(dict(defaults(), **{field: invalid}))
        for invalid in [0, -1, 121, float('nan'), float('inf'), True, '5']:
            with self.subTest(minutes=invalid):
                with self.assertRaises(ValueError):
                    validate(dict(defaults(), wait_reminder_minutes=invalid))
        for valid in [1, 5, 120]:
            self.assertEqual(validate(dict(defaults(), wait_reminder_minutes=valid))['wait_reminder_minutes'], valid)

    def test_groups_are_opt_in_and_round_trip_with_cross_source_members(self):
        self.assertEqual(defaults()['session_groups'], [])
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, AGENT_ORBIT_DATA_DIR=folder, CODEX_REMOTE_DATA_DIR=''):
            path = Path(folder) / 'settings.json'
            path.write_text('{"version":1,"theme":"aurora"}')
            self.assertEqual(load_settings()['session_groups'], [])
            groups = [dict(id='work-pair', name='实验组', members=['remote:atlas', 'local:thread-alpha'])]
            settings = validate(dict(load_settings(), session_groups=groups))
            atomic_json(path, settings)
            self.assertEqual(load_settings()['session_groups'], groups)
            settings['session_groups'] = []
            atomic_json(path, settings)
            self.assertEqual(load_settings()['session_groups'], [])

    def test_group_members_cannot_overlap_or_have_invalid_identities(self):
        def group(**changes):
            return dict(dict(id='pair', name='Work', members=['remote:atlas', 'local:thread-alpha']), **changes)
        invalid = [None, {}, [None], [group(id='')], [group(name=' ')],
                   [group(id='a\nb')], [group(name='x' * 41)], [group(name='👩‍💻' * 20)], [group(members=['remote:atlas'])],
                   [group(members=['remote:atlas', 'remote:atlas'])],
                   [group(members=['remote:atlas', 'local:'])],
                   [group(members=['remote:atlas', 'other:beta'])],
                   [group(members=['remote:atlas', 4])],
                   [group(), group(id='another')],
                   [group(), group(members=['remote:beta', 'remote:gamma'])]]
        for groups in invalid:
            with self.subTest(groups=groups):
                with self.assertRaises(ValueError):
                    validate(dict(defaults(), session_groups=groups))

    def test_new_wave_styles_and_compact_defaults_are_available(self):
        self.assertEqual((defaults()['bubble_scale'], defaults()['wave_height']), (.85, 1.3))
        for style in ['plasma', 'aurora', 'radar']:
            self.assertEqual(validate(dict(defaults(), wave_style=style))['wave_style'], style)
