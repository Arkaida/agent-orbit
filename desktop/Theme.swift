import AppKit
import SwiftUI

/// Color names describe their purpose, so skins can change hues without changing meaning.
enum ThemeRole: String, CaseIterable {
    case base, surface, accent, secondaryAccent, text, muted
    case running, waiting, attention, error, unknown, offline
}

/// Resource channels describe measurements; their colors never select a session state.
enum ResourceRole: String, CaseIterable {
    case output, io, tools, network, memory
    var title: String {
        switch self {
        case .output: return "输出"
        case .io: return "进程 I/O"
        case .tools: return "工具"
        case .network: return "网络"
        case .memory: return "内存"
        }
    }
    var description: String {
        switch self {
        case .output: return "页面、运行记录或已确认后台进展的活动变化；这是显示活动量，不等于字节吞吐、Token 消耗或完成状态。"
        case .io: return "根进程 rchar / wchar 的变化，即系统调用读写字节；包含缓存、管道等，并非纯磁盘读写，也不是整个会话的精确 I/O。"
        case .tools: return "空心菱形表示工具已发起、尚未返回，是静态标记；实心菱形针表示新观测到工具返回。没有新返回就没有事件波峰，返回不等于执行成功、任务完成或成功率。"
        case .network: return "所选进程树可归属的 TCP 收发字节。共享连接、权限或平台限制可能让数据只有部分覆盖；未观测不会当作零。"
        case .memory: return "进程树的常驻内存 RSS，显示为静态标记；共享内存页可能重复计入。占用内存不代表正在工作。"
        }
    }
    var graphicDescription: String {
        switch self {
        case .output: return "连续填充光带 · 新增输出越多，起伏越明显"
        case .io: return "分段短柱 · 读写增量越大，柱越高"
        case .tools: return "空心菱形标记未返回；实心菱形针标记新返回"
        case .network: return "细曲线与峰帽 · 收发增量越大，起伏越明显"
        case .memory: return "静态细弧 · 按所占主机内存比例显示"
        }
    }
    var symbolName: String {
        switch self {
        case .output: return "waveform.path"
        case .io: return "arrow.left.arrow.right"
        case .tools: return "wrench.and.screwdriver"
        case .network: return "network"
        case .memory: return "memorychip"
        }
    }
}

enum SessionVisualState: String, CaseIterable {
    case running, waiting, attention, error, unknown, offline

    var priority: Int {
        switch self {
        case .error: return 0
        case .attention: return 1
        case .unknown: return 2
        case .running: return 3
        case .waiting: return 4
        case .offline: return 5
        }
    }

    static func mostImportant(_ states: [Self]) -> Self {
        states.min { $0.priority < $1.priority } ?? .offline
    }

    var role: ThemeRole {
        switch self {
        case .running: return .running
        case .waiting: return .waiting
        case .attention: return .attention
        case .error: return .error
        case .unknown: return .unknown
        case .offline: return .offline
        }
    }

    static func resolve(state: String, connected: Bool, available: Bool? = nil,
                        attentionLevel: String? = nil) -> Self {
        guard connected else { return .offline }
        if attentionLevel == "error" || ["error", "interrupted", "stopped", "missing"].contains(state) {
            return .error
        }
        guard available != false else { return .offline }
        if state == "unknown" { return .unknown }
        if attentionLevel == "warning" || ["approval", "input", "paused"].contains(state) {
            return .attention
        }
        switch state {
        case "running": return .running
        case "idle": return .waiting
        default: return .unknown
        }
    }
}

struct SkinDefinition {
    let id: String
    let title: String
    let light: Bool
    let tokens: [ThemeRole: UInt32]
    let resourceTokens: [ResourceRole: UInt32]
    let defaultWaveStyle: String
    let defaultWaveLineWidth: CGFloat

    init(id: String, title: String, light: Bool, tokens: [ThemeRole: UInt32],
         defaultWaveStyle: String, defaultWaveLineWidth: CGFloat = 1.5,
         resourceTokens: [ResourceRole: UInt32]? = nil) {
        precondition(Set(tokens.keys) == Set(ThemeRole.allCases), "Every skin must define every color role")
        precondition(tokens.values.allSatisfy { $0 <= 0xFFFFFF }, "Skin colors must be RGB tokens")
        self.id = id
        self.title = title
        self.light = light
        self.tokens = tokens
        self.resourceTokens = resourceTokens ?? [.output: tokens[.accent]!, .io: tokens[.error]!, .tools: tokens[.attention]!,
                                                  .network: tokens[.secondaryAccent]!, .memory: tokens[.text]!]
        precondition(Set(self.resourceTokens.keys) == Set(ResourceRole.allCases), "Every skin must define every resource channel")
        precondition(self.resourceTokens.values.allSatisfy { $0 <= 0xFFFFFF }, "Resource colors must be opaque RGB tokens")
        self.defaultWaveStyle = defaultWaveStyle
        self.defaultWaveLineWidth = defaultWaveLineWidth
    }

