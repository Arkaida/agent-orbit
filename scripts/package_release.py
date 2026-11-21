#!/usr/bin/env python3
"""Build explicit-allowlist archives, never personal config or diagnostics."""
import argparse
import hashlib
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--include-demo', action='store_true', help='also attach the existing demo video')
options = parser.parse_args()
APP = ROOT / 'Agent Orbit.app'
with (ROOT / 'desktop/Info.plist').open('rb') as file:
    source_info = plistlib.load(file)
VERSION = source_info['CFBundleShortVersionString']
with (APP / 'Contents/Info.plist').open('rb') as file:
    app_info = plistlib.load(file)
if any(app_info[key] != source_info[key] for key in ('CFBundleShortVersionString', 'CFBundleVersion', 'LSMinimumSystemVersion')):
    raise RuntimeError('Application version differs from the source; rebuild before packaging')
build_info = subprocess.check_output(['/usr/bin/xcrun', 'vtool', '-show-build',
                                     str(APP / 'Contents/MacOS' / app_info['CFBundleExecutable'])], text=True)
minimums = re.findall(r'^\s*minos\s+([0-9.]+)\s*$', build_info, re.MULTILINE)
def version(value):
    parts = tuple(int(part) for part in value.split('.'))
    return parts + (0,) * max(0, 3 - len(parts))
if not minimums or any(version(value) > version(app_info['LSMinimumSystemVersion']) for value in minimums):
    raise RuntimeError('Application requires a newer macOS than declared; rebuild with the configured deployment target')
OUTPUT = ROOT / 'dist'
OUTPUT.mkdir(exist_ok=True)
files = [ROOT / name for name in ('agent-orbit', 'codex-remote', 'shortcuts.zsh', 'build_desktop.sh', 'Makefile',
                                 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md', 'OPEN_SOURCE_RESEARCH.md',
                                 '.gitignore', 'settings.example.json')]
files += [ROOT / name for name in ('agent_orbit_cli.py', 'configuration.py', 'local_probe.py', 'remote_probe.py', 'monitor.py',
                                 'desktop_bridge.py', 'desktop_launch.py', 'completion_tracker.py', 'response_tracker.py', 'resource_probe.py', 'remote_bundle.py',
                                 'activity_tracker.py', 'manual_tracker.py', 'usage_tracker.py', 'error_tracker.py')]
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
assets = [source_zip, app_zip]
if options.include_demo:
    video = OUTPUT / 'agent-orbit-demo.mp4'
    shutil.copy2(ROOT / 'docs/media/agent-orbit-demo.mp4', video)
    assets.append(video)
checksums = '\n'.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name for path in assets) + '\n'
(OUTPUT / 'SHA256SUMS').write_text(checksums)
for asset in assets:
    print(asset)
print(OUTPUT / 'SHA256SUMS')
