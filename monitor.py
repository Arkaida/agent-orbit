"""Persistent SSH transport and a terminal-size-aware, nonblocking dashboard."""
import curses
import json
import locale
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys
import time
import unicodedata
from remote_probe import issue
from remote_bundle import build_remote_source

VERSION = '5.0.0'


def prepare_locale():
    # macOS does not provide C.UTF-8, even when inherited from an SSH/IDE shell.
    # ncurses needs a valid UTF-8 LC_CTYPE to render Chinese cell widths.
    for candidate in ('', 'en_US.UTF-8', 'C.UTF-8', 'UTF-8'):
        try:
            locale.setlocale(locale.LC_CTYPE, candidate)
            if 'UTF' in locale.nl_langinfo(locale.CODESET).upper():
                return
        except locale.Error:
            continue


def clip(text, width):
    """Clip by terminal cells, excluding control characters from remote text."""
    out, size = [], 0
    for ch in text:
        if unicodedata.category(ch).startswith('C'):
            continue
        cells = 0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        if size + cells > width:
            break
        out.append(ch)
        size += cells
    return ''.join(out)


class Stream:
    def __init__(self, host, socket, source, interval, selected):
        self.host, self.socket, self.source = host, socket, build_remote_source(source)
        self.interval, self.selected = interval, selected
        self.process = None
        self.selector = selectors.DefaultSelector()
        self.buffer = b''
        self.stderr = b''
        self.last_packet = None
        self.started = 0
        self.retry_at = 0
        self.error = ''
        self.connections = 0
        self.total_frames = 0
        self.cursors = {}

    def connect(self):
        self.process = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.buffer, self.stderr = b'', b''
        self.started, self.last_packet = time.monotonic(), None
        self.connections += 1
        for pipe, name in [(self.process.stdout, 'stdout'), (self.process.stderr, 'stderr')]:
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, name)
        self.error = ''

    def command(self):
        remote = 'python3 -u -c {} {} {} --stream {} {}'.format(
            shlex.quote(self.source), shlex.quote(self.socket),
            shlex.quote(json.dumps(self.selected, ensure_ascii=False)), self.interval,
            shlex.quote(json.dumps(self.cursors, ensure_ascii=False)))
        return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', self.host, remote]

    def stop_process(self):
        if self.process:
            for pipe in [self.process.stdout, self.process.stderr]:
                try:
                    self.selector.unregister(pipe)
                except (KeyError, ValueError):
                    pass
                pipe.close()
            try:
                self.process.stdin.close()
            except (OSError, BrokenPipeError):
                pass
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=.3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None

    def close(self):
        self.stop_process()
        self.selector.close()

    def fail(self, error):
        self.error = error
        self.stop_process()
        self.retry_at = time.monotonic() + 2

    def subscribe(self, selected):
        if self.selected == selected:
            return
        self.selected = selected
        if self.process:
            try:
                self.process.stdin.write((json.dumps({'selected': selected}) + '\n').encode())
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                self.fail('连接已断开，正在重连')

    def poll(self):
        now = time.monotonic()
        if not self.process:
            if now < self.retry_at:
                return []
            try:
                self.connect()
            except OSError as e:
                self.fail(str(e))
                return []
        frames = []
        # Do not let a busy stream starve keyboard input / display updates.
        deadline = time.monotonic() + .04
        while time.monotonic() < deadline:
            ready = self.selector.select(0)
            if not ready:
                break
            for key, _ in ready:
                chunk = os.read(key.fileobj.fileno(), 262144)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                if key.data == 'stderr':
                    self.stderr = (self.stderr + chunk)[-4096:]
                else:
                    self.buffer += chunk
                    while b'\n' in self.buffer:
                        line, self.buffer = self.buffer.split(b'\n', 1)
                        try:
                            frame = json.loads(line)
                            if not isinstance(frame, dict) or not ('panes' in frame or 'error' in frame):
                                continue
                        except (ValueError, UnicodeDecodeError):
                            continue
                        self.last_packet = time.monotonic()
                        self.error = ''
                        self.total_frames += 1
                        frames.append(frame)
        if self.process.poll() is not None:
            reason = self.stderr.decode(errors='replace').strip() or '远端连接已结束'
            self.fail(reason)
        elif time.monotonic() - (self.last_packet or self.started) > max(10, self.interval * 3):
            self.fail('长时间未收到画面，正在重连')
        return frames


class LocalStream(Stream):
    def __init__(self, root, home, interval, selected, limit=16):
        super().__init__('本机', '', '', interval, selected)
        self.root, self.home, self.limit = Path(root), home, limit

    def command(self):
        return [sys.executable, '-u', str(self.root / 'local_probe.py'), '--stream', '--home', self.home,
                '--interval', str(self.interval), '--limit', str(self.limit), '--selected', json.dumps(self.selected)]


