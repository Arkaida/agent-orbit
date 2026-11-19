import AppKit
import SwiftUI

struct AppSettings: Codable {
    var version = 1
    var remoteEnabled = false
    var host = ""
    var tmuxSocket = ""
    var localEnabled = true
    var localCodexHome = ProcessInfo.processInfo.environment["CODEX_HOME"] ?? NSHomeDirectory() + "/.codex"
    var localLimit = 16
    var localFollowed: [String] = []
    var interval = 0.5
    var theme = "neon"
    var pinned = true
    var sound = true
    var completionSound = true
    var errorSound = true
    var showPreviews = true
    var showBackgroundCounts = true
    var bubbleScale = 1.0
    var opacity = 1.0
    var reduceMotion = false
    var launchCompact = false

    enum CodingKeys: String, CodingKey {
        case version, remoteEnabled, host, tmuxSocket, localEnabled, localCodexHome, localLimit, localFollowed,
             interval, theme, pinned, sound, completionSound, errorSound, showPreviews, showBackgroundCounts,
             bubbleScale, opacity, reduceMotion, launchCompact
    }
    init() {}
    init(from decoder: Decoder) throws {
        self.init()
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? version
        remoteEnabled = try c.decodeIfPresent(Bool.self, forKey: .remoteEnabled) ?? remoteEnabled
        host = try c.decodeIfPresent(String.self, forKey: .host) ?? host
        tmuxSocket = try c.decodeIfPresent(String.self, forKey: .tmuxSocket) ?? tmuxSocket
        localEnabled = try c.decodeIfPresent(Bool.self, forKey: .localEnabled) ?? localEnabled
        localCodexHome = try c.decodeIfPresent(String.self, forKey: .localCodexHome) ?? localCodexHome
        localLimit = try c.decodeIfPresent(Int.self, forKey: .localLimit) ?? localLimit
        localFollowed = try c.decodeIfPresent([String].self, forKey: .localFollowed) ?? localFollowed
        interval = try c.decodeIfPresent(Double.self, forKey: .interval) ?? interval
        theme = try c.decodeIfPresent(String.self, forKey: .theme) ?? theme
        pinned = try c.decodeIfPresent(Bool.self, forKey: .pinned) ?? pinned
        sound = try c.decodeIfPresent(Bool.self, forKey: .sound) ?? sound
        completionSound = try c.decodeIfPresent(Bool.self, forKey: .completionSound) ?? completionSound
        errorSound = try c.decodeIfPresent(Bool.self, forKey: .errorSound) ?? errorSound
        showPreviews = try c.decodeIfPresent(Bool.self, forKey: .showPreviews) ?? showPreviews
        showBackgroundCounts = try c.decodeIfPresent(Bool.self, forKey: .showBackgroundCounts) ?? showBackgroundCounts
        bubbleScale = try c.decodeIfPresent(Double.self, forKey: .bubbleScale) ?? bubbleScale
        opacity = try c.decodeIfPresent(Double.self, forKey: .opacity) ?? opacity
        reduceMotion = try c.decodeIfPresent(Bool.self, forKey: .reduceMotion) ?? reduceMotion
        launchCompact = try c.decodeIfPresent(Bool.self, forKey: .launchCompact) ?? launchCompact
    }
    func validate() throws {
        func invalid(_ text: String) -> NSError { NSError(domain: "Settings", code: 1, userInfo: [NSLocalizedDescriptionKey: text]) }
        guard localEnabled || remoteEnabled else { throw invalid("请至少启用一种监控来源。") }
        guard !remoteEnabled || !host.isEmpty else { throw invalid("请填写 SSH 主机别名或 user@host。") }
        if !host.isEmpty && host.range(of: "^[A-Za-z0-9_\\[][A-Za-z0-9_.@:\\[\\]-]*$", options: .regularExpression) == nil {
            throw invalid("SSH 主机不能包含空格、命令或选项。")
        }
        guard !localCodexHome.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw invalid("请填写 Codex 数据目录。") }
        guard (0.25...5).contains(interval), (1...64).contains(localLimit), (0.7...1.5).contains(bubbleScale), (0.65...1).contains(opacity), ThemePalette.names.contains(theme), version == 1 else { throw invalid("设置值超出了支持范围。") }
        guard !tmuxSocket.contains("\n"), !tmuxSocket.contains("\0"), !localCodexHome.contains("\n"), !localCodexHome.contains("\0") else { throw invalid("目录不能包含换行或空字符。") }
    }
    func encoded() throws -> Data {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }
}

