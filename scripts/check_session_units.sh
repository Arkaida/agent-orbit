#!/bin/sh
# Compile the shipped models and grouping helpers without launching the app.
set -eu
session_test_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if test "$(uname -s)" != Darwin; then
    printf 'Session unit tests require macOS (AppKit and SwiftUI).\n' >&2
    exit 1
fi
session_test_arch=$(uname -m)
session_test_dir=$(mktemp -d "${TMPDIR:-/tmp}/agent-orbit-session-tests.XXXXXX")
trap 'rm -rf "$session_test_dir"' EXIT HUP INT TERM
# Keep one implementation: test the actual declarations above PanelModel.
if ! awk '/^final class PanelModel: / { found = 1; exit } { print } END { if (!found) exit 1 }' \
    "$session_test_root/desktop/main.swift" > "$session_test_dir/SessionModels.swift"; then
    printf 'Could not locate the desktop model boundary in main.swift.\n' >&2
    exit 1
fi
set -- -parse-as-library -target "$session_test_arch-apple-macos13.0" \
    -module-cache-path "$session_test_dir/module-cache" -framework AppKit -framework SwiftUI
if test -f /Library/Developer/CommandLineTools/usr/include/swift/module.modulemap && \
   test -f /Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap; then
    set -- "$@" -vfsoverlay "$session_test_root/desktop/module-overlay.json"
fi
/usr/bin/swiftc "$@" "$session_test_root/desktop/Theme.swift" \
    "$session_test_root/desktop/AppSettings.swift" "$session_test_dir/SessionModels.swift" \
    "$session_test_root/desktop/tests/SessionUnitTests.swift" -o "$session_test_dir/session-tests"
"$session_test_dir/session-tests"
