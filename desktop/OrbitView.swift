import AppKit

final class BubbleView: NSView {
    var restore: (() -> Void)?
    var moved: (() -> Void)?
    var sizeChanged: ((CGFloat) -> Void)?
    private struct Node {
        var key: String
        var name: String
        var agent: String
        var status: String
        var summary: String
        var visualState: SessionVisualState
        var color: NSColor { ThemePalette.session(visualState) }
        var live: Bool { visualState != .offline }
        var running: Bool { visualState == .running }
        var waiting: Bool { visualState == .waiting }
        var attention: Bool { visualState == .attention }
        var critical: Bool { visualState == .error }
        var level: CGFloat
        var outputAge: TimeInterval?
        var observedFor: TimeInterval
        var source: String
        var pending: Bool
        var responseKey: String
        var responseKnown: Bool
        var responseElapsed: TimeInterval?
        var responseObserved: Bool
        var overdue: Bool
        var responseText: String
        var idleFraction: CGFloat?
        var timingSummary: String
        var resources: ResourceState
        var manualRunning: Bool
        var manualTarget: ManualRunTarget
    }
    private struct ResourceState {
        var memoryBytes: Double?
        var hostMemoryBytes: Double?
        var memoryPartial = false
        var ioRate: Double?
        var ioPartial = false
        var networkRate: Double?
        var networkPartial = false
        var networkShared = false
        var processShared = false
        var toolsKnown = false
        var toolsPartial = false
        var activeTools: Int?
    }
    private struct ResourceMark {
        var time: TimeInterval
        var amount: Double
        var identity: String
    }
    private struct ResourceReading {
        var epoch: String?
        var sample: Int?
        var read: Int?
        var write: Int?
        var received: Int?
        var sent: Int?
    }
    private struct ToolReading {
        var epoch: Int?
        var total: Int
    }
    private var resourceMarks: [String: [ResourceRole: [ResourceMark]]] = [:]
    private var resourceReadings: [String: ResourceReading] = [:]
    private var toolReadings: [String: ToolReading] = [:]
    private var resourceTargets: [String: Set<String>] = [:]
    private var toolTargets: [String: Set<String>] = [:]
    private var outputActivePanes = Set<String>()
    private struct Ring {
        var indices: [Int]
        var radius: CGFloat
        var phase: CGFloat = 0
        var fontSize: CGFloat = 9.4
    }
    private var ringSeparation: CGFloat { 48 }
    private struct Sample {
        var time: TimeInterval
        var level: CGFloat?
    }
    private struct OutputMark {
        var time: TimeInterval
        var amount: Int
        var paneId: String
    }
    private var outputMarks: [String: [OutputMark]] = [:]
    private var outputReadings: [String: (epoch: Int, total: Int)] = [:]
    private var observingSince: [String: TimeInterval] = [:]
    private var nodes: [Node] = []
    private var rings: [Ring] = []
    private var levels: [String: CGFloat] = [:]
    private var samples: [Sample] = []
    private var orbitRadius: CGFloat = 58
    private var lastDiameter: CGFloat = 176
    private var fullDiameter: CGFloat = 176
    private var bubbleScale: CGFloat = 1
    private let preferences: UserDefaults
    private var miniMode: Bool
    private var connected = false
    private var connectionState = "connecting"
    private var connectionMessage = "正在连接"
    private var usage: UsageMetrics?
    private var reduceMotion = false
    private var waveStyle = "theme"
    private var wavePalette = "theme"
    private var waveHeight: CGFloat = 1
    private var waveDensity: CGFloat = 1
    private var waveResources = true
    private var waveSensitivity: CGFloat = 1
    private var tint = ThemePalette.ns(.accent)
    private var hoveredName: String?
    private weak var model: PanelModel?
    private var tracking: NSTrackingArea?
    private var pressPoint: NSPoint?
    private var windowOrigin = NSPoint.zero
    private var dragged = false
    private var singleClick: DispatchWorkItem?
    private var clickGeneration: UInt64 = 0
    private var accessibilityOverview = "暂无会话数据"
    private var overviewToolTip = ""
    private var runningCount: Int { nodes.filter(\.running).count }
    private var waitingCount: Int { nodes.filter(\.waiting).count }
    private var errorCount: Int { nodes.filter(\.critical).count }
    private var warningCount: Int { nodes.filter { $0.attention && !$0.critical }.count }
    private var pendingCount: Int { Set(nodes.filter(\.pending).map(\.responseKey)).count }
    private var overdueCount: Int { Set(nodes.filter(\.overdue).map(\.responseKey)).count }
    private var responseCoverage: Bool {
        let live = nodes.filter(\.live)
        return !live.isEmpty && live.allSatisfy(\.responseKnown)
    }
    private var pendingCountText: String {
        responseCoverage ? "\(pendingCount)" : pendingCount > 0 ? "\(pendingCount)+" : "—"
    }
    private var longestWait: TimeInterval? { nodes.compactMap(\.responseElapsed).max() }
    private var longestWaitIsObserved: Bool {
        guard let longest = longestWait else { return false }
        return nodes.contains { $0.responseElapsed == longest && $0.responseObserved }
    }
    private var longestWaitValue: String {
        guard let elapsed = longestWait else { return "时长未知" }
        return (longestWaitIsObserved ? "≥" : "") + compactDuration(elapsed)
    }
    private var longestWaitText: String {
        guard let elapsed = longestWait else {
            return pendingCount > 0 ? "时长未知" : responseCoverage ? "暂无等待" : "状态待核实"
        }
        return "最久 " + (longestWaitIsObserved ? "≥" : "") + compactDuration(elapsed)
    }
    private var doubleClickDelay: TimeInterval {
        let interval = NSEvent.doubleClickInterval
        // AppKit can return zero before its normal event loop is active.
        return interval.isFinite && interval > 0 ? interval : 0.5
    }
    private var connectionNotice: String {
        switch connectionState {
        case "delayed": return "等待更新"
        case "connecting": return "连接中"
        case "remote_error": return "采样异常"
        case "config_error": return "设置异常"
        case "monitor_error": return "监控恢复中"
        case "partial": return "部分离线"
        default: return "正在重连"
        }
    }
    private var activity: CGFloat { nodes.filter(\.running).map(\.level).max() ?? 0 }

    override convenience init(frame frameRect: NSRect) {
        self.init(frame: frameRect, preferences: .standard)
    }