    static let all: [SkinDefinition] = [
        SkinDefinition(id: "neon", title: "霓虹深空", light: false, tokens: [
            .base: 0x060B16, .surface: 0x0B1421,
            .accent: 0x3DD9FF, .secondaryAccent: 0x976EFF,
            .text: 0xE0F0FF, .muted: 0x82A1C2,
            .running: 0x3DD9FF, .waiting: 0x57FFB2,
            .attention: 0xF4CD73, .error: 0xFF708C,
            .unknown: 0xBD9AEF, .offline: 0x7F8EA5
        ], defaultWaveStyle: "plasma", resourceTokens: [.output: 0x63D9FF, .io: 0xFF91B5, .tools: 0xFFD187, .network: 0xB99CFF, .memory: 0x7CE3C0]),
        SkinDefinition(id: "aurora", title: "极光紫", light: false, tokens: [
            .base: 0x100B20, .surface: 0x20132F,
            .accent: 0xBD9CFF, .secondaryAccent: 0xF38AC5,
            .text: 0xF3E8FF, .muted: 0xB5A1D0,
            .running: 0xBD9CFF, .waiting: 0x83E7BE,
            .attention: 0xF7CF79, .error: 0xFF8FAE,
            .unknown: 0xCFAC91, .offline: 0xA29AAF
        ], defaultWaveStyle: "aurora", defaultWaveLineWidth: 1.2, resourceTokens: [.output: 0x86D6FF, .io: 0xFF9DA6, .tools: 0xF3C57D, .network: 0xCE9EFF, .memory: 0x84DBC7]),
        SkinDefinition(id: "graphite", title: "石墨黑", light: false, tokens: [
            .base: 0x101215, .surface: 0x1C2025,
            .accent: 0x88C5D6, .secondaryAccent: 0xC0A9DA,
            .text: 0xE4E7EC, .muted: 0x9DA8B5,
            .running: 0x88C5D6, .waiting: 0x9CD4B2,
            .attention: 0xE2CB8B, .error: 0xE58D9C,
            .unknown: 0xB9A1CA, .offline: 0x8C96A3
        ], defaultWaveStyle: "radar", defaultWaveLineWidth: 0.9, resourceTokens: [.output: 0x8FCFE5, .io: 0xE8A0AB, .tools: 0xDBC084, .network: 0xC9AEEC, .memory: 0x9ACDBA]),
        SkinDefinition(id: "daylight", title: "晴空浅色", light: true, tokens: [
            .base: 0xEDF2FA, .surface: 0xFFFFFF,
            .accent: 0x136FBA, .secondaryAccent: 0x7856C7,
            .text: 0x15263E, .muted: 0x52677F,
            .running: 0x136FBA, .waiting: 0x087A4F,
            .attention: 0x8A6600, .error: 0xC52B45,
            .unknown: 0x79559A, .offline: 0x59697D
        ], defaultWaveStyle: "curve", defaultWaveLineWidth: 1.2, resourceTokens: [.output: 0x136FBA, .io: 0xB44860, .tools: 0x946000, .network: 0x7951C2, .memory: 0x24775E])
    ]
}

private struct WaveGradient {
    let start: UInt32
    let end: UInt32
}

private enum ColorCatalog {
    // Classic colors retain the same state meanings across skins. Separate light
    // tokens keep orange/yellow, purple and gray readable on a pale surface.
    static let classicDark: [ThemeRole: UInt32] = [
        .running: 0xFFAE67, .waiting: 0x72E2AE, .attention: 0xF4D66E,
        .error: 0xFF8398, .unknown: 0xC4B4F5, .offline: 0x93A1B6
    ]
    static let classicLight: [ThemeRole: UInt32] = [
        .running: 0x9E4B08, .waiting: 0x126D48, .attention: 0x866300,
        .error: 0xB02D4A, .unknown: 0x70549B, .offline: 0x586A80
    ]
    static let wavesDark: [String: WaveGradient] = [
        "ice": WaveGradient(start: 0x63E5FF, end: 0x7994FF),
        "aurora": WaveGradient(start: 0xBD9CFF, end: 0xF38AC5),
        "ember": WaveGradient(start: 0xFFD187, end: 0xFF8B7B)
    ]
    static let wavesLight: [String: WaveGradient] = [
        "ice": WaveGradient(start: 0x126EA5, end: 0x3558B8),
        "aurora": WaveGradient(start: 0x6949B8, end: 0xA4377D),
        "ember": WaveGradient(start: 0xA65B00, end: 0xC13B32)
    ]

    static func color(_ token: UInt32) -> NSColor {
        NSColor(srgbRed: Double((token >> 16) & 255) / 255,
                green: Double((token >> 8) & 255) / 255,
                blue: Double(token & 255) / 255, alpha: 1)
    }

