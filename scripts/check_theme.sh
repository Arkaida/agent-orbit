#!/bin/sh
# Standalone palette and configuration contracts; no collector or app launches.
set -eu
theme_test_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if test "$(uname -s)" != Darwin; then
    printf 'Theme tests require macOS (AppKit and SwiftUI).\n' >&2
    exit 1
fi
theme_test_arch=$(uname -m)
theme_test_dir=$(mktemp -d "${TMPDIR:-/tmp}/agent-orbit-theme-tests.XXXXXX")
trap 'rm -rf "$theme_test_dir"' EXIT HUP INT TERM
set -- -parse-as-library -target "$theme_test_arch-apple-macos13.0" \
    -module-cache-path "$theme_test_dir/module-cache" -framework AppKit -framework SwiftUI
if test -f /Library/Developer/CommandLineTools/usr/include/swift/module.modulemap && \
   test -f /Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap; then
    set -- "$@" -vfsoverlay "$theme_test_root/desktop/module-overlay.json"
fi
/usr/bin/swiftc "$@" "$theme_test_root/desktop/Theme.swift" \
    "$theme_test_root/desktop/AppSettings.swift" \
    "$theme_test_root/desktop/tests/ThemeTests.swift" -o "$theme_test_dir/theme-tests"
"$theme_test_dir/theme-tests"