    init(frame frameRect: NSRect, preferences: UserDefaults) {
        self.preferences = preferences
        miniMode = preferences.bool(forKey: "orbitMiniMode")
        super.init(frame: frameRect)
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        updateAccessibilityActions()
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    deinit { singleClick?.cancel() }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var acceptsFirstResponder: Bool { true }
    override func accessibilityPerformPress() -> Bool { toggleSize(nil); return true }

    private func updateAccessibilityActions() {
        setAccessibilityHelp("单击" + (miniMode ? "放大为完整会话环" : "缩小为小号悬浮球") + "，双击打开面板；按住拖动，右键查看更多操作。空格切换大小，回车打开面板。")
        setAccessibilityValue(miniMode ? "小号悬浮球" : "完整会话环")
        setAccessibilityLabel((miniMode ? "小号悬浮球，" : "完整会话环，") + accessibilityOverview)
        setAccessibilityCustomActions([
            NSAccessibilityCustomAction(name: "打开面板", handler: { [weak self] in
                guard let self = self else { return false }; self.openPanel(nil); return true
            }),
            NSAccessibilityCustomAction(name: miniMode ? "放大会话环" : "缩小悬浮球", handler: { [weak self] in
                guard let self = self else { return false }; self.toggleSize(nil); return true
            })
        ])
    }

    private func cancelSingleClick() {
        clickGeneration &+= 1
        singleClick?.cancel()
        singleClick = nil
    }

    @objc private func openPanel(_ sender: Any?) {
        cancelSingleClick()
        restore?()
    }

    @objc private func toggleSize(_ sender: Any?) {
        cancelSingleClick()
        miniMode.toggle()
        preferences.set(miniMode, forKey: "orbitMiniMode")
        hoveredName = nil
        applySize()
        updateAccessibilityActions()
        needsDisplay = true
    }

    private func applySize() {
        let diameter: CGFloat = miniMode ? 72 : fullDiameter
        let scaledDiameter = diameter * bubbleScale
        if abs(scaledDiameter - lastDiameter) > 0.5 {
            lastDiameter = scaledDiameter
            // The host preserves the center and clamps the new frame to its screen.
            sizeChanged?(scaledDiameter)
        }
        setBoundsSize(NSSize(width: diameter, height: diameter))
    }

    func update(_ model: PanelModel) {
        self.model = model
        let now = ProcessInfo.processInfo.systemUptime
        let wasConnected = connected
        connected = model.connected
        connectionState = model.connectionState
        usage = model.metrics
        reduceMotion = model.settings.reduceMotion
        waveStyle = model.settings.waveStyle
        wavePalette = model.settings.wavePalette
        waveHeight = CGFloat(min(1.5, max(0.5, model.settings.waveHeight)))
        waveDensity = CGFloat(min(1.6, max(0.6, model.settings.waveDensity)))
        waveResources = model.settings.waveResources
        waveSensitivity = CGFloat(min(1.8, max(0.6, model.settings.waveSensitivity)))
        bubbleScale = CGFloat(model.settings.bubbleScale)
        connectionMessage = model.message
        let groups = Dictionary(grouping: model.rows, by: \.orbitKey)
        let workingKeys = Set(groups.compactMap { key, rows -> String? in
            let state = rows.contains { $0.responseGroup != nil }
                ? SessionUnit(id: key, rows: rows).visualState(connected: model.isConnected)
                : SessionVisualState.mostImportant(rows.map { $0.visualState(connected: model.isConnected($0)) })
            return state == .running ? key : nil
        })
        let eligible = Set(model.rows.filter {
            workingKeys.contains($0.orbitKey) && $0.visualState(connected: model.isConnected($0)) == .running
        }.map(\.paneId))
        let liveRows = model.rows.filter { model.isConnected($0) && $0.available != false }
        recordOutput(liveRows, eligible: eligible, now: now)
        recordResources(liveRows, eligible: eligible, now: now)
        var order: [String] = []
        let followedKeys = model.followed.compactMap { name in
            model.rows.first { $0.source != "local" && ($0.session == name || $0.monitorKey == "remote:" + name) }?.orbitKey
        }
        // Status changes never reorder names or move them between rings.
        for name in followedKeys + groups.keys.sorted() where groups[name] != nil && !order.contains(name) {
            order.append(name)
        }
        func priority(_ pane: Pane) -> Int {
            pane.visualState(connected: model.isConnected(pane)).priority
        }
        let previouslyRunning = Set(nodes.filter(\.running).map(\.key))
        nodes = order.compactMap { name in
            guard let rows = groups[name] else { return nil }
            let responseGroup = rows.compactMap(\.responseGroup).first
            func groupPriority(_ pane: Pane) -> Int {
                switch pane.visualState(connected: model.isConnected(pane)) {
                case .error: return 0
                case .attention: return 1
                case .running: return 2
                case .unknown: return 3
                case .waiting: return 4
                case .offline: return 5
                }
            }
            guard let pane = rows.min(by: { responseGroup == nil ? priority($0) < priority($1) : groupPriority($0) < groupPriority($1) }) else { return nil }
            let visualState: SessionVisualState
            if responseGroup == nil {
                visualState = pane.visualState(connected: model.isConnected(pane))
            } else {
                visualState = SessionUnit(id: name, rows: rows).visualState(connected: model.isConnected)
            }
            let live = visualState != .offline
            let running = visualState == .running
            let waiting = visualState == .waiting
            if !wasConnected || !previouslyRunning.contains(name) || observingSince[name] == nil { observingSince[name] = now }
            let recentAmount = (outputMarks[name] ?? []).filter { now - $0.time < 1.5 }.reduce(0) { $0 + $1.amount }
            let target = running ? outputLevel(recentAmount) : 0
            let level = running ? (levels[name] ?? target) * 0.4 + target * 0.6 : 0
            let recent = rows.min { ($0.activityAge ?? .infinity) < ($1.activityAge ?? .infinity) }
            levels[name] = level
            let background = rows.reduce(0) { $0 + ($1.backgroundCount ?? 0) }
            let manualRunning = rows.contains { $0.manualRunning == true }
            let status = !live ? (connectionState == "delayed" ? "等待更新" : "离线") : visualState == .error ? "异常" : visualState == .attention ? "待处理" : running ? (manualRunning ? "手动运行中" : "运行中") : waiting ? "等待输入" : "待核实"
            let summary: String
            if !live {
                summary = ""
            } else if [.error, .attention].contains(visualState) {
                let error = clean(pane.errorSummary ?? "")
                summary = error.isEmpty ? pane.status : error
            } else if running && background > 0 {
                summary = pane.workingIsBackgroundCount ? "" : clean(pane.working ?? "")
            } else if running {
                let working = clean(pane.working ?? "")
                let preview = pane.preview.reversed().map { clean($0) }.first { isWorkText($0) }
                if let range = working.range(of: "\\d+ background terminals? running", options: [.regularExpression, .caseInsensitive]) {
                    let count = working[range].prefix(while: { $0.isNumber })
                    summary = "后台终端 \(count) 运行中"
                } else {
                    summary = working.range(of: "后台|工作流|确认|等待", options: .regularExpression) != nil ? working : preview ?? "主会话运行中"
                }
            } else {
                // A completed task's old terminal output is never a current-work summary.
                summary = waiting ? "" : responseGroup != nil && visualState == .unknown ? "部分成员状态待核实" : pane.status
            }
            let counts = live && model.settings.showBackgroundCounts ? rows.compactMap(\.backgroundCountsText).joined(separator: " / ") : ""
            let members = responseGroup == nil ? "" : "成员：" + Array(Set(rows.map(\.orbitName))).sorted().joined(separator: "、")
            let detail = [summary, counts, members].filter { !$0.isEmpty }.joined(separator: " · ")
            let liveRows = rows.filter { model.isConnected($0) && $0.available != false }
            let pending = liveRows.filter { !$0.isCritical && (responseGroup == nil || [.waiting, .attention].contains(visualState)) }.compactMap(\.effectiveResponse).filter {
                ["waiting", "attention"].contains($0.kind)
            }
            let timed = pending.compactMap { response -> (elapsed: TimeInterval, observed: Bool)? in
                guard let seconds = response.elapsedSeconds,
                      seconds.isFinite, seconds >= 0 else { return nil }
                return (seconds, response.observed)
            }.max { $0.elapsed < $1.elapsed }
            let elapsed = timed?.elapsed
            let observed = timed?.observed ?? false
            let overdue = !pending.isEmpty && pending.contains { $0.overdue == true }
            let responseText = pending.isEmpty ? "" : (overdue ? "久等 · " : "待响应 · ") +
                (elapsed.map { (observed ? "至少 " : "") + duration($0) } ?? "时长未知")
            let timingPane = rows.first { $0.responseGroup != nil } ?? pane
            let timing = timingPane.effectiveResponse
            let groupName = timingPane.responseGroup.map { clean($0.name) }
            let totals = "空闲 " + exactDuration(timing?.idleSeconds) + " · 运行 " + exactDuration(timing?.runSeconds)
            var timingSummary = (groupName.map { "互斥组「" + $0 + "」共享" } ?? "") + "累计观测：" + totals
            if timingPane.responseGroup?.kind == "running" && !running { timingSummary += "，组内有成员运行，等待提醒已暂停" }
            if timingPane.responseGroup?.parallelConflict == true { timingSummary += "，组内有并行运行" }
            let agents = Array(Set(rows.map { $0.agent == "claude" ? "Claude" : $0.agent == "codex" ? "Codex" : "CLI" })).sorted().joined(separator: " / ")
            let configuredMembers = responseGroup?.members ?? []
            let manualMembers = configuredMembers.isEmpty ? rows.map(\.monitorKey) : configuredMembers
            return Node(key: name, name: responseGroup?.name ?? pane.orbitName, agent: agents, status: status, summary: detail, visualState: visualState, level: level,
                        outputAge: running ? recent?.activityAge : nil, observedFor: now - (observingSince[name] ?? now), source: recent?.activitySource ?? "",
                        pending: live && !pending.isEmpty, responseKey: timingPane.responseMonitorKey,
                        responseKnown: !liveRows.isEmpty && visualState != .unknown && liveRows.allSatisfy { $0.effectiveResponse != nil },
                        responseElapsed: live ? elapsed : nil, responseObserved: observed,
                        overdue: live && overdue, responseText: live ? responseText : "",
                        idleFraction: Self.idleFraction(idle: timing?.idleSeconds, run: timing?.runSeconds), timingSummary: timingSummary,
                        resources: resourceState(rows.filter { model.isConnected($0) && $0.available != false }, expectedRows: rows.count),
                        manualRunning: manualRunning,
                        manualTarget: ManualRunTarget(key: name, members: Array(Set(manualMembers)).sorted()))
        }
        levels = levels.filter { order.contains($0.key) }
        observingSince = observingSince.filter { order.contains($0.key) }
        if !nodes.contains(where: { $0.key == hoveredName }) { hoveredName = nil }
        configureRings()
        let style = ThemePalette.waveStyle(waveStyle)
        let padding: CGFloat = style == "off" ? 23 : waveResources ? ceil(24 + 10 * waveHeight) : style == "bars" ? ceil(26 + 12 * waveHeight) : ["plasma", "aurora"].contains(style) ? ceil(26 + 7 * waveHeight) :
            style == "radar" ? ceil(26 + 9 * waveHeight) : max(32, 18 + 12 * waveHeight + 3)
        fullDiameter = ceil((orbitRadius + padding) * 2)
        applySize()
        let overviewStates = nodes.map(\.visualState) + (connectionState == "partial" ? [.attention] : [])
        let overall = !connected ? ThemePalette.connection(connected: connected, state: connectionState) : SessionVisualState.mostImportant(overviewStates)
        tint = ThemePalette.session(overall)
        samples.removeAll { now - $0.time > 60 }
        // The bridge redraws at 10 Hz; the graph records one real sample per second.
        if samples.last.map({ now - $0.time >= 1 }) ?? true {
            let since = samples.last?.time ?? now - 1
            let amount = outputMarks.values.flatMap { $0 }.filter { $0.time > since }.reduce(0) { $0 + $1.amount }
            samples.append(Sample(time: now, level: connected ? outputLevel(amount) : nil))
        }
        let summary = connected ? "\(runningCount) 个运行中，\(waitingCount) 个等待输入，待我 \(pendingCountText)，久等 \(overdueCount)，\(longestWaitText)，\(errorCount) 个异常" : model.message
        let descriptions = nodes.map { $0.name + "（" + $0.agent + "）：" + $0.status + ($0.summary.isEmpty ? "" : "，" + $0.summary) + ($0.running ? "，" + liveText($0) : "") + ($0.responseText.isEmpty ? "" : "，" + $0.responseText) + "，" + $0.timingSummary + (waveResources ? "，" + resourceSummary($0.resources) : "") }.joined(separator: "\n")
        let usageSummary = usage?.accessibilitySummary(connected: connected) ?? "用量暂不可用"
        overviewToolTip = summary + "\n" + usageSummary + "\n" + descriptions + "\n名称旁细弧：等待色为空闲，运行色为工作；悬停查看具体时长。\n声浪仅显示运行会话近24秒活动，顺时针从旧到新" + (waveResources ? "；资源通道在同一圈叠加。" : "；会话组共享声浪。") + "\n单击切换大小，双击打开面板，拖动移动；右键查看更多操作"
        updateToolTip()
        accessibilityOverview = summary + "，" + usageSummary + "，" + descriptions.replacingOccurrences(of: "\n", with: "，")
        setAccessibilityLabel((miniMode ? "小号悬浮球，" : "完整会话环，") + accessibilityOverview)
        needsDisplay = true
    }

    private func recordOutput(_ rows: [Pane], eligible: Set<String>, now: TimeInterval) {
        let sessions = Set(rows.map(\.orbitKey))
        let ids = Set(rows.map(\.paneId))
        let paneKeys = Dictionary(rows.map { ($0.paneId, $0.orbitKey) }, uniquingKeysWith: { first, _ in first })
        outputMarks = outputMarks.reduce(into: [:]) { result, item in
            guard sessions.contains(item.key) else { return }
            result[item.key] = item.value.filter { now - $0.time < 24 && eligible.contains($0.paneId) && paneKeys[$0.paneId] == item.key }
        }
        outputReadings = outputReadings.filter { ids.contains($0.key) }
        guard connected else { outputReadings.removeAll(); outputActivePanes.removeAll(); outputMarks.removeAll(); return }
        var readPanes = Set<String>()
        for pane in rows {
            guard readPanes.insert(pane.paneId).inserted, pane.available != false, let epoch = pane.activityEpoch, let total = pane.outputTotal else { continue }
            if let previous = outputReadings[pane.paneId], previous.epoch == epoch, total >= previous.total {
                let amount = total - previous.total
                if amount > 0 && eligible.contains(pane.paneId) && outputActivePanes.contains(pane.paneId) {
                    outputMarks[pane.orbitKey, default: []].append(OutputMark(time: now, amount: amount, paneId: pane.paneId))
                }
            } else {
                // An epoch change only clears that pane, preserving the other group members' output.
                outputMarks[pane.orbitKey]?.removeAll { $0.paneId == pane.paneId }
                if outputMarks[pane.orbitKey]?.isEmpty != false { observingSince[pane.orbitKey] = now }
            }
            outputReadings[pane.paneId] = (epoch, total)
        }
        outputActivePanes = eligible
    }

    private func resourceIdentity(_ pane: Pane) -> String? {
        guard let resources = pane.resources, resources.isFresh, let identity = resources.identity else { return nil }
        return (pane.source ?? "remote") + ":" + identity
    }

    private func toolIdentity(_ pane: Pane) -> String? {
        guard let identity = pane.usageMetrics?.rootIdentity else { return nil }
        return (pane.source ?? "remote") + ":" + identity
    }

    private func resourceState(_ rows: [Pane], expectedRows: Int) -> ResourceState {
        var result = ResourceState()
        var unique: [String: Pane] = [:]
        for pane in rows {
            guard let key = resourceIdentity(pane), let value = pane.resources else { continue }
            if let previous = unique[key]?.resources, (previous.sampleId ?? -1) > (value.sampleId ?? -1) { continue }
            unique[key] = pane
        }
        let resources = unique.values.compactMap(\.resources)
        let memory = resources.filter { $0.memoryAvailable == true && ($0.memoryBytes ?? -1) >= 0 }
        if !memory.isEmpty { result.memoryBytes = memory.reduce(0) { $0 + Double($1.memoryBytes ?? 0) } }
        let missingResources = rows.count < expectedRows || rows.contains { resourceIdentity($0) == nil }
        result.memoryPartial = missingResources || memory.count < resources.count || memory.contains { $0.memoryComplete != true }
        var hosts: [String: Double] = [:]
        for pane in unique.values {
            if let bytes = pane.resources?.hostMemoryBytes, bytes > 0 {
                let source = pane.source ?? "remote"; hosts[source] = max(hosts[source] ?? 0, Double(bytes))
            }
        }
        if !hosts.isEmpty { result.hostMemoryBytes = hosts.values.reduce(0, +) }
        func rate(_ value: Double?) -> Double? { value.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil } }
        let io = resources.filter { $0.ioAvailable == true }
        let ioRates = io.compactMap { value -> Double? in
            let directions = [rate(value.ioReadPerSecond), rate(value.ioWritePerSecond)].compactMap { $0 }
            return directions.isEmpty ? nil : directions.reduce(0, +)
        }
        if !ioRates.isEmpty { result.ioRate = ioRates.reduce(0, +) }
        result.ioPartial = missingResources || ioRates.count < resources.count || io.contains { $0.ioComplete != true || rate($0.ioReadPerSecond) == nil || rate($0.ioWritePerSecond) == nil }
        let network = resources.filter { $0.networkAvailable == true }
        let networkRates = network.compactMap { value -> Double? in
            let directions = [rate(value.networkRxPerSecond), rate(value.networkTxPerSecond)].compactMap { $0 }
            return directions.isEmpty ? nil : directions.reduce(0, +)
        }
        result.networkShared = network.contains { $0.networkShared == true }
        if !networkRates.isEmpty { result.networkRate = result.networkShared ? networkRates.max() : networkRates.reduce(0, +) }
        result.networkPartial = missingResources || networkRates.count < resources.count || network.contains { $0.networkComplete != true || rate($0.networkRxPerSecond) == nil || rate($0.networkTxPerSecond) == nil }
        result.processShared = resources.contains { $0.scope == "shared_process" }
        var tools: [String: UsageMetrics] = [:]
        for pane in rows {
            guard let key = toolIdentity(pane), let metrics = pane.usageMetrics, (metrics.toolsCovered ?? 0) > 0 else { continue }
            if let old = tools[key], (old.toolCompletions ?? -1) > (metrics.toolCompletions ?? -1) { continue }
            tools[key] = metrics
        }
        result.toolsKnown = tools.values.contains { ($0.liveToolsCovered ?? 0) > 0 }
        let expectedTools = Set(rows.map { toolIdentity($0) ?? ("unknown:" + ($0.source ?? "remote") + ":" + $0.monitorKey) }).count
        result.toolsPartial = rows.count < expectedRows || tools.count < expectedTools || tools.values.contains { ($0.liveToolsCovered ?? 0) == 0 }
        let active = tools.values.compactMap(\.activeTools).filter { $0 >= 0 }
        if active.count == tools.count && !active.isEmpty && !result.toolsPartial {
            result.activeTools = active.reduce(0) { sum, value in
                let (total, overflow) = sum.addingReportingOverflow(value); return overflow ? Int.max : total
            }
        }
        return result
    }

