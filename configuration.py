"""Portable, per-user configuration shared by the CLI and desktop app."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile


def data_dir():
    override = os.environ.get('AGENT_ORBIT_DATA_DIR') or os.environ.get('CODEX_REMOTE_DATA_DIR')
    if override:
        return Path(override).expanduser()
    if sys.platform == 'darwin':
        parent = Path.home() / 'Library/Application Support'
        current, legacy = parent / 'Agent Orbit', parent / 'Codex Remote'
    else:
        parent = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
        current, legacy = parent / 'agent-orbit', parent / 'codex-remote'
    return legacy if legacy.exists() and not current.exists() else current


def defaults(root=None):
    legacy = root is not None and (Path(root) / 'follow.json').exists()
    return dict(version=1, remote_enabled=legacy, host='myserver' if legacy else '',
                tmux_socket=os.environ.get('CODEX_TMUX_SOCKET', ''), local_enabled=True,
                local_codex_home=os.environ.get('CODEX_HOME', str(Path.home() / '.codex')),
                local_limit=16, local_followed=[], interval=.5, theme='neon',
                orbit_label_color='theme', wave_style='theme', wave_palette='theme',
                wave_height=1.3, wave_density=1.0, wave_resources=True, wave_sensitivity=1.0, pinned=True,
                sound=True, completion_sound=True, error_sound=True,
                wait_reminder_enabled=True, wait_reminder_minutes=5, wait_reminder_sound=True, show_previews=True,
                show_background_counts=True, bubble_scale=.85, opacity=1.0,
                reduce_motion=False, launch_compact=False, session_groups=[])


def validate(settings):
    result = dict(settings)
    for key in ('remote_enabled', 'local_enabled', 'pinned', 'sound', 'completion_sound',
                'error_sound', 'wait_reminder_enabled', 'wait_reminder_sound',
                'show_previews', 'show_background_counts', 'reduce_motion', 'launch_compact', 'wave_resources'):
        if type(result.get(key)) is not bool:
            raise ValueError(key + ' 必须是开关值')
    if not result['remote_enabled'] and not result['local_enabled']:
        raise ValueError('请至少启用一种监控来源')
    host = result.get('host', '')
    if not isinstance(host, str) or (host and not re.fullmatch(r'[A-Za-z0-9_\[][A-Za-z0-9_.@:\[\]-]*', host)):
        raise ValueError('SSH 主机请填写配置别名或 user@host')
    if result['remote_enabled'] and not host:
        raise ValueError('启用远端监控需要填写 SSH 主机')
    for key in ('tmux_socket', 'local_codex_home'):
        if not isinstance(result.get(key), str) or '\0' in result[key] or '\n' in result[key]:
            raise ValueError(key + ' 不是有效路径')
    if not result['local_codex_home'].strip():
        raise ValueError('请填写本机 Codex 数据目录')
    for key, low, high in [('interval', .25, 5), ('bubble_scale', .7, 1.5), ('opacity', .65, 1), ('local_limit', 1, 64),
                           ('wave_height', .5, 1.5), ('wave_density', .6, 1.6), ('wave_sensitivity', .6, 1.8), ('wait_reminder_minutes', 1, 120)]:
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError('{} 需要在 {}–{} 之间'.format(key, low, high))
    if int(result['local_limit']) != result['local_limit']:
        raise ValueError('本机会话数量需要是整数')
    if result.get('theme') not in ('neon', 'aurora', 'graphite', 'daylight'):
        raise ValueError('未知皮肤')
    for key, options, label in [
            ('orbit_label_color', ('theme', 'status'), '状态配色'),
            ('wave_style', ('theme', 'bars', 'curve', 'dots', 'plasma', 'aurora', 'radar', 'off'), '声浪样式'),
            ('wave_palette', ('theme', 'ice', 'aurora', 'ember', 'status'), '声浪配色')]:
        value = result.get(key)
        if not isinstance(value, str) or value not in options:
            raise ValueError(label + '不是可用选项')
    if not isinstance(result.get('local_followed'), list) or not all(isinstance(x, str) for x in result['local_followed']):
        raise ValueError('本机关注列表格式不正确')
    groups = result.get('session_groups')
    if not isinstance(groups, list) or len(groups) > 32:
        raise ValueError('会话组需要是列表，最多支持 32 组')
    group_ids, grouped_members = set(), set()
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError('会话组格式不正确')
        gid, name, members = group.get('id'), group.get('name'), group.get('members')
        if (not isinstance(gid, str) or not gid.strip() or len(gid) > 128 or gid in group_ids or
                not isinstance(name, str) or not name.strip() or len(name) > 40 or
                any(char in name + gid for char in ('\n', '\0'))):
            raise ValueError('会话组需要唯一标识和 1–40 字的组名')
        if not isinstance(members, list) or not 2 <= len(members) <= 64:
            raise ValueError('每个会话组需要选择 2–64 个会话')
        for member in members:
            if (not isinstance(member, str) or not member.startswith(('remote:', 'local:')) or
                    not member.split(':', 1)[1].strip() or len(member) > 512 or
                    any(char in member for char in ('\n', '\0')) or member in grouped_members):
                raise ValueError('会话组成员无效，或同一会话被重复编组')
            grouped_members.add(member)
        group_ids.add(gid)
    return result


def load_settings(root=None):
    settings = defaults(root)
    path = data_dir() / 'settings.json'
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved, dict) or saved.get('version', 1) != 1:
            raise ValueError('设置文件版本不受支持')
        settings.update({k: v for k, v in saved.items() if k in settings})
    return validate(settings)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_follow(root):
    path = data_dir() / 'follow.json'
    if not path.exists():
        path = Path(root) / 'follow.json'
    if not path.exists():
        return []
    names = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
        raise ValueError('关注列表格式不正确')
    return list(dict.fromkeys(names))


def save_follow(names):
    atomic_json(data_dir() / 'follow.json', list(dict.fromkeys(names)))