enum ThemePalette {
    static let names = ["neon", "aurora", "graphite", "daylight"]
    static let titles = ["霓虹深空", "极光紫", "石墨黑", "晴空浅色"]
    static var current = "neon"
    static var light: Bool { current == "daylight" }
    static var scheme: ColorScheme { light ? .light : .dark }
    static func rgb(_ hex: UInt) -> NSColor {
        NSColor(srgbRed: Double((hex >> 16) & 255) / 255, green: Double((hex >> 8) & 255) / 255, blue: Double(hex & 255) / 255, alpha: 1)
    }
    static var base: NSColor { rgb(light ? 0xEDF2FA : current == "aurora" ? 0x100B20 : current == "graphite" ? 0x101215 : 0x060B16) }
    static var surface: NSColor { rgb(light ? 0xFFFFFF : current == "aurora" ? 0x20132F : current == "graphite" ? 0x1C2025 : 0x0B1421) }
    static var accent: NSColor { rgb(light ? 0x136FBA : current == "aurora" ? 0xBD9CFF : current == "graphite" ? 0x88C5D6 : 0x3DD9FF) }
    static var violet: NSColor { rgb(light ? 0x7856C7 : current == "aurora" ? 0xF38AC5 : 0x976EFF) }
    // Keep status meanings consistent, with a readable shade for each skin.
    static var text: NSColor { rgb(light ? 0x15263E : current == "aurora" ? 0xF3E8FF : current == "graphite" ? 0xE4E7EC : 0xE0F0FF) }
    static var muted: NSColor { rgb(light ? 0x52677F : current == "aurora" ? 0xB5A1D0 : current == "graphite" ? 0x9DA8B5 : 0x82A1C2) }
    static var green: NSColor { rgb(light ? 0x087A4F : current == "aurora" ? 0x83E7BE : current == "graphite" ? 0x9CD4B2 : 0x57FFB2) }
    static var amber: NSColor { rgb(light ? 0x986000 : current == "aurora" ? 0xF7C98B : current == "graphite" ? 0xD8BB89 : 0xFFBA59) }
    static var red: NSColor { rgb(light ? 0xC52B45 : current == "aurora" ? 0xFF8FAE : current == "graphite" ? 0xE58D9C : 0xFF5775) }
}

