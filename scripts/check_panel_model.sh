#!/bin/sh
# Exercise the shipped publisher/event path without starting the app or collector.
set -eu
panel_test_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if test "$(uname -s)" != Darwin; then
    printf 'Panel model tests require macOS (AppKit and SwiftUI).\n' >&2
    exit 1
fi
panel_test_arch=$(uname -m)
panel_test_dir=$(mktemp -d "${TMPDIR:-/tmp}/agent-orbit-panel-tests.XXXXXX")
trap 'rm -rf "$panel_test_dir"' EXIT HUP INT TERM
if ! awk '/^final class FloatingPanel: / { found = 1; exit } { print } END { if (!found) exit 1 }' \
    "$panel_test_root/desktop/main.swift" > "$panel_test_dir/PanelModel.swift"; then
    printf 'Could not locate the panel model boundary in main.swift.\n' >&2
    exit 1
fi
set -- -parse-as-library -target "$panel_test_arch-apple-macos13.0" \
    -module-cache-path "$panel_test_dir/module-cache" -framework AppKit -framework SwiftUI
if test -f /Library/Developer/CommandLineTools/usr/include/swift/module.modulemap && \
   test -f /Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap; then
    set -- "$@" -vfsoverlay "$panel_test_root/desktop/module-overlay.json"
fi
/usr/bin/swiftc "$@" "$panel_test_root/desktop/Theme.swift" \
    "$panel_test_root/desktop/AppSettings.swift" "$panel_test_dir/PanelModel.swift" \
    "$panel_test_root/desktop/tests/PanelModelTests.swift" -o "$panel_test_dir/panel-model-tests"
"$panel_test_dir/panel-model-tests" --project "$panel_test_dir/project" --data-dir "$panel_test_dir/data"