    private func resourceSummary(_ value: ResourceState) -> String {
        let memory = value.memoryBytes.map { "内存 " + byteAmount($0) + (value.memoryPartial ? "（部分）" : "") + (value.processShared ? "（共享进程）" : "") } ?? "内存未观测"
        let io = value.ioRate.map { "进程I/O " + byteAmount($0) + "/s" + (value.ioPartial ? "（部分）" : "") } ?? "进程I/O 未观测"
        let network = value.networkRate.map { "网络 " + (value.networkShared ? "共享 " : "") + (value.networkShared || value.networkPartial ? "≥" : "") + byteAmount($0) + "/s" + (value.networkPartial ? "（部分）" : "") } ?? "网络未观测"
        let tools = value.activeTools.map { "未返回工具 \($0)" } ?? (value.toolsKnown ? value.toolsPartial ? "工具部分观测" : "工具已观测" : "工具未观测")
        return [memory, io, tools, network].joined(separator: " · ")
    }

    private func byteAmount(_ value: Double?) -> String {
        guard let value = value, value.isFinite, value >= 0 else { return "—" }
        if value >= 1_073_741_824 { return String(format: "%.1f GiB", value / 1_073_741_824) }
        if value >= 1_048_576 { return String(format: "%.0f MiB", value / 1_048_576) }
        if value >= 1024 { return String(format: "%.1f KiB", value / 1024) }
        return String(format: "%.0f B", value)
    }

    private func recordResources(_ rows: [Pane], eligible: Set<String>, now: TimeInterval) {
        let resourceGroups = Dictionary(grouping: rows.filter { resourceIdentity($0) != nil }, by: { resourceIdentity($0)! })
        let toolGroups = Dictionary(grouping: rows.filter { toolIdentity($0) != nil }, by: { toolIdentity($0)! })
        let workingKeys = Set(rows.filter { eligible.contains($0.paneId) }.map(\.orbitKey))
        var validTargets: [String: Set<String>] = [:]
        for (identity, panes) in resourceGroups {
            validTargets["resource:" + identity] = Set(panes.filter { eligible.contains($0.paneId) }.map(\.orbitKey))
        }
        for (identity, panes) in toolGroups {
            validTargets["tool:" + identity] = Set(panes.filter { eligible.contains($0.paneId) }.map(\.orbitKey))
        }
        resourceMarks = resourceMarks.filter { workingKeys.contains($0.key) }.mapValues { channels in
            channels.mapValues { marks in
                marks.filter { now - $0.time >= 0 && now - $0.time < 24 }
            }
        }
        for key in Array(resourceMarks.keys) {
            resourceMarks[key] = resourceMarks[key]?.mapValues { marks in
                marks.filter { validTargets[$0.identity]?.contains(key) == true }
            }
        }
        guard connected else {
            resourceReadings.removeAll(); toolReadings.removeAll()
            resourceTargets.removeAll(); toolTargets.removeAll(); resourceMarks.removeAll()
            return
        }
        resourceReadings = resourceReadings.filter { resourceGroups[$0.key] != nil }
        resourceTargets = resourceTargets.filter { resourceGroups[$0.key] != nil }
        toolReadings = toolReadings.filter { toolGroups[$0.key] != nil }
        toolTargets = toolTargets.filter { toolGroups[$0.key] != nil }
        func delta(_ current: Int?, _ previous: Int?) -> Double? {
            guard let current = current, let previous = previous, previous >= 0, current >= previous else { return nil }
            return Double(current - previous)
        }
        func clear(_ identity: String) {
            for key in Array(resourceMarks.keys) {
                resourceMarks[key] = resourceMarks[key]?.mapValues { $0.filter { $0.identity != identity } }
            }
        }
        func append(_ key: String, _ role: ResourceRole, _ amount: Double, _ identity: String) {
            guard amount.isFinite, amount > 0 else { return }
            resourceMarks[key, default: [:]][role, default: []].append(ResourceMark(time: now, amount: amount, identity: identity))
        }
        var networkDeltas: [String: [(amount: Double, identity: String)]] = [:]
        var sharedNetworks = Set<String>()
        for (identity, panes) in resourceGroups {
            let markIdentity = "resource:" + identity
            let targets = validTargets[markIdentity] ?? []
            let priorTargets = (resourceTargets[identity] ?? []).intersection(targets)
            resourceTargets[identity] = priorTargets
            guard let value = panes.compactMap(\.resources).max(by: { ($0.sampleId ?? -1) < ($1.sampleId ?? -1) }),
                  let sample = value.sampleId else { clear(markIdentity); continue }
            let old = resourceReadings[identity]
            guard old?.sample != sample || old?.epoch != value.epoch else { continue }
            let reset = old.map { previous in
                previous.epoch != value.epoch || sample < (previous.sample ?? -1) ||
                zip([value.ioReadBytes, value.ioWriteBytes, value.networkRxBytes, value.networkTxBytes],
                    [previous.read, previous.write, previous.received, previous.sent]).contains { current, prior in
                        if let current = current, let prior = prior { return current < prior }
                        return false
                    }
            } ?? false
            if reset { clear(markIdentity) }
            if let old = old, !reset, old.epoch == value.epoch, sample > (old.sample ?? -1) {
                for key in priorTargets {
                    let io = [delta(value.ioReadBytes, old.read), delta(value.ioWriteBytes, old.write)].compactMap { $0 }
                    if value.ioAvailable == true, !io.isEmpty {
                        append(key, .output, io.reduce(0, +), markIdentity)
                    }
                    let network = [delta(value.networkRxBytes, old.received), delta(value.networkTxBytes, old.sent)].compactMap { $0 }
                    if value.networkAvailable == true, !network.isEmpty {
                        networkDeltas[key, default: []].append((network.reduce(0, +), markIdentity))
                        if value.networkShared == true { sharedNetworks.insert(key) }
                    }
                }
            }
            resourceReadings[identity] = ResourceReading(epoch: value.epoch, sample: sample,
                read: value.ioAvailable == true ? value.ioReadBytes : nil,
                write: value.ioAvailable == true ? value.ioWriteBytes : nil,
                received: value.networkAvailable == true ? value.networkRxBytes : nil,
                sent: value.networkAvailable == true ? value.networkTxBytes : nil)
            // A newly running target becomes ready only after a real resource
            // sample; a cached idle sample cannot arm the next delta.
            resourceTargets[identity] = targets
        }
        for (key, amounts) in networkDeltas {
            let values = sharedNetworks.contains(key) ? amounts.max(by: { $0.amount < $1.amount }).map { [$0] } ?? [] : amounts
            for value in values { append(key, .network, value.amount, value.identity) }
        }
        for (identity, panes) in toolGroups {
            let markIdentity = "tool:" + identity
            let targets = validTargets[markIdentity] ?? []
            let priorTargets = (toolTargets[identity] ?? []).intersection(targets)
            guard let value = panes.compactMap(\.usageMetrics).filter({ ($0.toolsCovered ?? 0) > 0 && ($0.liveToolsCovered ?? 0) > 0 }).max(by: { ($0.toolCompletions ?? -1) < ($1.toolCompletions ?? -1) }),
                  let total = value.toolCompletions, total >= 0 else {
                toolReadings.removeValue(forKey: identity); toolTargets.removeValue(forKey: identity)
                clear(markIdentity); continue
            }
            if let previous = toolReadings[identity] {
                if previous.epoch != value.toolsEpoch || total < previous.total { clear(markIdentity) }
                if let epoch = value.toolsEpoch, previous.epoch == epoch, let amount = delta(total, previous.total) {
                    for key in priorTargets { append(key, .tools, amount, markIdentity) }
                }
            }
            toolReadings[identity] = ToolReading(epoch: value.toolsEpoch, total: total)
            toolTargets[identity] = targets
        }
    }

