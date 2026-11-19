import AppKit
import SwiftUI

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
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
                FollowTextEditor(text: $followed).frame(height: 72).background(ThemePalette.ui(.surface)).cornerRadius(6)
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
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
            }
            Section("刷新") {
                HStack { Text("采样间隔"); Slider(value: $draft.interval, in: 0.25...5, step: 0.25); Text(String(format: "%.2f 秒", draft.interval)).monospacedDigit().frame(width: 70) }
                Text("保留终端仅作参考，等待状态继续依据主任务记录与稳定输入页面。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
            }
        }.formStyle(.grouped)
    }
    var appearance: some View {
        Form {
            Section("皮肤") {
                Picker("窗口与悬浮球", selection: $draft.theme) {
                    ForEach(Array(zip(ThemePalette.names, ThemePalette.titles)), id: \.0) { item in Text(item.1).tag(item.0) }
                }.pickerStyle(.radioGroup)
                Picker("状态配色", selection: $draft.orbitLabelColor) {
                    Text("皮肤状态色").tag("theme")
                    Text("经典状态色").tag("status")
                }
                Text("名字、状态文字、标记与圆弧使用同一套状态色；每套皮肤均区分运行、等待、待处理与异常。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
                HStack(spacing: 12) {
                    ForEach(Array(zip([SessionVisualState.running, .waiting, .attention, .error, .unknown, .offline],
                                      ["运行", "等待", "待处理", "异常", "待核实", "离线"])), id: \.0) { state, title in
                        HStack(spacing: 4) {
                            Circle().fill(PaletteResolver(theme: draft.theme, statusStyle: draft.orbitLabelColor).ui(state.role))
                                .frame(width: 6, height: 6)
                            Text(title)
                        }
                    }
                }.font(.system(size: 10))
                    .foregroundColor(PaletteResolver(theme: draft.theme, statusStyle: draft.orbitLabelColor).ui(.text))
                    .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 6).fill(PaletteResolver(theme: draft.theme, statusStyle: draft.orbitLabelColor).ui(.base)))
            }
            Section("外围声浪") {
                Picker("样式", selection: $draft.waveStyle) {
                    Text("跟随皮肤").tag("theme")
                    Text("声浪柱").tag("bars")
                    Text("连续曲线").tag("curve")
                    Text("节奏光点").tag("dots")
                    Text("关闭").tag("off")
                }
                Picker("配色", selection: $draft.wavePalette) {
                    Text("跟随皮肤").tag("theme")
                    Text("冰蓝").tag("ice")
                    Text("极光紫").tag("aurora")
                    Text("暖焰").tag("ember")
                    Text("按运行状态").tag("status")
                }.disabled(draft.waveStyle == "off")
                HStack {
                    Text("高度")
                    Slider(value: $draft.waveHeight, in: 0.5...1.5, step: 0.05)
                    Text("\(Int((draft.waveHeight * 100).rounded()))%").monospacedDigit().frame(width: 44)
                }.disabled(draft.waveStyle == "off")
                HStack {
                    Text("密度")
                    Slider(value: $draft.waveDensity, in: 0.6...1.6, step: 0.1)
                    Text("\(Int((draft.waveDensity * 100).rounded()))%").monospacedDigit().frame(width: 44)
                }.disabled(draft.waveStyle == "off")
                Text("选择跟随皮肤，声浪样式与配色会随皮肤一起切换。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
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
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .scrollIndicators(.visible)
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
                    .font(.callout).foregroundColor(ThemePalette.ui(.muted))
            }
        }.formStyle(.grouped)
    }
    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "版本未知"
    }
    var about: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("Agent Orbit", systemImage: "circle.hexagongrid.fill").font(.title2.bold())
            Text("\(appVersion) · macOS 原生 CLI 会话监控").foregroundColor(ThemePalette.ui(.muted))
            Text("持续监控本机 Codex CLI 与 SSH 远端 Codex CLI / Claude Code CLI，提供完成提醒、异常提示和桌面悬浮球。")
            Text("开源许可：MIT\n界面：AppKit / SwiftUI\n采集：Python 标准库，读取会话状态")
                .font(.callout).foregroundColor(ThemePalette.ui(.muted))
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
            if !error.isEmpty { Text(error).font(.caption).foregroundColor(ThemePalette.ui(.error)).padding(.horizontal, 20).padding(.top, 8) }
            HStack {
                Button("取消", action: close).keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存设置") {
                    do { try model.saveSettings(draft, follow: followed); close() }
                    catch { self.error = error.localizedDescription }
                }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
            }.padding(18)
        }.frame(width: 540, height: 640).preferredColorScheme(ThemePalette.scheme)
            .tint(ThemePalette.ui(.accent))
    }
}
