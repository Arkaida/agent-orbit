#!/bin/sh
# Exercise the shipped orbit view with synthetic models and isolated preferences.
set -eu
orbit_test_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if test "$(uname -s)" != Darwin; then
    printf 'Orbit wave tests require macOS (AppKit and SwiftUI).\n' >&2
    exit 1
fi
orbit_test_arch=$(uname -m)
orbit_test_dir=$(mktemp -d "${TMPDIR:-/tmp}/agent-orbit-wave-tests.XXXXXX")
trap 'rm -rf "$orbit_test_dir"' EXIT HUP INT TERM
# Compile the actual DTOs, rather than maintaining a second model definition.
if ! awk '/^final class PanelModel: / { found = 1; exit } { print } END { if (!found) exit 1 }' \
    "$orbit_test_root/desktop/main.swift" > "$orbit_test_dir/SessionModels.swift"; then
    printf 'Could not locate the desktop model boundary in main.swift.\n' >&2
    exit 1
fi
/usr/bin/xcrun clang -fobjc-arc -fobjc-arc-exceptions -target "$orbit_test_arch-apple-macos13.0" \
    -c "$orbit_test_root/desktop/OrbitTextRenderer.m" -o "$orbit_test_dir/OrbitTextRenderer.o"
set -- -parse-as-library -target "$orbit_test_arch-apple-macos13.0" \
    -module-cache-path "$orbit_test_dir/module-cache" -framework AppKit -framework SwiftUI -framework CoreText \
    -import-objc-header "$orbit_test_root/desktop/OrbitTextRenderer.h"
if test -f /Library/Developer/CommandLineTools/usr/include/swift/module.modulemap && \
   test -f /Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap; then
    set -- "$@" -vfsoverlay "$orbit_test_root/desktop/module-overlay.json"
fi
/usr/bin/swiftc "$@" "$orbit_test_dir/OrbitTextRenderer.o" "$orbit_test_root/desktop/OrbitTextLayout.swift" "$orbit_test_root/desktop/Theme.swift" \
    "$orbit_test_root/desktop/AppSettings.swift" "$orbit_test_dir/SessionModels.swift" \
    "$orbit_test_root/desktop/OrbitView.swift" "$orbit_test_root/desktop/tests/OrbitWaveTests.swift" \
    -o "$orbit_test_dir/orbit-wave-tests"
"$orbit_test_dir/orbit-wave-tests"