    static func blend(_ start: UInt32, _ end: UInt32, fraction: CGFloat) -> UInt32 {
        let amount = fraction.isFinite ? min(1, max(0, Double(fraction))) : 0
        func channel(_ shift: UInt32) -> UInt32 {
            let left = Double((start >> shift) & 255)
            let right = Double((end >> shift) & 255)
            return UInt32((left + (right - left) * amount).rounded())
        }
        return (channel(16) << 16) | (channel(8) << 8) | channel(0)
    }
}

/// Immutable value resolver: tests can compare RGB tokens without a model,
/// window, mutable global palette, or an active application.
struct PaletteResolver {
    let skin: SkinDefinition
    let statusStyle: String

    init(theme: String, statusStyle: String) {
        self.init(skin: SkinDefinition.all.first { $0.id == theme } ?? SkinDefinition.all[0],
                  statusStyle: statusStyle)
    }

    init(skin: SkinDefinition, statusStyle: String) {
        self.skin = skin
        self.statusStyle = statusStyle == "status" ? "status" : "theme"
    }

    func token(_ role: ThemeRole) -> UInt32 {
        if statusStyle == "status",
           let classic = (skin.light ? ColorCatalog.classicLight : ColorCatalog.classicDark)[role] {
            return classic
        }
        // SkinDefinition validates completeness at construction.
        return skin.tokens[role]!
    }

    func ns(_ role: ThemeRole) -> NSColor { ColorCatalog.color(token(role)) }
    func ui(_ role: ThemeRole) -> Color { Color(nsColor: ns(role)) }
    func session(_ state: SessionVisualState) -> UInt32 { token(state.role) }
    func resourceToken(_ role: ResourceRole, fraction: CGFloat = 0) -> UInt32 {
        ColorCatalog.blend(skin.resourceTokens[role]!, token(.text), fraction: min(1, max(0, fraction.isFinite ? fraction : 0)) * 0.12)
    }
    func resourceColor(_ role: ResourceRole, fraction: CGFloat = 0) -> NSColor {
        ColorCatalog.color(resourceToken(role, fraction: fraction))
    }

    func waveStyle(_ requested: String) -> String {
        ["bars", "curve", "dots", "off", "plasma", "aurora", "radar"].contains(requested) ? requested : skin.defaultWaveStyle
    }

    func waveLineWidth(_ requested: String) -> CGFloat {
        if requested == "theme" { return skin.defaultWaveLineWidth }
        switch waveStyle(requested) {
        case "curve": return 1.4
        case "plasma": return 1.2
        case "aurora": return 1.1
        case "radar": return 1
        default: return 1.5
        }
    }

    func waveColor(state: SessionVisualState, palette: String, fraction: CGFloat) -> UInt32 {
        // Availability and incidents take precedence over decorative palettes.
        // A warning/unknown/error must not look like ordinary activity.
        if [.attention, .error, .unknown, .offline].contains(state) { return session(state) }
        let presets = skin.light ? ColorCatalog.wavesLight : ColorCatalog.wavesDark
        if let gradient = presets[palette] {
            return ColorCatalog.blend(gradient.start, gradient.end, fraction: fraction)
        }
        // Both legacy "theme" and "status" select the current global semantic
        // palette. A slight same-hue gradient preserves state distinctions.
        let color = session(state)
        let end = ColorCatalog.blend(color, token(.text), fraction: 0.16)
        return ColorCatalog.blend(color, end, fraction: fraction)
    }
}

/// The sole UI bridge. Configure once when settings load or are saved; views
/// ask for purpose-based colors rather than owning their own state palettes.
enum ThemePalette {
    private static var resolver = PaletteResolver(theme: "neon", statusStyle: "theme")
    static let names = SkinDefinition.all.map(\.id)
    static let titles = SkinDefinition.all.map(\.title)
    static var current: String { resolver.skin.id }
    static var light: Bool { resolver.skin.light }
    static var scheme: ColorScheme { light ? .light : .dark }

    static func configure(theme: String, statusStyle: String) {
        resolver = PaletteResolver(theme: theme, statusStyle: statusStyle)
    }

    static func ns(_ role: ThemeRole) -> NSColor { resolver.ns(role) }
    static func ui(_ role: ThemeRole) -> Color { resolver.ui(role) }
    static func session(_ state: SessionVisualState) -> NSColor { resolver.ns(state.role) }
    static func resourceColor(_ role: ResourceRole, fraction: CGFloat = 0) -> NSColor { resolver.resourceColor(role, fraction: fraction) }

    static func connection(connected: Bool, state: String) -> SessionVisualState {
        switch state {
        case "config_error", "monitor_error", "remote_error": return .error
        case "partial", "reconnecting", "delayed": return .attention
        case "connecting": return .unknown
        default: return connected ? .running : .offline
        }
    }

    static func waveStyle(_ requested: String) -> String { resolver.waveStyle(requested) }
    static func waveLineWidth(_ requested: String) -> CGFloat { resolver.waveLineWidth(requested) }
    static func waveColor(state: SessionVisualState, palette: String, fraction: CGFloat) -> NSColor {
        ColorCatalog.color(resolver.waveColor(state: state, palette: palette, fraction: fraction))
    }
}