struct SettingsView: View {
    @ObservedObject var model: PanelModel
    let close: () -> Void
    @State private var draft: AppSettings
    @State private var followed: String
    @State private var error = ""
    @State private var selectedTab: Int
    init(model: PanelModel, initialTab: Int = 0, close: @escaping () -> Void) {
        self.model = model; self.close = close
        _draft = State(initialValue: model.settings)
        _followed = State(initialValue: model.followed.joined(separator: "\n"))
        _selectedTab = State(initialValue: initialTab)
    }
    var monitoring: some View {
        Form {
            Section("远端会话") {
                Toggle("启用 SSH 远端监控", isOn: $draft.remoteEnabled)
                TextField("SSH 主机", text: $draft.host).textFieldStyle(.roundedBorder).disabled(!draft.remoteEnabled)
                TextField("tmux Socket（留空使用默认）", text: $draft.tmuxSocket).textFieldStyle(.roundedBorder).disabled(!draft.remoteEnabled)
                Text("关注会话，每行一个；留空显示全部。")
                    .font(.caption).foregroundColor(.secondary)
                FollowTextEditor(text: $followed).frame(height: 72).background(Color(nsColor: .textBackgroundColor)).cornerRadius(6)
            }
            Section("本机 Codex") {
                Toggle("监控本机 Codex（桌面端 / CLI / VS Code）", isOn: $draft.localEnabled)
                TextField("Codex 数据目录", text: $draft.localCodexHome).textFieldStyle(.roundedBorder).disabled(!draft.localEnabled)
                Stepper("最多显示 \(draft.localLimit) 个本机会话", value: $draft.localLimit, in: 1...64)
                Toggle("关注全部当前打开的本机会话", isOn: Binding(get: { draft.localFollowed.isEmpty }, set: { all in
                    draft.localFollowed = all ? [] : model.localSessions.isEmpty ? ["__none__"] : model.localSessions.map(\.id)
                }))
                if !model.localSessions.isEmpty {
                    ForEach(model.localSessions) { session in
                        Toggle(session.session, isOn: Binding(get: { draft.localFollowed.isEmpty || draft.localFollowed.contains(session.id) }, set: { selected in
                            if draft.localFollowed.isEmpty { draft.localFollowed = model.localSessions.map(\.id) }
                            if selected { if !draft.localFollowed.contains(session.id) { draft.localFollowed.append(session.id) } }
                            else { draft.localFollowed.removeAll { $0 == session.id }; if draft.localFollowed.isEmpty { draft.localFollowed = ["__none__"] } }
                        })).font(.caption)
                    }
                }
                Text("自动发现当前打开的主会话，读取运行记录；不会向 Codex 发送输入。")
                    .font(.caption).foregroundColor(.secondary)
            }
            Section("刷新") {
                HStack { Text("采样间隔"); Slider(value: $draft.interval, in: 0.25...5, step: 0.25); Text(String(format: "%.2f 秒", draft.interval)).monospacedDigit().frame(width: 70) }
                Text("保留终端仅作参考，等待状态继续依据主任务记录与稳定输入页面。")
                    .font(.caption).foregroundColor(.secondary)
            }
        }.formStyle(.grouped)
    }
    var appearance: some View {
        Form {
            Section("皮肤") {
                Picker("窗口与悬浮球", selection: $draft.theme) {
                    ForEach(Array(zip(ThemePalette.names, ThemePalette.titles)), id: \.0) { item in Text(item.1).tag(item.0) }
                }.pickerStyle(.radioGroup)
                Text("皮肤会同时应用到窗口、会话环和设置页，状态颜色仍保留原来的含义。")
                    .font(.caption).foregroundColor(.secondary)
            }
            Section("显示") {
                Toggle("始终置顶", isOn: $draft.pinned)
                Toggle("显示最近工作内容", isOn: $draft.showPreviews)
                Toggle("显示子智能体与保留终端数量", isOn: $draft.showBackgroundCounts)
                Toggle("减少装饰动画", isOn: $draft.reduceMotion)
                Toggle("启动时收起为悬浮球", isOn: $draft.launchCompact)
                HStack { Text("悬浮球大小"); Slider(value: $draft.bubbleScale, in: 0.7...1.5, step: 0.1); Text("\(Int(draft.bubbleScale * 100))%").frame(width: 44) }
                HStack { Text("窗口不透明度"); Slider(value: $draft.opacity, in: 0.65...1, step: 0.05); Text("\(Int(draft.opacity * 100))%").frame(width: 44) }
            }
        }.formStyle(.grouped)
    }
    var notifications: some View {
        Form {
            Section("声音") {
                Toggle("启用提示音", isOn: $draft.sound)
                Toggle("任务结束时提示", isOn: $draft.completionSound).disabled(!draft.sound)
                Toggle("异常或需要处理时提示", isOn: $draft.errorSound).disabled(!draft.sound)
                Button("试听完成提示") { NSSound(named: NSSound.Name("Glass"))?.play() }
                Button("试听异常提示") { NSSound(named: NSSound.Name("Basso"))?.play() }
            }
            Section("提醒规则") {
                Text("首次打开时已经等待的会话不补报完成；恢复的旧异常会自动移除。收起为悬浮球后仍持续监控和提示。")
                    .font(.callout).foregroundColor(.secondary)
            }
        }.formStyle(.grouped)
    }
    var about: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("Agent Orbit", systemImage: "circle.hexagongrid.fill").font(.title2.bold())
            Text("5.0.0 · macOS 原生会话监控").foregroundColor(.secondary)
            Text("本机 Codex 与 SSH 远端 Codex / Claude 的持续监控、完成提醒、异常提示和桌面悬浮球。")
            Text("开源许可：MIT\n界面：AppKit / SwiftUI\n采集：Python 标准库，读取会话状态")
                .font(.callout).foregroundColor(.secondary)
            Button("打开个人设置目录") { NSWorkspace.shared.open(model.dataRoot) }
            Button("查看项目使用说明") { NSWorkspace.shared.open(model.root.appendingPathComponent("README.md")) }
            Spacer()
        }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
    }
    var body: some View {
        VStack(spacing: 0) {
            TabView(selection: $selectedTab) {
                monitoring.tabItem { Label("监控", systemImage: "antenna.radiowaves.left.and.right") }.tag(0)
                appearance.tabItem { Label("外观", systemImage: "paintpalette") }.tag(1)
                notifications.tabItem { Label("提醒", systemImage: "bell") }.tag(2)
                about.tabItem { Label("关于", systemImage: "info.circle") }.tag(3)
            }.padding(.top, 12)
            if !error.isEmpty { Text(error).font(.caption).foregroundColor(.red).padding(.horizontal, 20).padding(.top, 8) }
            HStack {
                Button("取消", action: close).keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存设置") {
                    do { try model.saveSettings(draft, follow: followed); close() }
                    catch { self.error = error.localizedDescription }
                }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
            }.padding(18)
        }.frame(width: 540, height: 640).preferredColorScheme(ThemePalette.scheme)
    }
}