    private func outputLevel(_ amount: Int) -> CGFloat {
        outputLevel(Double(amount))
    }

    private func outputLevel(_ amount: Double) -> CGFloat {
        CGFloat(min(1, log1p(max(0, amount) * Double(waveSensitivity)) / log1p(5000)))
    }

    private func liveText(_ node: Node) -> String {
        guard node.running else { return node.status }
        guard let age = node.outputAge else { return "暂无新输出 · " + duration(node.observedFor) }
        if age < 1.5 {
            return node.source == "tokens" ? "生成量刚增加" : node.source == "log" ? "日志刚更新" : node.source == "background" ? "后台刚有输出" : node.source == "workflow" ? "工作流有进展" : "刚有新输出"
        }
        return "静默 " + duration(age) + " · 运行中"
    }

    private func duration(_ seconds: TimeInterval) -> String {
        guard seconds.isFinite else { return "—" }
        let value = Int(min(9_000_000_000_000_000, max(0, seconds)))
        return value < 60 ? "\(value)s" : "\(value / 60)m\(value % 60)s"
    }

    private func compactDuration(_ seconds: TimeInterval) -> String {
        guard seconds.isFinite, seconds >= 0 else { return "—" }
        let value = Int(min(86400 * 999, seconds))
        if seconds >= 86400 * 999 { return "999d+" }
        if value >= 86400 { return "\(value / 86400)d" }
        if value >= 3600 { return "\(value / 3600)h\(value % 3600 / 60)m" }
        return value >= 60 ? "\(value / 60)m" : "\(value)s"
    }

    static func idleFraction(idle: Double?, run: Double?) -> CGFloat? {
        guard let idle = idle, let run = run, idle.isFinite, run.isFinite, idle >= 0, run >= 0 else { return nil }
        let scale = max(idle, run)
        guard scale > 0 else { return nil }
        return CGFloat((idle / scale) / (idle / scale + run / scale))
    }

    static func resourceBaselineOffset(inner: Bool) -> CGFloat { inner ? 14 : 16 }

    private func exactDuration(_ seconds: TimeInterval?) -> String {
        guard let seconds = seconds, seconds.isFinite, seconds >= 0 else { return "—" }
        guard seconds < Double(Int.max / 2) else { return "超长时长" }
        let total = Int(seconds), hours = total / 3600, minutes = total / 60 % 60, rest = total % 60
        if hours > 0 { return "\(hours)小时\(minutes)分\(rest)秒" }
        if minutes > 0 { return "\(minutes)分\(rest)秒" }
        return "\(rest)秒"
    }

    private func updateToolTip() {
        guard let node = nodes.first(where: { $0.key == hoveredName }) else {
            toolTip = overviewToolTip
            return
        }
        toolTip = node.name + "（" + node.agent + "）· " + node.status + "\n" + node.timingSummary +
            "\n细弧：等待色为空闲，运行色为工作" +
            (node.responseText.isEmpty ? "" : "\n" + node.responseText) +
            (node.summary.isEmpty ? "" : "\n" + node.summary) +
            (node.manualRunning ? "\n手动运行标记有效；收到新的回复结果后自动解除，可右键取消。错误或离线优先显示。" : "") +
            (waveResources ? "\n" + resourceSummary(node.resources) : "")
    }

    private func isWorkText(_ text: String) -> Bool {
        !text.isEmpty && text.range(of: "^\\+\\s*(?:Show details|\\d+ lines?)|^.*\\(\\^t to expand\\)$|^[─━]+$|^(?:Claude(?: Code)?|Codex)$|^(?:⎿\\s*)?Tip: Use /btw", options: [.regularExpression, .caseInsensitive]) == nil
    }