class Model:
    def __init__(self):
        self.rows, self.last_received = [], None
        self.changes = {}
        self.frame = {}
        self.remote_error = ''
        self.vanished = {}

    def accept(self, frame, now=None):
        now = time.monotonic() if now is None else now
        if 'error' in frame:
            self.remote_error = frame['error']
            return
        self.remote_error = ''
        self.frame = frame
        incoming = frame.get('panes', [])
        incoming_ids = {row['pane_id'] for row in incoming}
        slots = {(r['session'], r.get('window'), r.get('pane')) for r in incoming}
        for previous in self.rows:
            if previous['pane_id'] not in incoming_ids and (previous['session'], previous.get('window'), previous.get('pane')) not in slots:
                if previous.get('source') == 'local' and previous.get('state') == 'idle':
                    continue  # Closing an already finished local chat is normal.
                vanished = dict(previous, state='missing', status='会话 / 窗格已消失', lifecycle_events=[], working='')
                vanished.update(issue('会话或窗格已关闭、退出或改名', 'missing'))
                self.vanished[previous['pane_id']] = vanished
        self.vanished = {key: value for key, value in list(self.vanished.items())[-100:]
                         if key not in incoming_ids and (value['session'], value.get('window'), value.get('pane')) not in slots}
        self.rows = incoming
        self.last_received = now
        for row in self.rows:
            key = row['pane_id']
            old = self.changes.get(key)
            fingerprint = row.get('fingerprint', row.get('screen'))
            if old is None or old[0] != fingerprint:
                self.changes[key] = (fingerprint, now)

    def visible(self, selected):
        if selected == ['__none__']:
            return []
        rows = [r for r in self.rows + list(self.vanished.values()) if not selected or r['session'] in selected or r.get('thread_id') in selected]
        found = {key for r in rows for key in (r['session'], r.get('thread_id'))}
        for name in selected:
            if name not in found:
                rows.append(dict(session=name, pane_id='missing:' + name, state='missing',
                                 status='未找到会话', preview=[], working='', screen='', window='-', pane='-',
                                 **issue('关注的会话当前不存在', 'missing')))
        return rows


