#!/usr/bin/env python3
"""Fetch a pinned, verified and relocatable runtime; used only while building."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination, arch):
    manifest = json.loads((ROOT / 'scripts/runtime-manifest.json').read_text())
    asset = manifest['assets'][arch]
    cache = ROOT / '.build-runtime'
    cache.mkdir(exist_ok=True)
    archive = cache / (asset['sha256'] + '.tar.gz')
    if not archive.exists():
        download = archive.with_suffix('.download')
        with urllib.request.urlopen(asset['url'], timeout=60) as response, download.open('wb') as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(download.read_bytes()).hexdigest() != asset['sha256']:
            download.unlink()
            raise RuntimeError('Python runtime checksum mismatch')
        download.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != asset['sha256']:
        raise RuntimeError('Cached Python runtime checksum mismatch')
    with tempfile.TemporaryDirectory(dir=cache) as folder, tarfile.open(archive) as source:
        for member in source.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != 'python' or member.isdev():
                raise RuntimeError('Unsafe runtime archive member')
            if member.issym() or member.islnk():
                link = str(name.parent / member.linkname) if member.issym() else member.linkname
                normalized = PurePosixPath(os.path.normpath(link))
                if normalized.is_absolute() or not normalized.parts or normalized.parts[0] != 'python' or '..' in normalized.parts:
                    raise RuntimeError('Unsafe runtime archive link')
        source.extractall(folder)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(Path(folder) / 'python', destination, symlinks=True)
    (destination / 'agent-orbit-runtime.json').write_text(json.dumps(manifest, indent=2) + '\n')
    subprocess.run([str(destination / 'bin/python3'), '-B', '-I', '-c', 'import sqlite3,ssl,json,selectors; print("Bundled Python is ready")'], check=True)
    for folder in destination.rglob('__pycache__'):
        shutil.rmtree(folder)


def sign(destination):
    magic = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
    binaries = []
    for path in destination.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as file:
                if file.read(4) in magic: binaries.append(path)
    for path in binaries:
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    print('Signed {} runtime binaries'.format(len(binaries)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--arch', default=platform.machine(), choices=('arm64', 'x86_64'))
    parser.add_argument('--sign-only', action='store_true')
    args = parser.parse_args()
    if args.sign_only: sign(args.destination)
    else: prepare(args.destination, args.arch)
