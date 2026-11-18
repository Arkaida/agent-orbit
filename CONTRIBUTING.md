# Contributing

Agent Orbit uses Python's standard library and native AppKit / SwiftUI.
Python 3.9+ runs the collectors and tests; macOS 13+ and Xcode Command Line
Tools build the desktop application.

Run `python3 -m unittest -v`, then `sh build_desktop.sh` on macOS. Changes to
status detection should include sanitized transcript/event fixtures for both
the failure and recovery paths. A quiet page alone never proves completion;
retained shells alone never prove ongoing work. Startup and reconnection must
not replay old completion sounds.

Keep collectors read-only. Never send input, install hooks, kill agents, or
change a user's Codex/tmux configuration to discover status. Keep source
availability independent when changing the combined feed.

Do not attach real chat logs, tokens, SSH configurations, or personal settings
to issues. Include the OS, Codex/Claude version, expected state, observed state,
and a minimal redacted fixture. User settings, diagnostics and backups are
excluded from Git and release archives.

Pull requests should describe the resulting behavior, relevant limitations,
and validation. Contributions use the MIT license in this repository.