def watch(args, read_follow):
    if not .2 <= args.interval <= 60:
        raise RuntimeError('刷新间隔需要在 0.2 到 60 秒之间。')
    source = (Path(__file__).resolve().parent / 'remote_probe.py').read_text()
    selected = [] if args.all else read_follow()
    if getattr(args, 'local', False):
        from configuration import load_settings
        settings = load_settings(Path(__file__).resolve().parent)
        selected = settings['local_followed']
        read_follow = lambda: load_settings(Path(__file__).resolve().parent)['local_followed']
        stream = LocalStream(Path(__file__).resolve().parent, settings['local_codex_home'], args.interval, selected, settings['local_limit'])
    else:
        stream = Stream(args.host, args.socket, source, args.interval, selected)
    model = Model()
    config_error = ''
    next_config = 0

    def step():
        nonlocal selected, next_config, config_error
        now = time.monotonic()
        if now >= next_config:
            try:
                selected = [] if args.all else read_follow()
                stream.subscribe(selected)
                config_error = ''
            except (RuntimeError, OSError, ValueError) as e:
                config_error = str(e)
            next_config = now + .5
        frames = stream.poll()
        for frame in frames:
            model.accept(frame)
        return frames

    def connection():
        if config_error:
            return config_error, False
        if stream.error:
            return '重连中 · ' + stream.error, False
        if model.remote_error:
            return '远端读取失败 · ' + model.remote_error, False
        if stream.last_packet is None:
            return '正在连接 ' + stream.host, False
        age = time.monotonic() - stream.last_packet
        if age > max(2, args.interval * 2):
            return '数据延迟 · {:.1f} 秒未收到画面'.format(age), False
        return '已连接 · 接收 {} 帧 · {:.1f} 秒前 · 采集 {} ms'.format(
            stream.total_frames, age, model.frame.get('collection_ms', '?')), True

    def tui(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.nodelay(True)
        if curses.has_colors() and not os.environ.get('NO_COLOR'):
            curses.start_color()
            curses.use_default_colors()
            for pair, color in [(1, curses.COLOR_GREEN), (2, curses.COLOR_YELLOW), (3, curses.COLOR_CYAN), (4, curses.COLOR_RED)]:
                curses.init_pair(pair, color, -1)
        color = lambda n: curses.color_pair(n) if curses.has_colors() and not os.environ.get('NO_COLOR') else 0
        index, live = 0, False
        selected_key = None
        while True:
            step()
            rows = model.visible(selected) if model.last_received is not None else []
            if selected_key:
                index = next((i for i, r in enumerate(rows) if r['pane_id'] == selected_key), min(index, max(0, len(rows) - 1)))
            index = min(index, max(0, len(rows) - 1))
            key = screen.getch()
            if key in (ord('q'), 3):
                break
            if key in (curses.KEY_DOWN, ord('j')):
                index = min(index + 1, max(0, len(rows) - 1))
            elif key in (curses.KEY_UP, ord('k')):
                index = max(0, index - 1)
            elif key == ord('v'):
                live = not live
            elif key in (10, 13, curses.KEY_ENTER) and rows and rows[index]['state'] != 'missing':
                session = rows[index]['session']
                command = 'tmux -S {} attach-session -t {}'.format(shlex.quote(args.socket), shlex.quote('=' + session))
                curses.def_prog_mode()
                curses.endwin()
                try:
                    subprocess.run(['ssh', '-t', args.host, command])
                except KeyboardInterrupt:
                    pass
                finally:
                    curses.reset_prog_mode()
                    screen.nodelay(True)
                    screen.clear()
                    stream.stop_process()
                    stream.last_packet = None
                    stream.retry_at = 0
            selected_key = rows[index]['pane_id'] if rows else None
            height, width = screen.getmaxyx()
            screen.erase()

            def put(y, text, attr=0):
                if 0 <= y < height:
                    try:
                        screen.addstr(y, 0, clip(text, max(0, width - 1)), attr)
                    except curses.error:
                        pass

            label, connected = connection()
            put(0, 'Agent Orbit {} | {} | {} 秒采样 | {}'.format(VERSION, stream.host, args.interval, time.strftime('%H:%M:%S')), curses.A_BOLD)
            put(1, label, color(3 if connected else 4))
            body_height = max(0, height - 4)
            if live and rows:
                row = rows[index]
                put(2, '{} · {} · 实时画面'.format(row['session'], row['status']), color(1 if row['state'] == 'idle' else 2))
                for y, line in enumerate(row.get('screen', '').splitlines()[-max(1, body_height - 1):], 3):
                    if y >= height - 1:
                        break
                    put(y, line)
            else:
                page_size = max(1, body_height // 4)
                offset = (index // page_size) * page_size
                for i, row in enumerate(rows[offset:offset + page_size], offset):
                    y = 3 + (i - offset) * 4
                    changed_at = model.changes.get(row['pane_id'], (None, time.monotonic()))[1]
                    changed = max(0, time.monotonic() - changed_at)
                    status_color = 4 if row.get('attention_level') == 'error' else 1 if row['state'] == 'idle' else 2 if row['state'] in ('running', 'approval', 'input', 'paused') else 4
                    agent = 'Claude' if row.get('agent') == 'claude' else 'Codex' if row.get('agent') == 'codex' else ''
                    put(y, '{} {} [{}.{}] {}  {}  · 画面 {:.0f}s 前变化'.format(
                        '▶' if i == index else ' ', row['session'], row['window'], row['pane'], agent, row['status'], changed), color(status_color) | (curses.A_BOLD if i == index else 0))
                    put(y + 1, '  ' + (row.get('error_summary') or row.get('working') or row.get('cwd', '')), color(status_color))
                    for j, line in enumerate(row.get('preview', [])[-2:]):
                        if y + 2 + j < height - 1:
                            put(y + 2 + j, '  ' + line)
                if not rows:
                    put(3, '正在等待第一帧…' if model.last_received is None else '没有会话')
            put(height - 1, '↑↓ 选择  Enter 进入  v 实时画面/列表  q 退出   {}/{}  {}'.format(
                index + 1 if rows else 0, len(rows), '关注列表' if selected else '全部会话'))
            screen.noutrefresh()
            curses.doupdate()
            time.sleep(.1)

    try:
        if sys.stdout.isatty() and sys.stdin.isatty() and not args.json:
            prepare_locale()
            curses.wrapper(tui)
        else:
            last_label, last_report = '', 0
            while True:
                frames = step()
                for frame in frames:
                    if args.json:
                        print(json.dumps(frame, ensure_ascii=False), flush=True)
                    else:
                        label, _ = connection()
                        print('{}  {}'.format(time.strftime('%H:%M:%S'), label), flush=True)
                        for row in model.visible(selected):
                            print('{}  {}  {}\n{}'.format(row['session'], row['status'], row.get('working', ''), '\n'.join(row.get('preview', []))), flush=True)
                label, connected = connection()
                now = time.monotonic()
                if not connected and (label != last_label or now - last_report >= 5):
                    print(label, file=sys.stderr, flush=True)
                    last_label, last_report = label, now
                time.sleep(.1)
    except KeyboardInterrupt:
        pass
    finally:
        stream.close()
