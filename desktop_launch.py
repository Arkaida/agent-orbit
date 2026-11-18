"""Open the bundled native macOS panel, building it only when necessary."""
import subprocess


def launch(root, host, socket):
    app = root / 'Agent Orbit.app'
    binary = app / 'Contents' / 'MacOS' / 'AgentOrbit'
    sources = [root / 'desktop' / name for name in ('main.swift', 'PanelView.swift', 'OrbitView.swift', 'SettingsView.swift', 'DemoSupport.swift', 'Info.plist')]
    if not binary.exists() or any(path.stat().st_mtime > binary.stat().st_mtime for path in sources):
        result = subprocess.run(['/bin/sh', str(root / 'build_desktop.sh')])
        if result.returncode:
            raise RuntimeError('无法构建悬浮窗，请检查 Swift 编译工具是否可用。')
    command = ['/usr/bin/open', str(app), '--args', '--project', str(root)]
    if host: command += ['--host', host, '--socket', socket]
    result = subprocess.run(command)
    if result.returncode:
        raise RuntimeError('无法打开悬浮窗。')
