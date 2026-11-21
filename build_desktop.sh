#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
app_dir="$project_dir/Agent Orbit.app"
macos_target="$(/usr/bin/uname -m)-apple-macos13.0"
mkdir -p "$app_dir/Contents/MacOS" "$project_dir/.build-cache-fixed"
# Some upgraded Command Line Tools retain the pre-Swift-6 duplicate map.
# The overlay only affects this compiler invocation; system files are untouched.
if test -f /Library/Developer/CommandLineTools/usr/include/swift/module.modulemap && test -f /Library/Developer/CommandLineTools/usr/include/swift/bridging.modulemap; then
    /usr/bin/swiftc -O -target "$macos_target" -vfsoverlay "$project_dir/desktop/module-overlay.json" -module-cache-path "$project_dir/.build-cache-fixed" -framework AppKit -framework SwiftUI "$project_dir/desktop/Theme.swift" "$project_dir/desktop/AppSettings.swift" "$project_dir/desktop/main.swift" "$project_dir/desktop/PanelView.swift" "$project_dir/desktop/OrbitView.swift" "$project_dir/desktop/SettingsView.swift" "$project_dir/desktop/DemoSupport.swift" -o "$app_dir/Contents/MacOS/AgentOrbit"
else
    /usr/bin/swiftc -O -target "$macos_target" -module-cache-path "$project_dir/.build-cache-fixed" -framework AppKit -framework SwiftUI "$project_dir/desktop/Theme.swift" "$project_dir/desktop/AppSettings.swift" "$project_dir/desktop/main.swift" "$project_dir/desktop/PanelView.swift" "$project_dir/desktop/OrbitView.swift" "$project_dir/desktop/SettingsView.swift" "$project_dir/desktop/DemoSupport.swift" -o "$app_dir/Contents/MacOS/AgentOrbit"
fi
/bin/cp "$project_dir/desktop/Info.plist" "$app_dir/Contents/Info.plist"
mkdir -p "$app_dir/Contents/Resources"
/bin/cp "$project_dir/desktop/AgentOrbit.icns" "$app_dir/Contents/Resources/AgentOrbit.icns"
python3 "$project_dir/scripts/prepare_runtime.py" "$app_dir/Contents/Resources/python"
python3 "$project_dir/scripts/prepare_runtime.py" "$app_dir/Contents/Resources/python" --sign-only
engine_dir="$app_dir/Contents/Resources/engine"
mkdir -p "$engine_dir"
for source_file in configuration.py local_probe.py remote_probe.py resource_probe.py remote_bundle.py monitor.py desktop_bridge.py completion_tracker.py response_tracker.py manual_tracker.py activity_tracker.py usage_tracker.py error_tracker.py README.md LICENSE THIRD_PARTY_NOTICES.md; do
    if test -f "$project_dir/$source_file"; then /bin/cp "$project_dir/$source_file" "$engine_dir/$source_file"; fi
done
/usr/bin/codesign --force --sign - "$app_dir"
printf '已生成：%s\n' "$app_dir"
