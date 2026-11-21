#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from configuration import load_settings, read_follow as configured_follow, save_follow
from remote_bundle import build_remote_source

ROOT = Path(__file__).resolve().parent
try:
    SETTINGS = load_settings(ROOT)
except (OSError, ValueError) as error:
    print('设置错误：' + str(error), file=sys.stderr)
    sys.exit(1)
p = argparse.ArgumentParser(description='Agent Orbit：监控本机 Codex 与远端 Codex / Claude 会话')
p.add_argument('--host', default=os.environ.get('CODEX_REMOTE_HOST', SETTINGS['host']), help='SSH 主机别名或 user@host')
p.add_argument('--socket', default=os.environ.get('CODEX_TMUX_SOCKET', SETTINGS['tmux_socket']), help='远端 tmux socket；留空使用默认')
p.add_argument('--local', action='store_true', help='监控本机 Codex')
sub = p.add_subparsers(dest='action')
sub.add_parser('list', help='列出所有窗口状态（默认）')
sub.add_parser('panel', help='打开始终置顶的桌面悬浮窗')
w = sub.add_parser('watch', help='持续刷新状态'); w.add_argument('-i', '--interval', type=float, default=SETTINGS['interval'])
w.add_argument('--json', action='store_true', help='持续输出原始采样数据，用于诊断')
w.add_argument('--all', action='store_true', help='显示全部会话')
f = sub.add_parser('follow', help='管理关注列表'); f.add_argument('operation', choices=['add', 'remove', 'list', 'clear']); f.add_argument('names', nargs='*')
s = sub.add_parser('show', help='查看窗口当前画面'); s.add_argument('target', help='会话名或 pane ID，如 %%53')
a = sub.add_parser('attach', help='进入原 tmux 会话'); a.add_argument('session', help='会话名')
args = p.parse_args()
host_override = bool(os.environ.get('CODEX_REMOTE_HOST')) or any(value == '--host' or value.startswith('--host=') for value in sys.argv[1:])
args.local = args.local or not SETTINGS['remote_enabled'] and not host_override
action = args.action or 'list'

def read_follow():
    try:
        return configured_follow(ROOT)
    except (OSError, ValueError) as e:
        raise RuntimeError('无法读取关注列表：{}'.format(e)) from e

def fetch(selected=None):
    if args.local:
        from local_probe import LocalCollector
        return LocalCollector(SETTINGS['local_codex_home'], SETTINGS['local_limit']).snapshot(selected or SETTINGS['local_followed'])['panes']
    if not args.host or args.host.startswith('-'):
        raise RuntimeError('请在设置中填写 SSH 主机，或使用 --host 主机名。')
    script = build_remote_source(root=ROOT).encode()
    command = 'python3 - ' + shlex.quote(args.socket) + ' ' + shlex.quote(json.dumps(selected or [], ensure_ascii=False))
    try:
        result = subprocess.run(['ssh', '-o', 'ConnectTimeout=10', args.host, command], input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        if result.returncode:
            try: detail = json.loads(result.stdout).get('error', '')
            except (ValueError, AttributeError): detail = ''
            raise RuntimeError(detail or result.stderr.decode(errors='replace').strip() or 'SSH 连接失败')
        return json.loads(result.stdout)['panes']
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as e:
        raise RuntimeError(str(e)) from e

def listing(rows, followed=None):
    print(('本机 Codex' if args.local else '远端：{}    tmux：{}'.format(args.host, args.socket or '默认')) + '    刷新：' + time.strftime('%Y-%m-%d %H:%M:%S'))
    print('会话 / 窗格 / 状态 / 最近内容')
    for x in rows:
        status = x['status']
        if sys.stdout.isatty() and status.startswith('等待输入') and not os.environ.get('NO_COLOR'):
            status = '\033[32m' + status + '\033[0m'
        print('{}  [{}]  {}'.format(x['session'], x['window']+'.'+x['pane'], status))
        print('  ' + '\n  '.join(x.get('preview') or ['暂无正文']))
    if followed is not None:
        missing = sorted(set(followed) - {x['session'] for x in rows})
        for name in missing:
            print('{}  未找到会话（可能已结束或改名）'.format(name))
        if not followed:
            print('关注列表为空，当前显示全部会话；用 ao + 会话名 添加。')
    if not rows: print('没有找到窗口。')

try:
    if action == 'panel':
        from desktop_launch import launch
        launch(ROOT, args.host if host_override else None, args.socket)
    elif action == 'follow':
        followed = read_follow()
        if args.operation in ('add', 'remove') and not args.names:
            raise RuntimeError('请提供至少一个会话名。')
        if args.operation == 'add':
            for name in args.names:
                if name not in followed: followed.append(name)
        elif args.operation == 'remove':
            followed = [name for name in followed if name not in args.names]
        elif args.operation == 'clear':
            followed = []
        if args.operation != 'list':
            save_follow(followed)
        print('关注列表：' + ('、'.join(followed) if followed else '空（监控全部会话）'))
    elif action == 'attach':
        if args.local: raise RuntimeError('attach 用于远端 tmux；本机会话请在原 Codex 客户端查看。')
        if args.session.startswith('-') or ':' in args.session or not args.session:
            raise RuntimeError('请输入完整的 tmux 会话名。')
        command = 'tmux {}attach-session -t {}'.format(('-S ' + shlex.quote(args.socket) + ' ') if args.socket else '', shlex.quote('='+args.session))
        os.execvp('ssh', ['ssh', '-t', args.host, command])
    elif action == 'show':
        rows = [r for r in fetch() if r['session'] == args.target or r['pane_id'] == args.target]
        if not rows: raise RuntimeError('未找到该会话或窗口。')
        for r in rows:
            print('\n--- {} {}.{} {}：{} ---'.format(r['session'], r['window'], r['pane'], r['pane_id'], r['status']))
            print(r['screen'])
    elif action == 'watch':
        from monitor import watch
        watch(args, read_follow)
    else:
        listing(fetch())
except KeyboardInterrupt:
    print('\n已退出。')
except RuntimeError as e:
    print('错误：{}'.format(e), file=sys.stderr)
    sys.exit(1)