    private func clean(_ value: String) -> String {
        String(value.replacingOccurrences(of: "^[\\s│┃└├─●•✻✽✶✳✢·]+", with: "", options: .regularExpression)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).prefix(180))
    }

    private func label(_ name: String) -> String {
        name.count > 20 ? String(name.prefix(11)) + "…" + String(name.suffix(7)) : name
    }

    private func configureRings() {
        func requiredRadius(_ indices: [Int], _ size: CGFloat) -> CGFloat {
            let font = NSFont.monospacedSystemFont(ofSize: size, weight: .medium)
            let perRadian = CGFloat(indices.count) / (.pi * 2)
            return indices.map { index -> CGFloat in
                let name = label(nodes[index].name)
                let titleWidth = (name as NSString).size(withAttributes: [.font: font]).width + CGFloat(name.count) * 0.25
                return (titleWidth + 12) * perRadian
            }.max() ?? 0
        }
        if nodes.count <= 6 {
            let indices = Array(nodes.indices)
            orbitRadius = max(76, requiredRadius(indices, 9.4))
            rings = [Ring(indices: indices, radius: orbitRadius)]
        } else {
            var outerCount = Int(ceil(Double(nodes.count) * 0.6))
            func radiusForSplit(_ split: Int) -> CGFloat {
                max(76 + ringSeparation, requiredRadius(Array(0..<split), 9.4),
                    max(76, requiredRadius(Array(split..<nodes.count), 9.2)) + ringSeparation)
            }
            orbitRadius = radiusForSplit(outerCount)
            // Fit long names by balancing the two circumferences, not by shrinking type.
            for split in max(4, (nodes.count + 1) / 2)...(nodes.count - 2) {
                let candidate = radiusForSplit(split)
                if candidate < orbitRadius - 0.5 { outerCount = split; orbitRadius = candidate }
            }
            let outer = Array(0..<outerCount)
            let inner = Array(outerCount..<nodes.count)
            rings = [Ring(indices: outer, radius: orbitRadius),
                     Ring(indices: inner, radius: orbitRadius - ringSeparation, phase: .pi / CGFloat(inner.count), fontSize: 9.2)]
        }
    }

    private func point(_ center: NSPoint, _ radius: CGFloat, _ angle: CGFloat) -> NSPoint {
        NSPoint(x: center.x + cos(angle) * radius, y: center.y + sin(angle) * radius)
    }

    private func angle(_ index: Int, _ ring: Ring) -> CGFloat {
        .pi / 2 - CGFloat(index) * .pi * 2 / CGFloat(max(1, ring.indices.count)) + ring.phase
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let center = NSPoint(x: bounds.midX, y: bounds.midY)
        if miniMode { drawMini(center); return }
        let bodyRadius = orbitRadius + 14
        let body = NSBezierPath(ovalIn: NSRect(x: center.x - bodyRadius, y: center.y - bodyRadius, width: bodyRadius * 2, height: bodyRadius * 2))
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow(); shadow.shadowColor = tint.withAlphaComponent(0.16)
        shadow.shadowBlurRadius = 8; shadow.shadowOffset = .zero; shadow.set()
        ThemePalette.ns(.base).withAlphaComponent(0.98).setFill(); body.fill()
        NSGraphicsContext.restoreGraphicsState()
        NSGradient(starting: ThemePalette.ns(.surface), ending: ThemePalette.ns(.base))?.draw(in: body, angle: -65)
        NSGraphicsContext.saveGraphicsState(); body.addClip()
        let grid = NSBezierPath()
        for x in stride(from: CGFloat(0), through: bounds.width, by: 16) {
            grid.move(to: NSPoint(x: x, y: 0)); grid.line(to: NSPoint(x: x, y: bounds.height))
            grid.move(to: NSPoint(x: 0, y: x)); grid.line(to: NSPoint(x: bounds.width, y: x))
        }
        ThemePalette.ns(.accent).withAlphaComponent(0.035).setStroke(); grid.lineWidth = 0.4; grid.stroke()
        NSGraphicsContext.restoreGraphicsState()
        tint.withAlphaComponent(0.16).setStroke(); body.lineWidth = 0.6; body.stroke()
        let reducedMotion = reduceMotion || NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        let time = reducedMotion ? 0 : CGFloat(ProcessInfo.processInfo.systemUptime)
        for (ringIndex, ring) in rings.enumerated() {
            drawWaves(center, ring: ring, inner: ringIndex > 0, now: ProcessInfo.processInfo.systemUptime, reducedMotion: reducedMotion)
            let radius = ring.radius - 20
            let orbit = NSBezierPath(ovalIn: NSRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2))
            ThemePalette.ns(.accent).withAlphaComponent(0.13).setStroke(); orbit.lineWidth = 0.6; orbit.stroke()
            for (index, nodeIndex) in ring.indices.enumerated() {
                let node = nodes[nodeIndex]
                let theta = angle(index, ring)
                let span = CGFloat.pi * 2 / CGFloat(ring.indices.count)
                let arc = NSBezierPath()
                arc.appendArc(withCenter: center, radius: radius, startAngle: (theta - span * 0.37) * 180 / .pi,
                              endAngle: (theta + span * 0.37) * 180 / .pi, clockwise: false)
                let hovered = hoveredName == node.key
                node.color.withAlphaComponent(hovered ? 0.95 : node.running ? 0.6 : 0.3).setStroke()
                arc.lineWidth = hovered ? 2 : node.running ? 1.6 : 1; arc.lineCapStyle = .round; arc.stroke()
                let nameColor = node.color
                drawArcText(label(node.name), center: center, radius: ring.radius, angle: theta, size: ring.fontSize, color: nameColor)
                drawTimingArc(node, center: center, radius: ring.radius - 11, theta: theta, span: span)
                if node.running {
                    let position = point(center, radius, theta)
                    let age = outputMarks[node.key]?.last.map { ProcessInfo.processInfo.systemUptime - $0.time }
                    node.color.withAlphaComponent(age.map { $0 < 1.5 } == true ? 1 : 0.35).setFill()
                    NSBezierPath(ovalIn: NSRect(x: position.x - 1.5, y: position.y - 1.5, width: 3, height: 3)).fill()
                }
            }
        }
        drawHub(center, time: time, reducedMotion: reducedMotion)
    }

    private func drawTimingArc(_ node: Node, center: NSPoint, radius: CGFloat, theta: CGFloat, span: CGFloat) {
        func arc(_ from: CGFloat, _ to: CGFloat) -> NSBezierPath {
            let path = NSBezierPath()
            path.appendArc(withCenter: center, radius: radius,
                           startAngle: (theta + span * from) * 180 / .pi,
                           endAngle: (theta + span * to) * 180 / .pi)
            path.lineWidth = 2; path.lineCapStyle = .butt
            return path
        }
        ThemePalette.ns(.muted).withAlphaComponent(0.2).setStroke()
        arc(-0.32, 0.32).stroke()
        guard let fraction = node.idleFraction else { return }
        let split = -0.32 + 0.64 * fraction
        if fraction > 0 {
            ThemePalette.ns(.waiting).withAlphaComponent(node.live ? 0.78 : 0.3).setStroke()
            arc(-0.32, split).stroke()
        }
        if fraction < 1 {
            ThemePalette.ns(.running).withAlphaComponent(node.live ? 0.78 : 0.3).setStroke()
            arc(split, 0.32).stroke()
        }
    }

    private func drawMini(_ center: NSPoint) {
        let body = NSBezierPath(ovalIn: NSRect(x: center.x - 29, y: center.y - 29, width: 58, height: 58))
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = tint.withAlphaComponent(0.16)
        shadow.shadowBlurRadius = 4
        shadow.shadowOffset = .zero
        shadow.set()
        ThemePalette.ns(.base).withAlphaComponent(0.98).setFill(); body.fill()
        NSGraphicsContext.restoreGraphicsState()
        NSGradient(starting: ThemePalette.ns(.surface), ending: ThemePalette.ns(.base))?.draw(in: body, angle: -65)
        let track = NSBezierPath(ovalIn: NSRect(x: center.x - 31, y: center.y - 31, width: 62, height: 62))
        ThemePalette.ns(.muted).withAlphaComponent(0.15).setStroke()
        track.lineWidth = 2.2; track.stroke()
        let span = CGFloat.pi * 2 / CGFloat(max(1, nodes.count))
        for (index, node) in nodes.enumerated() {
            let theta = CGFloat.pi / 2 - CGFloat(index) * span
            let half = span * 0.38
            let arc = NSBezierPath()
            arc.appendArc(withCenter: center, radius: 31, startAngle: (theta - half) * 180 / .pi,
                          endAngle: (theta + half) * 180 / .pi)
            node.color.withAlphaComponent(node.live ? 0.95 : 0.45).setStroke()
            arc.lineWidth = 2.2; arc.lineCapStyle = .round; arc.stroke()
        }
        func rect(_ y: CGFloat, _ height: CGFloat) -> NSRect {
            NSRect(x: center.x - 27, y: center.y + y, width: 54, height: height)
        }
        let primary = connected ? "待我 \(pendingCountText)" : "离线"
        let primaryColor: NSColor = connected ? ThemePalette.ns(pendingCount > 0 ? .waiting : .text) : ThemePalette.ns(.offline)
        drawText(primary, rect: rect(4, 17), size: pendingCount >= 100 ? 10.5 : 11.8, color: primaryColor)
        drawText(connected ? longestWaitText : connectionNotice, rect: rect(-7, 12), size: 8.3,
                 color: ThemePalette.ns(connected && overdueCount > 0 ? .attention : .muted))
        drawText(connected ? "等 \(waitingCount) · 久 \(overdueCount)" : "等 — · 久 —", rect: rect(-19, 11), size: 7.9,
                 color: ThemePalette.ns(connected && overdueCount > 0 ? .attention : .muted))
        // This compact overview stays still, including when Reduce Motion is on.
    }

    private func drawWaves(_ center: NSPoint, ring: Ring, inner: Bool, now: TimeInterval, reducedMotion: Bool) {
        guard waveStyle != "off" else { return }
        let style = ThemePalette.waveStyle(waveStyle)
        if waveResources {
            drawResourceWaves(center, ring: ring, inner: inner, now: now, reducedMotion: reducedMotion)
            return
        }
        if style == "bars" {
            drawSpectrumBars(center, ring: ring, inner: inner, now: now, reducedMotion: reducedMotion)
            return
        }
        if ["plasma", "aurora", "radar"].contains(style) {
            drawLayeredWaves(center, ring: ring, inner: inner, now: now, reducedMotion: reducedMotion, style: style)
            return
        }
        let radius = ring.radius + (inner ? 16 : 18)
        let span = CGFloat.pi * 2 / CGFloat(max(1, ring.indices.count))
        for (slot, nodeIndex) in ring.indices.enumerated() {
            let node = nodes[nodeIndex]
            guard node.running else { continue }
            let theta = angle(slot, ring)
            let baseBars = max(12, min(48, Int(span * radius * 0.8 / 3.2)))
            let bars = max(8, min(72, Int((CGFloat(baseBars) * waveDensity).rounded())))
            let bucketDuration = 24 / Double(bars)
            let marks = outputMarks[node.key] ?? []
            let anchor = now
            let end = floor(anchor / bucketDuration) * bucketDuration + bucketDuration
            var points: [(tip: NSPoint, base: NSPoint, color: NSColor, alpha: CGFloat, measured: Bool)] = []
            for index in 0..<bars {
                let fraction = CGFloat(index) / CGFloat(max(1, bars - 1))
                let barAngle = theta + span * (0.4 - fraction * 0.8)
                let start = end - Double(bars - index) * bucketDuration
                let amount = marks.filter { $0.time >= start && $0.time < start + bucketDuration }.reduce(0) { $0 + $1.amount }
                let measured = node.live && node.running && amount > 0
                // Density changes resolution; the intensity remains output per
                // second, so adding more marks does not make a busy task quieter.
                let energy = outputLevel(Double(amount) / bucketDuration)
                let height: CGFloat = measured ? ((inner ? 2.5 : 4) + energy * (inner ? 2.5 : 8)) * waveHeight : 0.6
                let color = waveColor(node, fraction: fraction)
                let alpha: CGFloat = measured ? 0.35 + fraction * 0.6 : node.running ? 0.18 : 0.1
                let base = point(center, radius, barAngle)
                let tip = point(center, radius + height, barAngle)
                points.append((tip, base, color, alpha, measured))
                if style == "dots" {
                    let dotRadius: CGFloat = measured ? 0.75 + energy * 0.8 : 0.4
                    color.withAlphaComponent(alpha).setFill()
                    NSBezierPath(ovalIn: NSRect(x: tip.x - dotRadius, y: tip.y - dotRadius, width: dotRadius * 2, height: dotRadius * 2)).fill()
                } else if style != "curve" {
                    let path = NSBezierPath()
                    path.move(to: base); path.line(to: tip)
                    color.withAlphaComponent(alpha).setStroke()
                    path.lineWidth = measured ? ThemePalette.waveLineWidth(waveStyle) : 0.7
                    path.lineCapStyle = .round; path.stroke()
                }
            }
            if style == "curve", points.count > 1 {
                let maximumRadius = radius + (inner ? 5 : 12) * waveHeight
                func control(_ value: NSPoint) -> NSPoint {
                    let dx = value.x - center.x, dy = value.y - center.y
                    let distance = hypot(dx, dy)
                    guard distance > maximumRadius else { return value }
                    return NSPoint(x: center.x + dx * maximumRadius / distance, y: center.y + dy * maximumRadius / distance)
                }
                for index in 0..<(points.count - 1) {
                    let left = points[index], right = points[index + 1]
                    let p0 = points[max(0, index - 1)].tip
                    let p3 = points[min(points.count - 1, index + 2)].tip
                    let c1 = control(NSPoint(x: left.tip.x + (right.tip.x - p0.x) / 6, y: left.tip.y + (right.tip.y - p0.y) / 6))
                    let c2 = control(NSPoint(x: right.tip.x - (p3.x - left.tip.x) / 6, y: right.tip.y - (p3.y - left.tip.y) / 6))
                    let path = NSBezierPath(); path.move(to: left.tip)
                    path.curve(to: right.tip, controlPoint1: c1, controlPoint2: c2)
                    if left.measured || right.measured {
                        let ribbon = path.copy() as! NSBezierPath
                        ribbon.line(to: right.base); ribbon.line(to: left.base); ribbon.close()
                        right.color.withAlphaComponent(0.09).setFill(); ribbon.fill()
                    }
                    right.color.withAlphaComponent(max(left.alpha, right.alpha)).setStroke()
                    path.lineWidth = left.measured || right.measured ? ThemePalette.waveLineWidth(waveStyle) : 0.7
                    path.lineCapStyle = .round; path.stroke()
                }
            }
            // The head flashes only after an observed output event, never on a clock loop.
            if !reducedMotion && node.live && node.running, let mark = marks.last, now - mark.time < 1.5 {
                let age = max(0, now - mark.time)
                let head = point(center, radius, theta - span * 0.4)
                waveColor(node, fraction: 1).withAlphaComponent(CGFloat(1 - age / 2)).setFill()
                NSBezierPath(ovalIn: NSRect(x: head.x - 2, y: head.y - 2, width: 4, height: 4)).fill()
            }
        }
    }

    private func resourceEnergy(_ amount: Double, reference: Double) -> CGFloat {
        guard amount.isFinite, amount > 0 else { return 0 }
        return CGFloat(min(1, log1p(amount * Double(waveSensitivity)) / log1p(reference)))
    }

    private func drawResourceWaves(_ center: NSPoint, ring: Ring, inner: Bool, now: TimeInterval,
                                   reducedMotion: Bool) {
        let span = CGFloat.pi * 2 / CGFloat(max(1, ring.indices.count))
        let radius = ring.radius + Self.resourceBaselineOffset(inner: inner)
        let count = max(8, min(inner ? 30 : 56, Int((inner ? 19 : 30) * waveDensity)))
        let bucket = 24 / Double(count)
        let end = floor(now / bucket) * bucket + bucket
        func arc(_ theta: CGFloat, _ from: CGFloat, _ to: CGFloat) -> NSBezierPath {
            let path = NSBezierPath()
            path.appendArc(withCenter: center, radius: radius,
                           startAngle: (theta + span * from) * 180 / .pi,
                           endAngle: (theta + span * to) * 180 / .pi)
            return path
        }
        for (slot, nodeIndex) in ring.indices.enumerated() {
            let node = nodes[nodeIndex]
            guard node.running else { continue }
            let theta = angle(slot, ring), state = node.resources
            ThemePalette.ns(.muted).withAlphaComponent(0.14).setStroke()
            let rail = arc(theta, -0.4, 0.4); rail.lineWidth = 0.5; rail.stroke()
            if let bytes = state.memoryBytes, bytes > 0 {
                let memory = ThemePalette.resourceColor(.memory)
                if let host = state.hostMemoryBytes, host > 0 {
                    let extent = CGFloat(min(1, max(0.012, bytes / host)))
                    memory.withAlphaComponent(0.52).setStroke()
                    let occupied = arc(theta, -0.4, -0.4 + 0.8 * extent)
                    occupied.lineWidth = 1; occupied.stroke()
                } else {
                    memory.withAlphaComponent(0.42).setStroke()
                    for position in [CGFloat(-0.25), 0, 0.25] {
                        let tick = arc(theta, position - 0.025, position + 0.025)
                        tick.lineWidth = 1; tick.stroke()
                    }
                }
            }
            if state.networkRate == nil || state.networkPartial {
                drawArcText("?", center: center, radius: radius + 1, angle: theta - span * 0.44,
                            size: inner ? 6.4 : 7.2, color: ThemePalette.ns(.muted))
            }
            if state.toolsKnown, let active = state.activeTools, active > 0 {
                ThemePalette.resourceColor(.tools).withAlphaComponent(0.55).setStroke()
                let extent = min(0.25, 0.05 + CGFloat(log1p(Double(active))) * 0.05)
                let activeArc = arc(theta, -extent, extent); activeArc.lineWidth = 1.3; activeArc.stroke()
            }
            // All channels start on the same circle and at the same time bucket.
            // Wide translucent output sits behind narrower tool/network strokes.
            for role in [ResourceRole.output, .tools, .network] {
                guard role == .output || role == .tools && state.toolsKnown ||
                        role == .network && state.networkRate != nil else { continue }
                let marks = (resourceMarks[node.key]?[role] ?? []).filter { now - $0.time >= 0 && now - $0.time < 24 }
                let outputs = role == .output ? (outputMarks[node.key] ?? []).filter { now - $0.time >= 0 && now - $0.time < 24 } : []
                for index in 0..<count {
                    let fraction = CGFloat(index) / CGFloat(max(1, count - 1))
                    let theta = theta + span * (0.4 - 0.8 * fraction)
                    let start = end - Double(count - index) * bucket
                    let events = marks.filter { $0.time >= start && $0.time < start + bucket }
                    let activity = outputs.filter { $0.time >= start && $0.time < start + bucket }
                    let amount = events.reduce(0) { $0 + $1.amount }
                    let output = activity.reduce(0) { $0 + Double($1.amount) }
                    guard amount > 0 || output > 0 else { continue }
                    let energy: CGFloat
                    switch role {
                    case .output: energy = max(resourceEnergy(output / bucket, reference: 2000), resourceEnergy(amount / bucket, reference: 65536))
                    case .tools: energy = resourceEnergy(amount / bucket, reference: 3)
                    default: energy = resourceEnergy(amount / bucket, reference: 32768)
                    }
                    let floor: CGFloat = inner ? 1.7 : role == .output ? 3.5 : role == .tools ? 4.2 : 4.8
                    let ceiling: CGFloat = inner ? 4.2 : role == .output ? 10 : role == .tools ? 9 : 8
                    let height = (floor + (ceiling - floor) * energy) * waveHeight
                    let newest = max(events.last?.time ?? 0, activity.last?.time ?? 0)
                    let flash = reducedMotion ? CGFloat(0) : CGFloat(max(0, 1 - max(0, now - newest) / 1.5))
                    let tone = ThemePalette.resourceColor(role, fraction: fraction)
                    let width: CGFloat = (role == .output ? 3.2 : role == .tools ? 1.8 : 0.85) * (inner ? 0.65 : 1)
                    let opacity: CGFloat = role == .output ? 0.28 : role == .tools ? 0.66 : 0.95
                    let segments = NSBezierPath()
                    let pitch: CGFloat = inner ? 1.6 : 2.6
                    var offset: CGFloat = 0
                    while offset < height {
                        segments.move(to: point(center, radius + offset, theta))
                        segments.line(to: point(center, radius + min(height, offset + pitch * 0.75), theta))
                        offset += pitch
                    }
                    tone.withAlphaComponent(min(1, opacity + flash * 0.08)).setStroke()
                    segments.lineWidth = width; segments.stroke()
                    let peak = NSBezierPath()
                    peak.move(to: point(center, radius + height, theta - 0.003))
                    peak.line(to: point(center, radius + height, theta + 0.003))
                    tone.withAlphaComponent(min(1, opacity + 0.15)).setStroke()
                    peak.lineWidth = 0.9; peak.stroke()
                }
            }
        }
    }

    private func drawSpectrumBars(_ center: NSPoint, ring: Ring, inner: Bool, now: TimeInterval,
                                  reducedMotion: Bool) {
        let radius = ring.radius + (inner ? 16 : 18)
        let span = CGFloat.pi * 2 / CGFloat(max(1, ring.indices.count))
        let scale: CGFloat = inner ? 0.42 : 1
        for (slot, nodeIndex) in ring.indices.enumerated() {
            let node = nodes[nodeIndex]
            guard node.running else { continue }
            let theta = angle(slot, ring)
            let baseCount = max(12, min(48, Int(span * radius * 0.8 / 3.2)))
            let count = max(8, min(72, Int((CGFloat(baseCount) * waveDensity).rounded())))
            let spacing = radius * span * 0.8 / CGFloat(max(1, count - 1))
            let width = min(inner ? 1.55 : 2.55, max(0.65, spacing * 0.52))
            let bucket = 24 / Double(count)
            let marks = outputMarks[node.key] ?? []
            let anchor = now
            let end = floor(anchor / bucket) * bucket + bucket
            // History expires on real time even when motion is reduced.
            let visualTime = now
            let rail = NSBezierPath()
            rail.appendArc(withCenter: center, radius: radius - 1.5,
                           startAngle: (theta - span * 0.41) * 180 / .pi,
                           endAngle: (theta + span * 0.41) * 180 / .pi)
            waveColor(node, fraction: 0).withAlphaComponent(node.live ? 0.12 : 0.055).setStroke()
            rail.lineWidth = 0.55; rail.stroke()
            var previousPeak: NSPoint?
            for index in 0..<count {
                let fraction = CGFloat(index) / CGFloat(max(1, count - 1))
                let barAngle = theta + span * (0.4 - fraction * 0.8)
                let start = end - Double(count - index) * bucket
                let events = marks.filter { $0.time >= start && $0.time < start + bucket }
                let amount = events.reduce(0) { $0 + $1.amount }
                let color = waveColor(node, fraction: fraction)
                let base = point(center, radius, barAngle)
                guard node.live && node.running && amount > 0 else {
                    color.withAlphaComponent(node.live ? 0.13 : 0.065).setFill()
                    NSBezierPath(ovalIn: NSRect(x: base.x - 0.4, y: base.y - 0.4, width: 0.8, height: 0.8)).fill()
                    previousPeak = nil
                    continue
                }
                let energy = outputLevel(Double(amount) / bucket)
                let height = (inner ? 1.7 + energy * 3.3 : 2.2 + energy * 9.8) * waveHeight
                let age = max(0, visualTime - (events.last?.time ?? visualTime))
                let flash = CGFloat(max(0, 1 - age / 1.8))
                let opacity: CGFloat = 0.35 + fraction * 0.46
                let bright = color.blended(withFraction: 0.48, of: ThemePalette.ns(.text)) ?? color
                let trail = NSBezierPath()
                trail.move(to: base)
                trail.line(to: point(center, radius + height + flash * 3 * scale, barAngle))
                color.withAlphaComponent(0.055 + flash * 0.085).setStroke()
                trail.lineWidth = min(spacing * 0.8, width + 1.8 * scale)
                trail.lineCapStyle = .round; trail.stroke()

                // Batch all segments of one column into a path. Gaps keep
                // high-density rings legible without a broad continuous glow.
                let segments = NSBezierPath()
                let pitch: CGFloat = inner ? 1.95 : 2.8
                var offset: CGFloat = 0
                while offset < height {
                    let stop = min(height, offset + pitch * 0.64)
                    segments.move(to: point(center, radius + offset, barAngle))
                    segments.line(to: point(center, radius + stop, barAngle))
                    offset += pitch
                }
                segments.lineCapStyle = .butt
                color.withAlphaComponent(opacity).setStroke(); segments.lineWidth = width; segments.stroke()
                bright.withAlphaComponent(0.36 + flash * 0.32).setStroke()
                segments.lineWidth = min(0.6, width * 0.38); segments.stroke()

                let tip = point(center, radius + height, barAngle)
                let peak = point(center, radius + height + (0.8 + flash * 2.2) * scale, barAngle)
                let tangent = NSPoint(x: -sin(barAngle), y: cos(barAngle))
                let halfCap = min(spacing * 0.34, width * 0.72 + 0.25)
                let cap = NSBezierPath()
                cap.move(to: NSPoint(x: peak.x - tangent.x * halfCap, y: peak.y - tangent.y * halfCap))
                cap.line(to: NSPoint(x: peak.x + tangent.x * halfCap, y: peak.y + tangent.y * halfCap))
                color.withAlphaComponent(0.13 + flash * 0.16).setStroke()
                cap.lineWidth = 2 * scale + 0.4; cap.lineCapStyle = .round; cap.stroke()
                bright.withAlphaComponent(0.55 + flash * 0.4).setStroke(); cap.lineWidth = 0.65; cap.stroke()
                if let previous = previousPeak {
                    let crown = NSBezierPath(); crown.move(to: previous); crown.line(to: peak)
                    color.withAlphaComponent(0.13 + flash * 0.1).setStroke()
                    crown.lineWidth = 0.5; crown.stroke()
                }
                previousPeak = peak
                if !inner {
                    let reflection = NSBezierPath(); reflection.move(to: base)
                    reflection.line(to: point(center, radius - min(2.2, height * 0.15), barAngle))
                    color.withAlphaComponent(opacity * 0.19).setStroke()
                    reflection.lineWidth = width * 0.8; reflection.stroke()
                }
                if flash > 0 {
                    bright.withAlphaComponent(flash * 0.8).setFill()
                    let dot: CGFloat = inner ? 0.55 : 0.75
                    NSBezierPath(ovalIn: NSRect(x: tip.x - dot, y: tip.y - dot, width: dot * 2, height: dot * 2)).fill()
                }
            }
        }
    }

    private func drawLayeredWaves(_ center: NSPoint, ring: Ring, inner: Bool, now: TimeInterval,
                                  reducedMotion: Bool, style: String) {
        let span = CGFloat.pi * 2 / CGFloat(max(1, ring.indices.count))
        let bandScale: CGFloat = inner ? 0.42 : 1
        let baseRadius = ring.radius + (inner ? 16 : 18)
        func stroke(_ points: [NSPoint], color: NSColor, width: CGFloat, alpha: CGFloat) {
            guard let first = points.first else { return }
            let path = NSBezierPath(); path.move(to: first)
            for position in points.dropFirst() { path.line(to: position) }
            path.lineJoinStyle = .round; path.lineCapStyle = .round
            color.withAlphaComponent(alpha * 0.22).setStroke(); path.lineWidth = width + 3 * bandScale; path.stroke()
            color.withAlphaComponent(alpha).setStroke(); path.lineWidth = width; path.stroke()
        }
        for (slot, nodeIndex) in ring.indices.enumerated() {
            let node = nodes[nodeIndex]
            guard node.running else { continue }
            let theta = angle(slot, ring)
            let count = max(16, min(inner ? 36 : 56, Int(36 * waveDensity)))
            let marks = outputMarks[node.key] ?? []
            let energyTime = now
            let recent = marks.filter { energyTime - $0.time >= 0 && energyTime - $0.time < 2.5 }
            let recentAmount = recent.reduce(0) { $0 + $1.amount }
            let liveEnergy = node.live && node.running ? outputLevel(Double(recentAmount) / 2.5) : 0
            let moving = liveEnergy > 0 && !reducedMotion
            // Reduced motion stops oscillation, while real history still expires.
            let anchor = now
            let bucket = 24 / Double(count)
            let end = floor(anchor / bucket) * bucket + bucket
            let energies: [CGFloat] = (0..<count).map { index in
                guard node.live && node.running else { return 0 }
                let start = end - Double(count - index) * bucket
                let amount = marks.filter { $0.time >= start && $0.time < start + bucket }.reduce(0) { $0 + $1.amount }
                return outputLevel(Double(amount) / bucket)
            }
            let track = NSBezierPath()
            track.appendArc(withCenter: center, radius: baseRadius, startAngle: (theta - span * 0.39) * 180 / .pi,
                            endAngle: (theta + span * 0.39) * 180 / .pi)
            waveColor(node, fraction: 0).withAlphaComponent(node.live ? 0.11 : 0.06).setStroke()
            track.lineWidth = 0.6; track.stroke()
            if style == "radar" {
                for index in 0..<max(8, count / 2) {
                    let fraction = CGFloat(index) / CGFloat(max(1, count / 2 - 1))
                    let position = point(center, baseRadius + 4 * bandScale, theta + span * (0.38 - fraction * 0.76))
                    waveColor(node, fraction: fraction).withAlphaComponent(node.live ? 0.1 : 0.04).setFill()
                    NSBezierPath(ovalIn: NSRect(x: position.x - 0.45, y: position.y - 0.45, width: 0.9, height: 0.9)).fill()
                }
            }
            guard node.live && node.running, energies.contains(where: { $0 > 0 }) else { continue }
            let phase: CGFloat = moving ? CGFloat(now) : 0
            if style == "radar" {
                let events = reducedMotion ? Array(marks.suffix(3)) : Array(recent.suffix(3))
                for (index, mark) in events.enumerated() {
                    let progress = reducedMotion ? CGFloat(index + 1) / 4 : CGFloat(min(1, max(0, now - mark.time) / 2.5))
                    let energy = outputLevel(mark.amount)
                    let radius = baseRadius + 12 * waveHeight * bandScale * progress
                    let arc = NSBezierPath()
                    arc.appendArc(withCenter: center, radius: radius, startAngle: (theta - span * 0.36) * 180 / .pi,
                                  endAngle: (theta + span * 0.36) * 180 / .pi)
                    let color = waveColor(node, fraction: progress)
                    color.withAlphaComponent((0.18 + energy * 0.65) * (reducedMotion ? 0.7 : 1 - progress)).setStroke()
                    arc.lineWidth = 0.8 + energy * 0.5; arc.lineCapStyle = .round; arc.stroke()
                    let tip = point(center, radius, theta - span * 0.36)
                    let size: CGFloat = 1 + energy * bandScale
                    color.withAlphaComponent(reducedMotion ? 0.6 : 1 - progress).setFill()
                    NSBezierPath(ovalIn: NSRect(x: tip.x - size, y: tip.y - size, width: size * 2, height: size * 2)).fill()
                }
                continue
            }
            let layers = style == "plasma" ? 2 : 3
            var outerPoints: [NSPoint] = []
            for layer in 0..<layers {
                let maximum: CGFloat = (style == "plasma" && layer == 0 ? 8 : 6) * waveHeight * bandScale
                let points: [NSPoint] = (0..<count).map { index in
                    let fraction = CGFloat(index) / CGFloat(count - 1)
                    let envelope = min(1, energies[index] * 0.75 + liveEnergy * 0.3 * sin(fraction * .pi))
                    let speed: CGFloat = style == "plasma" ? (layer == 0 ? 1.8 : -1.3) : 0.7 + CGFloat(layer) * 0.2
                    let oscillation = 0.5 + 0.5 * sin(fraction * .pi * CGFloat(5 + layer * 2) + phase * speed + CGFloat(layer))
                    let radius = baseRadius + maximum * envelope * (0.25 + oscillation * 0.75)
                    return point(center, radius, theta + span * (0.39 - fraction * 0.78))
                }
                let color = waveColor(node, fraction: CGFloat(layer) / CGFloat(max(1, layers - 1)))
                if style == "aurora", let first = points.first {
                    let ribbon = NSBezierPath(); ribbon.move(to: first)
                    for position in points.dropFirst() { ribbon.line(to: position) }
                    for position in points.reversed() {
                        let dx = position.x - center.x, dy = position.y - center.y
                        let length = max(1, hypot(dx, dy))
                        ribbon.line(to: NSPoint(x: position.x - dx / length * 1.8 * bandScale,
                                               y: position.y - dy / length * 1.8 * bandScale))
                    }
                    ribbon.close(); color.withAlphaComponent(0.08 + liveEnergy * 0.08).setFill(); ribbon.fill()
                }
                stroke(points, color: color, width: ThemePalette.waveLineWidth(waveStyle) * (inner ? 0.75 : 1),
                       alpha: 0.3 + max(liveEnergy, energies.max() ?? 0) * 0.55)
                outerPoints = points
            }
            if style == "plasma", !outerPoints.isEmpty {
                let particles = reducedMotion ? Array(marks.suffix(3)) : Array(recent.suffix(3))
                for (index, mark) in particles.enumerated() {
                    let age = min(2.5, max(0, now - mark.time))
                    let fraction = reducedMotion ? Double(index + 1) / 4 : (Double(index) * 0.27 + age / 2.5).truncatingRemainder(dividingBy: 1)
                    let position = outerPoints[min(outerPoints.count - 1, Int(fraction * Double(outerPoints.count - 1)))]
                    let size: CGFloat = 0.8 + outputLevel(mark.amount) * 0.7 * bandScale
                    waveColor(node, fraction: CGFloat(fraction)).withAlphaComponent(reducedMotion ? 0.75 : 1 - CGFloat(age / 3)).setFill()
                    NSBezierPath(ovalIn: NSRect(x: position.x - size, y: position.y - size, width: size * 2, height: size * 2)).fill()
                }
            }
        }
    }

    private func waveColor(_ node: Node, fraction: CGFloat) -> NSColor {
        ThemePalette.waveColor(state: node.visualState, palette: wavePalette, fraction: fraction)
    }

    private func drawHub(_ center: NSPoint, time: CGFloat, reducedMotion: Bool) {
        let radius = (rings.last?.radius ?? orbitRadius) - 25
        let scale = min(1.12, radius / 51)
        let energy = activity
        let hasRecentOutput = outputMarks.values.contains { $0.last.map { ProcessInfo.processInfo.systemUptime - $0.time < 2.5 } == true }
        let pulse: CGFloat = connected && runningCount > 0 && energy > 0.001 && hasRecentOutput && !reducedMotion ? (sin(time * 1.8) + 1) / 2 : 0
        let glowRadius = radius * (0.78 + energy * 0.12 + pulse * 0.025)
        let core = NSBezierPath(ovalIn: NSRect(x: center.x - glowRadius, y: center.y - glowRadius, width: glowRadius * 2, height: glowRadius * 2))
        NSGradient(starting: tint.withAlphaComponent(0.09 + energy * 0.12 + pulse * 0.02), ending: tint.withAlphaComponent(0))?
            .draw(in: core, relativeCenterPosition: .zero)
        // Short guide arcs frame the content without boxing in session names.
        for start in [CGFloat(35), 155, 275] {
            let guide = NSBezierPath()
            guide.appendArc(withCenter: center, radius: radius - 2, startAngle: start, endAngle: start + 27)
            tint.withAlphaComponent(0.13 + energy * 0.13).setStroke(); guide.lineWidth = 0.65; guide.stroke()
        }
        func rect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSRect {
            NSRect(x: center.x + x * scale, y: center.y + y * scale, width: w * scale, height: h * scale)
        }
        // The hub is always a global overview; hovering an arc only highlights that arc.
        let countSize: CGFloat = max(runningCount, waitingCount) >= 100 ? 22 : 27
        drawText(connected ? "\(runningCount)" : "—", rect: rect(-43, 7, 37, 29), size: countSize * scale,
                 color: connected ? ThemePalette.ns(.running) : ThemePalette.ns(.offline))
        drawText("/", rect: rect(-6, 13, 12, 21), size: 17 * scale, color: ThemePalette.ns(.muted))
        drawText(connected ? "\(waitingCount)" : "—", rect: rect(6, 7, 37, 29), size: countSize * scale,
                 color: connected ? ThemePalette.ns(.waiting) : ThemePalette.ns(.offline))
        drawText("运行", rect: rect(-43, -7, 37, 13), size: 10 * scale, color: ThemePalette.ns(.text))
        drawText("等待", rect: rect(6, -7, 37, 13), size: 10 * scale, color: ThemePalette.ns(.text))
        var notices: [String] = []
        if !connected || connectionState == "partial" { notices.append(connectionNotice) }
        if connected && errorCount > 0 { notices.append("异常 \(errorCount)") }
        let unknownCount = nodes.filter { $0.visualState == .unknown }.count
        if connected && errorCount == 0 && warningCount > 0 { notices.append("待处理 \(warningCount)") }
        if connected && errorCount == 0 && warningCount == 0 && unknownCount > 0 { notices.append("待核实 \(unknownCount)") }
        if connected && overdueCount > 0 { notices.append("久等 \(overdueCount)") }
        if !notices.isEmpty {
            drawFittedText(notices.joined(separator: " · "), rect: rect(-48, 37, 96, 12), size: 8.7 * scale, minimumSize: 8 * scale,
                     color: !connected ? ThemePalette.session(ThemePalette.connection(connected: connected, state: connectionState)) : errorCount > 0 ? ThemePalette.ns(.error) : connectionState == "partial" || warningCount > 0 || overdueCount > 0 ? ThemePalette.ns(.attention) : ThemePalette.ns(.unknown))
        }
        let responseSummary = pendingCount > 0 ? longestWaitValue : responseCoverage ? "暂无等待" : "时长未知"
        drawFittedText(connected ? "待你 \(pendingCountText) · " + responseSummary : "待你 — · 时长未知",
                      rect: rect(-51, -21, 102, 13), size: 9 * scale, minimumSize: 8.5 * scale,
                      color: ThemePalette.ns(!connected || pendingCount == 0 ? .muted : overdueCount > 0 ? .attention : .waiting))
        let tokenWidth: CGFloat = 108
        func tokenLine(prefix: String, exact: String, value: Int?, suffix: String, size: CGFloat) -> String {
            var amounts = [exact]
            if exact != "—", let value = value, value >= 0 {
                amounts.append(UsageMetrics.amount(value))
                amounts.append(String(format: "%.2e", Double(value)).replacingOccurrences(of: "e+", with: "e"))
            }
            let font = NSFont.monospacedSystemFont(ofSize: size, weight: .medium)
            return amounts.map { prefix + $0 + suffix }.first {
                ($0 as NSString).size(withAttributes: [.font: font]).width <= tokenWidth
            } ?? prefix + (amounts.last ?? "—") + suffix
        }
        let liveTokens = usage?.liveWindowTokens(connected: connected) ?? "—"
        let livePartial = liveTokens != "—" && usage?.liveUsageIsPartial == true ? "*" : ""
        let window = max(1, usage?.windowSeconds ?? 30)
        let liveLine = tokenLine(prefix: "\(window)s " + (liveTokens == "—" ? "" : "+"), exact: liveTokens,
                                 value: liveTokens == "—" ? nil : usage?.windowTokens, suffix: " tok" + livePartial, size: 10)
        drawFittedText(liveLine, rect: rect(-54, -35, tokenWidth, 13), size: 10 * scale, minimumSize: 9.5 * scale,
                      color: ThemePalette.ns(liveTokens == "—" ? .muted : .accent))
        let totalLine = tokenLine(prefix: "累计 ", exact: UsageMetrics.exactAmount(usage?.totalTokens), value: usage?.totalTokens,
                                  suffix: " tok" + (usage?.usageIsPartial == true ? "*" : ""), size: 9.5)
        drawFittedText(totalLine, rect: rect(-54, -49, tokenWidth, 13), size: 9.5 * scale, minimumSize: 9 * scale,
                      color: ThemePalette.ns(connected ? .text : .muted))
    }

    private func drawChart(in rect: NSRect) {
        let baseline = NSBezierPath()
        baseline.move(to: NSPoint(x: rect.minX, y: rect.minY)); baseline.line(to: NSPoint(x: rect.maxX, y: rect.minY))
        ThemePalette.ns(.muted).withAlphaComponent(0.2).setStroke(); baseline.lineWidth = 0.5; baseline.stroke()
        let now = ProcessInfo.processInfo.systemUptime
        var segment: [NSPoint] = []
        var previous: TimeInterval?
        func flush() {
            guard let first = segment.first else { return }
            let line = NSBezierPath(); line.move(to: first)
            for point in segment.dropFirst() { line.line(to: point) }
            let area = line.copy() as! NSBezierPath
            area.line(to: NSPoint(x: segment.last!.x, y: rect.minY))
            area.line(to: NSPoint(x: first.x, y: rect.minY)); area.close()
            tint.withAlphaComponent(connected ? 0.1 : 0.03).setFill(); area.fill()
            tint.withAlphaComponent(connected ? 0.7 : 0.2).setStroke(); line.lineWidth = 1; line.lineJoinStyle = .round; line.stroke()
            segment.removeAll(keepingCapacity: true)
        }
        for sample in samples {
            if sample.level == nil || previous.map({ sample.time - $0 > 2.5 }) == true { flush() }
            if let level = sample.level {
                let x = rect.maxX - CGFloat(min(60, max(0, now - sample.time))) / 60 * rect.width
                segment.append(NSPoint(x: x, y: rect.minY + min(1, max(0, level)) * rect.height))
            }
            previous = sample.time
        }
        let lastPoint = segment.last
        flush()
        if connected, let end = lastPoint {
            tint.withAlphaComponent(0.8).setFill()
            NSBezierPath(ovalIn: NSRect(x: end.x - 1.2, y: end.y - 1.2, width: 2.4, height: 2.4)).fill()
        }
    }

    private func drawArcText(_ text: String, center: NSPoint, radius: CGFloat, angle: CGFloat, size: CGFloat, color: NSColor) {
        let font = NSFont.monospacedSystemFont(ofSize: size, weight: .medium)
        let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color]
        let characters = text.map { String($0) as NSString }
        let widths = characters.map { $0.size(withAttributes: attributes).width + 0.25 }
        let total = widths.reduce(0, +)
        let bottom = sin(angle.truncatingRemainder(dividingBy: .pi * 2)) < -0.1
        var offset = -total / 2
        for (index, character) in characters.enumerated() {
            let width = widths[index]
            let delta = (offset + width / 2) / radius
            let theta = angle + (bottom ? delta : -delta)
            let position = point(center, radius, theta)
            NSGraphicsContext.saveGraphicsState()
            let transform = NSAffineTransform()
            transform.translateX(by: position.x, yBy: position.y)
            transform.rotate(byDegrees: theta * 180 / .pi + (bottom ? 90 : -90))
            transform.concat()
            character.draw(at: NSPoint(x: -width / 2, y: -font.ascender / 2), withAttributes: attributes)
            NSGraphicsContext.restoreGraphicsState()
            offset += width
        }
    }

    private func drawText(_ text: String, rect: NSRect, size: CGFloat, color: NSColor) {
        let style = NSMutableParagraphStyle(); style.alignment = .center; style.lineBreakMode = .byTruncatingMiddle
        (text as NSString).draw(in: rect, withAttributes: [.font: NSFont.monospacedSystemFont(ofSize: size, weight: .medium), .foregroundColor: color, .paragraphStyle: style])
    }

    private func drawFittedText(_ text: String, rect: NSRect, size: CGFloat, minimumSize: CGFloat, color: NSColor) {
        let width = (text as NSString).size(withAttributes: [.font: NSFont.monospacedSystemFont(ofSize: size, weight: .medium)]).width
        let fittedSize = width > rect.width ? max(minimumSize, size * rect.width / width) : size
        drawText(text, rect: rect, size: fittedSize, color: color)
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self)
        tracking = area; addTrackingArea(area)
    }

    override func mouseMoved(with event: NSEvent) {
        guard !miniMode else {
            if hoveredName != nil { hoveredName = nil; needsDisplay = true }
            return
        }
        let hovered = targetKey(at: convert(event.locationInWindow, from: nil))
        if hoveredName != hovered { hoveredName = hovered; updateToolTip(); needsDisplay = true }
    }

    /// Resolve the pointer's current label sector, independent of stale hover state.
    func targetKey(at cursor: NSPoint) -> String? {
        guard !miniMode else { return nil }
        let center = NSPoint(x: bounds.midX, y: bounds.midY)
        let dx = cursor.x - center.x, dy = cursor.y - center.y
        let radius = hypot(dx, dy)
        let theta = atan2(dy, dx)
        if let ring = rings.min(by: { abs($0.radius - radius) < abs($1.radius - radius) }), abs(ring.radius - radius) < 13, !ring.indices.isEmpty {
            let raw = (.pi / 2 + ring.phase - theta) * CGFloat(ring.indices.count) / (.pi * 2)
            let slot = (Int(raw.rounded()) % ring.indices.count + ring.indices.count) % ring.indices.count
            return nodes[ring.indices[slot]].key
        }
        return nil
    }
    override func mouseExited(with event: NSEvent) { hoveredName = nil; updateToolTip(); needsDisplay = true }

    override func mouseDown(with event: NSEvent) {
        cancelSingleClick()
        pressPoint = window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation
        windowOrigin = window?.frame.origin ?? .zero; dragged = false
    }
    override func mouseDragged(with event: NSEvent) {
        guard let start = pressPoint else { return }
        let point = window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation
        let dx = point.x - start.x, dy = point.y - start.y
        if abs(dx) + abs(dy) > 4 { dragged = true; cancelSingleClick() }
        if dragged { window?.setFrameOrigin(NSPoint(x: windowOrigin.x + dx, y: windowOrigin.y + dy)) }
    }
    override func mouseUp(with event: NSEvent) {
        guard let start = pressPoint else { return }
        let point = window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation
        if abs(point.x - start.x) + abs(point.y - start.y) > 4 { dragged = true }
        pressPoint = nil
        if dragged {
            cancelSingleClick()
            moved?()
        } else if event.clickCount == 2 {
            openPanel(nil)
        } else if event.clickCount <= 1 {
            let generation = clickGeneration
            let action = DispatchWorkItem { [weak self] in
                guard let self = self, self.clickGeneration == generation else { return }
                self.toggleSize(nil)
            }
            singleClick = action
            DispatchQueue.main.asyncAfter(deadline: .now() + doubleClickDelay + 0.02, execute: action)
        }
    }

    override func menu(for event: NSEvent) -> NSMenu? {
        cancelSingleClick()
        let menu = NSMenu()
        menu.autoenablesItems = false
        if let key = targetKey(at: convert(event.locationInWindow, from: nil)), let node = nodes.first(where: { $0.key == key }) {
            let heading = menu.addItem(withTitle: node.name, action: nil, keyEquivalent: "")
            heading.isEnabled = false
            addManualActions(to: menu, node: node)
            menu.addItem(.separator())
        } else if !nodes.isEmpty {
            let targets = NSMenu(title: "会话与互斥组")
            targets.autoenablesItems = false
            for node in nodes {
                let item = targets.addItem(withTitle: node.name + (node.manualRunning ? " · 手动" : ""), action: nil, keyEquivalent: "")
                let actions = NSMenu(title: node.name)
                actions.autoenablesItems = false
                addManualActions(to: actions, node: node)
                item.submenu = actions
            }
            let list = menu.addItem(withTitle: "会话与互斥组", action: nil, keyEquivalent: "")
            list.submenu = targets
            menu.addItem(.separator())
        }
        let open = menu.addItem(withTitle: "打开面板", action: #selector(openPanel(_:)), keyEquivalent: "")
        open.target = self
        let toggle = menu.addItem(withTitle: miniMode ? "放大为完整会话环" : "缩小为小号悬浮球", action: #selector(toggleSize(_:)), keyEquivalent: "")
        toggle.target = self
        return menu
    }

    private func addManualActions(to menu: NSMenu, node: Node) {
        let mark = menu.addItem(withTitle: "手动标为运行中", action: #selector(markManualRunning(_:)), keyEquivalent: "")
        mark.target = self; mark.representedObject = node.manualTarget
        mark.identifier = NSUserInterfaceItemIdentifier("manual.running." + node.key)
        mark.state = node.manualRunning ? .on : .off
        mark.toolTip = "收到新的回复结果后自动解除；不会遮蔽错误或离线状态。"
        let clear = menu.addItem(withTitle: "取消手动运行", action: #selector(clearManualRunning(_:)), keyEquivalent: "")
        clear.target = self; clear.representedObject = node.manualTarget
        clear.identifier = NSUserInterfaceItemIdentifier("manual.clear." + node.key)
        clear.toolTip = "恢复自动判断，也可撤回刚提交的手动标记。"
    }

    @objc private func markManualRunning(_ sender: NSMenuItem) {
        guard let target = sender.representedObject as? ManualRunTarget else { return }
        model?.requestManualRunning(.running, target: target)
    }

    @objc private func clearManualRunning(_ sender: NSMenuItem) {
        guard let target = sender.representedObject as? ManualRunTarget else { return }
        model?.requestManualRunning(.clear, target: target)
    }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 || event.keyCode == 76 {
            openPanel(nil)
        } else if event.charactersIgnoringModifiers == " " {
            toggleSize(nil)
        } else {
            super.keyDown(with: event)
        }
    }
}
