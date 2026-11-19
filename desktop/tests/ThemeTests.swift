import AppKit
import SwiftUI

/// Public contracts for the palette and settings, independent of panes/windows.
private final class ThemeTestSuite {
    var assertions = 0
    var failures: [String] = []
    private let states = SessionVisualState.allCases

    func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        if !condition() { failures.append(message) }
    }

    private func nativeToken(_ color: NSColor) -> UInt32 {
        guard let rgb = color.usingColorSpace(.sRGB) else {
            failures.append("A native palette color could not resolve to sRGB")
            return 0
        }
        func channel(_ value: CGFloat) -> UInt32 { UInt32(min(255, max(0, (value * 255).rounded()))) }
        check(abs(rgb.alphaComponent - 1) < 0.0001, "Palette RGB tokens must remain opaque through the native bridge")
        return channel(rgb.redComponent) << 16 | channel(rgb.greenComponent) << 8 | channel(rgb.blueComponent)
    }

    private func channels(_ token: UInt32) -> [Double] {
        [Double((token >> 16) & 255), Double((token >> 8) & 255), Double(token & 255)].map { $0 / 255 }
    }

    private func linear(_ value: Double) -> Double {
        value <= 0.04045 ? value / 12.92 : pow((value + 0.055) / 1.055, 2.4)
    }

    private func contrast(_ foreground: UInt32, _ background: UInt32) -> Double {
        func luminance(_ token: UInt32) -> Double {
            let rgb = channels(token).map(linear)
            return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        }
        let a = luminance(foreground), b = luminance(background)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    private func lab(_ token: UInt32) -> [Double] {
        let rgb = channels(token).map(linear)
        let x = (0.4124564 * rgb[0] + 0.3575761 * rgb[1] + 0.1804375 * rgb[2]) / 0.95047
        let y = 0.2126729 * rgb[0] + 0.7151522 * rgb[1] + 0.0721750 * rgb[2]
        let z = (0.0193339 * rgb[0] + 0.1191920 * rgb[1] + 0.9503041 * rgb[2]) / 1.08883
        func f(_ value: Double) -> Double {
            value > 216.0 / 24389.0 ? pow(value, 1.0 / 3.0) : (24389.0 / 27.0 * value + 16) / 116
        }
        return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))]
    }

    private func distance(_ first: UInt32, _ second: UInt32) -> Double {
        let a = lab(first), b = lab(second)
        return sqrt(zip(a, b).map { pow($0.0 - $0.1, 2) }.reduce(0, +))
    }

    func registryAndSemanticPalettes() {
        check(states.count == 6, "The six session states must remain independently represented")
        check(Set(SkinDefinition.all.map(\.id)).count == SkinDefinition.all.count, "Built-in skin identifiers must be unique")
        check(Set(["neon", "aurora", "graphite", "daylight"]).isSubset(of: Set(SkinDefinition.all.map(\.id))), "All four shipped skins must remain available")
        for skin in SkinDefinition.all {
            check(Set(skin.tokens.keys) == Set(ThemeRole.allCases), "\(skin.id) must define every role")
            for mode in ["theme", "status"] {
                let palette = PaletteResolver(skin: skin, statusStyle: mode)
                let context = skin.id + "/" + mode
                let colors = states.map(palette.session)
                check(Set(colors).count == states.count, "\(context): running/waiting/attention/error/unknown/offline must have distinct colors")
                for i in states.indices {
                    for j in states.indices where j > i {
                        // CIE76 15 is deliberately much larger than a barely
                        // noticeable difference; color still supplements text.
                        check(distance(colors[i], colors[j]) >= 15, "\(context): \(states[i]) and \(states[j]) are too similar (ΔE76 < 15)")
                    }
                }
                for role in [ThemeRole.text, .muted] + states.map(\.role) {
                    for background in [ThemeRole.base, .surface] {
                        let ratio = contrast(palette.token(role), palette.token(background))
                        check(ratio >= 4.5, "\(context): \(role) on \(background) contrast is \(String(format: "%.2f", ratio)), below 4.5")
                    }
                }
                ThemePalette.configure(theme: skin.id, statusStyle: mode)
                for role in ThemeRole.allCases {
                    check(nativeToken(ThemePalette.ns(role)) == palette.token(role), "\(context): NSColor bridge disagrees for \(role)")
                    check(nativeToken(NSColor(ThemePalette.ui(role))) == palette.token(role), "\(context): SwiftUI bridge disagrees for \(role)")
                }
                for state in states {
                    check(nativeToken(ThemePalette.session(state)) == palette.session(state), "\(context): session bridge disagrees for \(state)")
                }
            }
        }
    }

    func statePrecedence() {
        let cases: [(String, Bool, Bool?, String?, SessionVisualState)] = [
            ("running", true, nil, nil, .running), ("idle", true, true, nil, .waiting),
            ("approval", true, true, nil, .attention), ("input", true, true, nil, .attention),
            ("paused", true, true, nil, .attention), ("running", true, true, "warning", .attention),
            ("unknown", true, true, "warning", .unknown), ("future-state", true, true, nil, .unknown),
            ("error", true, true, nil, .error), ("interrupted", true, true, nil, .error),
            ("stopped", true, true, nil, .error), ("missing", true, false, nil, .error),
            ("idle", true, true, "error", .error), ("unknown", true, true, "error", .error),
            ("idle", true, false, nil, .offline), ("idle", false, true, nil, .offline),
            ("running", false, true, nil, .offline)
        ]
        for (state, connected, available, level, expected) in cases {
            check(SessionVisualState.resolve(state: state, connected: connected, available: available, attentionLevel: level) == expected,
                  "State precedence failed for \(state), connected=\(connected), available=\(String(describing: available)), level=\(String(describing: level))")
        }
        for label in ["config_error", "monitor_error", "remote_error"] {
            check(ThemePalette.connection(connected: false, state: label) == .error, "Connection incident \(label) must be an error")
        }
        for label in ["partial", "reconnecting", "delayed"] {
            check(ThemePalette.connection(connected: true, state: label) == .attention, "Connection state \(label) must be attention")
        }
        check(ThemePalette.connection(connected: false, state: "connecting") == .unknown, "Initial connection must remain unconfirmed")
        check(ThemePalette.connection(connected: false, state: "live") == .offline, "A lost connection must not retain a live color")
        check(ThemePalette.connection(connected: true, state: "live") == .running, "A healthy connection must use the running role")
        let groups: [([SessionVisualState], SessionVisualState)] = [
            ([], .offline), ([.offline, .waiting], .waiting),
            ([.waiting, .running], .running), ([.running, .unknown], .unknown),
            ([.unknown, .running], .unknown), ([.unknown, .attention], .attention),
            ([.attention, .error], .error), ([.running, .waiting, .unknown, .error], .error),
            ([.offline, .offline], .offline), ([.waiting, .waiting], .waiting)
        ]
        for (states, expected) in groups {
            check(SessionVisualState.mostImportant(states) == expected, "Group aggregation chose the wrong overall state for \(states)")
        }
    }

    func waveSemanticsAndFallbacks() {
        for skin in SkinDefinition.all {
            for mode in ["theme", "status"] {
                let palette = PaletteResolver(skin: skin, statusStyle: mode)
                ThemePalette.configure(theme: skin.id, statusStyle: mode)
                for state in states {
                    for requested in ["theme", "status"] {
                        check(palette.waveColor(state: state, palette: requested, fraction: 0) == palette.session(state), "\(skin.id)/\(mode): \(requested) wave must start with the session color for \(state)")
                        for fraction in [CGFloat(0), 0.5, 1] {
                            let expected = palette.waveColor(state: state, palette: requested, fraction: fraction)
                            check(nativeToken(ThemePalette.waveColor(state: state, palette: requested, fraction: fraction)) == expected, "Native wave bridge changed semantic color")
                            check(expected == palette.waveColor(state: state, palette: "theme", fraction: fraction), "Legacy wave setting must not select a competing semantic palette")
                        }
                    }
                    if [.attention, .error, .unknown, .offline].contains(state) {
                        for requested in ["theme", "status", "ice", "aurora", "ember"] {
                            for fraction in [CGFloat(0), 0.5, 1] {
                                check(palette.waveColor(state: state, palette: requested, fraction: fraction) == palette.session(state), "Decorative \(requested) wave hid \(state)")
                            }
                        }
                    }
                }
                check(palette.waveStyle("theme") == skin.defaultWaveStyle, "Default wave style must come from the skin")
                check(palette.waveLineWidth("theme") == skin.defaultWaveLineWidth, "Default wave width must come from the skin")
                for explicit in ["bars", "curve", "dots", "off"] {
                    check(palette.waveStyle(explicit) == explicit, "Explicit wave style \(explicit) must be respected")
                }
                check(palette.waveColor(state: .running, palette: "ice", fraction: -1) == palette.waveColor(state: .running, palette: "ice", fraction: 0), "Gradient input must clamp at the start")
                check(palette.waveColor(state: .running, palette: "ice", fraction: 2) == palette.waveColor(state: .running, palette: "ice", fraction: 1), "Gradient input must clamp at the end")
                check(palette.waveColor(state: .running, palette: "ice", fraction: .nan) == palette.waveColor(state: .running, palette: "ice", fraction: 0), "A nonfinite gradient value must remain deterministic")
            }
        }
        let fallback = PaletteResolver(theme: "not-an-installed-skin", statusStyle: "future-mode")
        check(fallback.skin.id == SkinDefinition.all[0].id && fallback.statusStyle == "theme", "Unknown stored preferences need a safe palette fallback")
    }

    func legacySettingsRoundTrip() {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        for json in ["{}", "{\"version\":1,\"theme\":\"aurora\",\"orbit_label_color\":\"theme\",\"local_codex_home\":\"/Users/demo/.codex\"}"] {
            do {
                let loaded = try decoder.decode(AppSettings.self, from: Data(json.utf8))
                check(loaded.orbitLabelColor == "theme", "Legacy/default settings must retain their compatible theme value")
                let restored = try decoder.decode(AppSettings.self, from: loaded.encoded())
                check(restored.theme == loaded.theme && restored.orbitLabelColor == loaded.orbitLabelColor, "Saving and reopening old settings must preserve the selected mode")
                let palette = PaletteResolver(theme: restored.theme, statusStyle: restored.orbitLabelColor)
                check(Set(states.map(palette.session)).count == states.count, "Old theme settings must produce six semantic colors, not one accent")
            } catch { failures.append("Legacy settings could not round-trip: \(error)") }
        }
    }

    func injectedSkinExtension() {
        let tokens: [ThemeRole: UInt32] = [
            .base: 0xF7F9FB, .surface: 0xFFFFFF, .accent: 0x13579B, .secondaryAccent: 0x9B57AC,
            .text: 0x102030, .muted: 0x435363, .running: 0x2468AC, .waiting: 0x247C58,
            .attention: 0x856500, .error: 0xAA3355, .unknown: 0x7256A8, .offline: 0x607080
        ]
        let skin = SkinDefinition(id: "qa-extension", title: "QA extension", light: true, tokens: tokens, defaultWaveStyle: "dots", defaultWaveLineWidth: 1.1)
        let palette = PaletteResolver(skin: skin, statusStyle: "theme")
        for role in ThemeRole.allCases {
            check(palette.token(role) == tokens[role], "An injected skin role must not fall back to hardcoded built-in colors")
            check(nativeToken(palette.ns(role)) == tokens[role], "An injected skin must reach native previews through the same color roles")
            check(nativeToken(NSColor(palette.ui(role))) == tokens[role], "An injected skin must reach SwiftUI previews through the same color roles")
        }
        for state in states {
            check(palette.session(state) == tokens[state.role], "An injected skin must color session states centrally")
            check(palette.waveColor(state: state, palette: "theme", fraction: 0) == tokens[state.role], "An injected skin must also color semantic waves centrally")
        }
        check(palette.waveStyle("theme") == "dots" && palette.waveLineWidth("theme") == 1.1, "A new skin's wave defaults must not need renderer changes")
        let classic = PaletteResolver(skin: skin, statusStyle: "status")
        check(classic.token(.base) == tokens[.base], "Classic status mode must preserve the selected skin's nonsemantic colors")
        check(classic.session(.waiting) != palette.session(.waiting), "Classic status mode must still apply to injected skins")
    }

    func previewIsolation() {
        ThemePalette.configure(theme: "neon", statusStyle: "theme")
        let originalTheme = ThemePalette.current
        let originalColors = ThemeRole.allCases.map { nativeToken(ThemePalette.ns($0)) }
        for skin in SkinDefinition.all {
            for mode in ["theme", "status"] {
                let preview = PaletteResolver(skin: skin, statusStyle: mode)
                for role in ThemeRole.allCases {
                    check(nativeToken(preview.ns(role)) == preview.token(role), "Local native preview must use its own palette")
                    check(nativeToken(NSColor(preview.ui(role))) == preview.token(role), "Local SwiftUI preview must use its own palette")
                }
                check(ThemePalette.current == originalTheme, "Opening a palette preview must not switch the live theme")
                check(ThemeRole.allCases.map { nativeToken(ThemePalette.ns($0)) } == originalColors, "Opening a palette preview must not recolor the live monitor")
            }
        }
    }
}

@main
private struct ThemeTests {
    static func main() {
        let suite = ThemeTestSuite()
        suite.registryAndSemanticPalettes()
        suite.statePrecedence()
        suite.waveSemanticsAndFallbacks()
        suite.legacySettingsRoundTrip()
        suite.injectedSkinExtension()
        suite.previewIsolation()
        if !suite.failures.isEmpty {
            for message in suite.failures { fputs("FAIL: " + message + "\n", stderr) }
            fputs("Theme tests failed: \(suite.failures.count) failures across \(suite.assertions) assertions\n", stderr)
            exit(1)
        }
        print("Theme tests passed: \(suite.assertions) assertions across palettes, state precedence, native bridges, waves, legacy settings, skin extension and isolated previews")
    }
}
