#!/bin/sh
# Real text engine and exception recovery, using synthetic labels only.
set -eu
text_test_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if test "$(uname -s)" != Darwin; then
    printf 'Text renderer tests require macOS (AppKit and CoreText).\n' >&2
    exit 1
fi
text_test_arch=$(uname -m)
text_test_dir=$(mktemp -d "${TMPDIR:-/tmp}/agent-orbit-text-tests.XXXXXX")
trap 'rm -rf "$text_test_dir"' EXIT HUP INT TERM
/usr/bin/xcrun clang -fobjc-arc -fobjc-arc-exceptions -DORBIT_TEXT_TESTING -target "$text_test_arch-apple-macos13.0" \
    -c "$text_test_root/desktop/OrbitTextRenderer.m" -o "$text_test_dir/OrbitTextRenderer.o"
/usr/bin/xcrun clang -fobjc-arc -fobjc-arc-exceptions -DORBIT_TEXT_TESTING -target "$text_test_arch-apple-macos13.0" \
    -I "$text_test_root/desktop" "$text_test_dir/OrbitTextRenderer.o" \
    "$text_test_root/desktop/tests/OrbitTextRendererTests.m" -framework AppKit -framework CoreText \
    -o "$text_test_dir/text-boundary-tests"
"$text_test_dir/text-boundary-tests"
set -- -parse-as-library -target "$text_test_arch-apple-macos13.0" \
    -module-cache-path "$text_test_dir/module-cache" -framework AppKit -framework CoreText \
    -import-objc-header "$text_test_root/desktop/OrbitTextRenderer.h" -Xcc -DORBIT_TEXT_TESTING
if test -f /Library/Developer/CommandLineTools/usr/include/swift/module.modulemap && \
   test -f /Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap; then
    set -- "$@" -vfsoverlay "$text_test_root/desktop/module-overlay.json"
fi
/usr/bin/swiftc "$@" "$text_test_dir/OrbitTextRenderer.o" \
    "$text_test_root/desktop/OrbitTextLayout.swift" "$text_test_root/desktop/tests/TextTests.swift" \
    -o "$text_test_dir/text-tests"
"$text_test_dir/text-tests"
