import AppKit
import SwiftUI

private struct GroupCandidate: Identifiable {
    var id: String
    var label: String
}

private struct SessionGroupEditor: View {
    @Binding var group: SessionGroup
    let candidates: [GroupCandidate]
    let assignedElsewhere: Set<String>
    let remove: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                TextField("组名", text: $group.name).textFieldStyle(.roundedBorder)
                Button(role: .destructive, action: remove) { Image(systemName: "trash") }
                    .buttonStyle(.borderless).help("拆散这个组，成员恢复独立统计")
            }
            ForEach(candidates) { candidate in
                Toggle(candidate.label, isOn: Binding(get: {
                    group.members.contains(candidate.id)
                }, set: { selected in
                    if selected && !group.members.contains(candidate.id) { group.members.append(candidate.id) }
                    else if !selected { group.members.removeAll { $0 == candidate.id } }
                }))
                .font(.caption)
                .disabled(assignedElsewhere.contains(candidate.id) && !group.members.contains(candidate.id))
                .help(assignedElsewhere.contains(candidate.id) ? "这个会话已分配到其他组；请先从原组移出。" : candidate.label)
            }
            Text("已选择 \(group.members.count) 个会话 · 至少 2 个，或删除此组恢复独立")
                .font(.caption).foregroundColor(ThemePalette.ui(.muted))
        }.padding(.vertical, 5)
    }
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
    private var groupCandidates: [GroupCandidate] {
        var labels: [String: String] = [:]
        let remoteNames = Set(followed.components(separatedBy: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",，、"))).filter { !$0.isEmpty })
        for pane in model.rows {
            let local = pane.source == "local"
            let id = pane.groupId ?? (local ? "local:" + (pane.threadId ?? pane.paneId) : "remote:" + pane.session)
            labels[id] = (local ? "本机 · " : "远端 · ") + pane.session
        }
        for name in followed.components(separatedBy: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",，、"))) where !name.isEmpty {
            labels["remote:" + name] = "远端 · " + name
        }
        for session in model.localSessions { labels["local:" + session.id] = "本机 · " + session.session }
        for id in draft.sessionGroups.flatMap(\.members) where labels[id] == nil {
            labels[id] = id.hasPrefix("remote:") ? "远端 · " + String(id.dropFirst(7)) : "本机 · " + String(id.dropFirst(6).prefix(16))
        }
        var candidates: [GroupCandidate] = []
        for (id, label) in labels {
            let local = id.hasPrefix("local:")
            let enabled = local ? draft.localEnabled : draft.remoteEnabled
            let selected: Bool
            if local { selected = draft.localFollowed.isEmpty || draft.localFollowed.contains(String(id.dropFirst(6))) }
            else { selected = remoteNames.isEmpty || remoteNames.contains(String(id.dropFirst(7))) }
            let suffix = !enabled ? "（来源关闭）" : !selected ? "（未关注）" : ""
            candidates.append(GroupCandidate(id: id, label: label + suffix))
        }
        return candidates.sorted { $0.label < $1.label }
    }
    private func groupEditor(_ binding: Binding<SessionGroup>) -> some View {
        let id = binding.wrappedValue.id
        let elsewhere = Set(draft.sessionGroups.filter { $0.id != id }.flatMap(\.members))
        return SessionGroupEditor(group: binding, candidates: groupCandidates, assignedElsewhere: elsewhere) {
            draft.sessionGroups.removeAll { $0.id == id }
        }
    }
    private var sessionGroupsSection: some View {
        Section("互斥会话组") {
            Text("默认独立统计。把不能同时运行的会话放进同一组，联合计算空闲与运行时间；组内任一会话工作时，不催其他等待成员。")
                .font(.caption).foregroundColor(ThemePalette.ui(.muted))
            ForEach($draft.sessionGroups) { binding in groupEditor(binding) }
            Button("新建会话组") {
                draft.sessionGroups.append(SessionGroup(id: UUID().uuidString, name: "新会话组", members: []))
            }.disabled(draft.sessionGroups.count >= 32 || groupCandidates.count < 2)
            if groupCandidates.count < 2 {
                Text("先关注至少两个会话，即可选择成员。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
            }
            Text("移出成员即可恢复独立统计。分组不会自动关注成员；成员缺失或离线时，整组空闲计时暂停。")
                .font(.caption).foregroundColor(ThemePalette.ui(.muted))
        }
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
            sessionGroupsSection
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
                Toggle("多层资源声浪", isOn: $draft.waveResources).disabled(draft.waveStyle == "off")
                HStack(spacing: 10) {
                    ForEach(ResourceRole.allCases, id: \.self) { role in
                        HStack(spacing: 4) {
                            Image(systemName: role.symbolName)
                                .foregroundColor(Color(nsColor: PaletteResolver(theme: draft.theme, statusStyle: draft.orbitLabelColor).resourceColor(role)))
                            Text(role.title)
                        }.help(role.graphicDescription + "\n" + role.description)
                    }
                }.font(.system(size: 10)).foregroundColor(PaletteResolver(theme: draft.theme, statusStyle: draft.orbitLabelColor).ui(.text))
                Text("输出、进程 I/O、工具与网络共用一圈基线，展示最近 24 秒；内存是静态细弧。没有活动不画起伏，未知不填零，等待时收起。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
                Button("声浪图说明…") {
                    NotificationCenter.default.post(name: Notification.Name("AgentOrbit.OpenWaveGuide"), object: nil)
                }.help("查看五种信号的形状、含义与观测范围，不会改变当前设置")
                Picker("样式", selection: $draft.waveStyle) {
                    Text("跟随皮肤").tag("theme")
                    Text("全息声浪柱 · 分段峰值").tag("bars")
                    Text("连续曲线").tag("curve")
                    Text("节奏光点").tag("dots")
                    Text("等离子双波 · 多层粒子").tag("plasma")
                    Text("极光光带 · 多层轨迹").tag("aurora")
                    Text("雷达涟漪 · 输出触发").tag("radar")
                    Text("关闭").tag("off")
                }.disabled(draft.waveResources && draft.waveStyle != "off")
                Picker("配色", selection: $draft.wavePalette) {
                    Text("跟随皮肤").tag("theme")
                    Text("冰蓝").tag("ice")
                    Text("极光紫").tag("aurora")
                    Text("暖焰").tag("ember")
                    Text("按运行状态").tag("status")
                }.disabled(draft.waveStyle == "off" || draft.waveResources)
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
                HStack {
                    Text("低活动灵敏度")
                    Slider(value: $draft.waveSensitivity, in: 0.6...1.8, step: 0.1)
                    Text("\(Int((draft.waveSensitivity * 100).rounded()))%").monospacedDigit().frame(width: 44)
                }.disabled(draft.waveStyle == "off")
                Text(draft.waveResources && draft.waveStyle != "off" ? "多层共用同一圈基线并叠加皮肤资源色；关闭多层可调整单层样式与配色。" : "选择跟随皮肤，声浪样式与配色会随皮肤一起切换。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
            }
            Section("显示") {
                Toggle("始终置顶", isOn: $draft.pinned)
                Toggle("显示最近工作内容", isOn: $draft.showPreviews)
                Toggle("显示子智能体与保留终端数量", isOn: $draft.showBackgroundCounts)
                Toggle("减少装饰动画", isOn: $draft.reduceMotion)
                Toggle("启动时收起为悬浮球", isOn: $draft.launchCompact)
                Text("悬浮球单击切换大小，双击打开面板；按住可拖动。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
                HStack { Text("悬浮球大小"); Slider(value: $draft.bubbleScale, in: 0.7...1.5, step: 0.05); Text("\(Int((draft.bubbleScale * 100).rounded()))%").frame(width: 44) }
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
                Toggle("提醒我处理久等的会话", isOn: $draft.waitReminderEnabled)
                HStack {
                    Text("等待阈值")
                    Stepper(value: $draft.waitReminderMinutes, in: 1...120, step: 1) {
                        Text("\(Int(draft.waitReminderMinutes)) 分钟").monospacedDigit()
                    }
                }.disabled(!draft.waitReminderEnabled)
                Toggle("久等时响一声", isOn: $draft.waitReminderSound)
                    .disabled(!draft.waitReminderEnabled || !draft.sound)
                Text("每轮等待只提醒一次。进入会话可静音当前提醒；稍后提醒会延后一个阈值周期。会话继续工作后，旧等待自动清除。首次遇到已等待的会话从观察时开始计时。")
                    .font(.caption).foregroundColor(ThemePalette.ui(.muted))
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
