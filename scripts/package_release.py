#!/usr/bin/env python3
"""Build explicit-allowlist archives, never personal config or diagnostics."""
import hashlib
from pathlib import Path
import platform
import plistlib
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'Agent Orbit.app'
with (ROOT / 'desktop/Info.plist').open('rb') as file:
    VERSION = plistlib.load(file)['CFBundleShortVersionString']
OUTPUT = ROOT / 'dist'
OUTPUT.mkdir(exist_ok=True)
files = [ROOT / name for name in ('agent-orbit', 'codex-remote', 'shortcuts.zsh', 'build_desktop.sh', 'Makefile',
                                 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'OPEN_SOURCE_RESEARCH.md',
                                 '.gitignore', 'settings.example.json')]
files += [ROOT / name for name in ('agent_orbit_cli.py', 'configuration.py', 'local_probe.py', 'remote_probe.py', 'monitor.py',
                                 'desktop_bridge.py', 'desktop_launch.py', 'completion_tracker.py',
                                 'activity_tracker.py', 'error_tracker.py')]
files += sorted(ROOT.glob('test_*.py'))
for folder in ('desktop', 'docs', 'scripts', '.github'):
    files += [path for path in (ROOT / folder).rglob('*') if path.is_file() and '__pycache__' not in path.parts]
source_zip = OUTPUT / ('agent-orbit-' + VERSION + '-source.zip')
with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        archive.write(path, 'agent-orbit-' + VERSION + '/' + str(path.relative_to(ROOT)))
app_zip = OUTPUT / ('Agent-Orbit-' + VERSION + '-macos-' + platform.machine() + '.zip')
if app_zip.exists(): app_zip.unlink()
subprocess.run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--noextattr', '--noacl', '--keepParent', str(APP), str(app_zip)], check=True)
checksums = '\n'.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name for path in (source_zip, app_zip)) + '\n'
(OUTPUT / 'SHA256SUMS').write_text(checksums)
print(source_zip)
print(app_zip)
