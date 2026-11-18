# Third-party notices

Agent Orbit application source is distributed under the MIT license.

macOS release applications include CPython from [Astral's python-build-standalone](https://github.com/astral-sh/python-build-standalone), so users do not need to install Python. The exact release URLs and SHA-256 hashes are pinned in `scripts/runtime-manifest.json`. The runtime has its own licenses, including the Python Software Foundation License and the bundled library notices. Those licenses are preserved in `Agent Orbit.app/Contents/Resources/python/`.

AppKit and SwiftUI are provided by macOS. SSH, tmux, Codex and Claude are not redistributed. No code was copied from the projects listed in `OPEN_SOURCE_RESEARCH.md`.
