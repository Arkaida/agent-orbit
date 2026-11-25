import AppKit
import Darwin

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
        var outputKnown = false
        var outputPartial = false
        var outputFreshUntil: TimeInterval?
        var outputRate: Double?
        var memoryBytes: Double?
        var hostMemoryBytes: Double?
        var memoryPartial = false
        var cpuPercent: Double?
        var cpuPartial = false
        var gpuPercent: Double?
        var gpuPartial = false
        var diskRate: Double?
        var diskKnown = false
        var diskPartial = false
        var networkRate: Double?
        var networkKnown = false
        var networkPartial = false
        var networkShared = false
        var processShared = false
        var processUnattributed = false
        var toolsKnown = false
        var toolsPartial = false
        var activeTools: Int?
    }
    private struct ResourceMark {
        var time: TimeInterval
        var amount: Double
        var identity: String
        var duration: TimeInterval = 0
        var sampleKey: String?
    }
    private struct ResourceReading {
        var epoch: String?
        var sample: Int?
        var gpuSample: Int?
        var read: Int?
        var write: Int?
        var received: Int?
        var sent: Int?
    }
    private struct ToolReading {
        var epoch: Int?
        var total: Int
    }
    private struct DialogueReading {
        var sample: Int
        var total: Int
        var time: TimeInterval
        var source: String?
    }
    private struct PanePosition: Hashable {
        var source: String
        var pane: String
    }
    /// Ownership outlives a temporary measurement gap; counter baselines do not.
    private struct StreamOwnership {
        var identity: String
        var epoch: String?
        var producer: String?
        var target: String
    }
    struct WaveEvent {
        var time: TimeInterval
        var amount: Double
        var duration: TimeInterval = 0
        var pointValue = false
    }
    struct WaveBucket {
        var rate: Double = 0
        var newest: TimeInterval?
        var startTime: TimeInterval = 0
    }
    private struct GeometryKey: Hashable {
        var node: String
        var role: ResourceRole
        var bucketStart: TimeInterval
    }
    private struct GeometryTransition {
        var from: CGFloat
        var target: CGFloat
        var startedAt: TimeInterval
        func height(at now: TimeInterval) -> CGFloat {
            BubbleView.interpolatedWaveHeight(from: from, to: target,
                                               elapsed: now - startedAt) ?? target
        }
    }
    static let waveTransitionDuration: TimeInterval = 0.18
    // Settings permit at most five seconds between samples; three missed
    // intervals bound dialogue continuity without accepting unbounded backlog.
    private static let maximumDialogueObservationWindow: TimeInterval = 15
    static let dynamicResourceRoles: [ResourceRole] = [.cpu, .gpu, .disk, .network, .output]
    private static let hardwareRoles: [ResourceRole] = [.cpu, .gpu, .disk, .network]
    private var observationTime: TimeInterval?
    private var visualNow: TimeInterval { observationTime ?? ProcessInfo.processInfo.systemUptime }
    private var waveTransitions: [GeometryKey: GeometryTransition] = [:]
    private var drawCostEMA: TimeInterval?
    private let textLayouts = OrbitTextLayoutCache()
    struct PresentationCPUReading {
        var time: TimeInterval
        var cpuSeconds: TimeInterval
        var drawnFrames: UInt64
    }
    private var drawnFrames: UInt64 = 0
    private var processCPUReading: PresentationCPUReading?
    private var processFrameCostEMA: TimeInterval?
    private var lastCPUPoll: TimeInterval?
    private var resourceMarks: [String: [ResourceRole: [ResourceMark]]] = [:]
    private var resourceReadings: [String: ResourceReading] = [:]
    private var toolReadings: [String: ToolReading] = [:]
    private var resourceTargets: [String: Set<String>] = [:]
    private var resourceReadyAt: [String: [String: TimeInterval]] = [:]
    private var resourceOwners: [PanePosition: StreamOwnership] = [:]
    private var dialogueMarks: [String: [ResourceMark]] = [:]
    private var dialogueReadings: [String: DialogueReading] = [:]
    private var dialogueTargets: [String: Set<String>] = [:]
    private var dialogueReadyAt: [String: [String: TimeInterval]] = [:]
    private var dialogueOwners: [PanePosition: StreamOwnership] = [:]
    private var dialogueActivePanes = Set<String>()
    private var dialogueObservationWindow: TimeInterval = 4
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

    /// Start a fresh visual baseline when returning from a hidden window. The
    /// current model still determines status; hidden increments are not replayed.
    func resumePresentation() {
        outputMarks.removeAll(); outputReadings.removeAll(); outputActivePanes.removeAll()
        resourceMarks.removeAll(); resourceReadings.removeAll(); toolReadings.removeAll()
        resourceTargets.removeAll(); resourceReadyAt.removeAll(); toolTargets.removeAll(); waveTransitions.removeAll()
        resourceOwners.removeAll()
        dialogueMarks.removeAll(); dialogueReadings.removeAll(); dialogueTargets.removeAll()
        dialogueReadyAt.removeAll(); dialogueActivePanes.removeAll()
        dialogueOwners.removeAll()
        observingSince.removeAll(); levels.removeAll(); samples.removeAll()
        processCPUReading = nil; lastCPUPoll = nil
    }

    static func adaptiveFrameRate(visible: Bool, mini: Bool, reducedMotion: Bool,
                                  fixedTime: Bool, recentPeak: Bool, drawCost: TimeInterval?) -> Double {
        guard visible, !fixedTime else { return 0 }
        let preferred = mini ? 1.5 : reducedMotion ? 2.0 : recentPeak ? 24.0 : 3.0
        guard let cost = drawCost, cost.isFinite, cost > 0 else { return preferred }
        // Spend at most roughly 6% of one core on drawing, before framework
        // overhead. Heavy frames lower presentation rate, never state delivery.
        return min(preferred, max(1, 0.06 / cost))
    }

    /// Self CPU time includes framework work after draw() returns, such as
    /// display-list commit and client-side rasterisation. It is an estimate,
    /// not a promise that every part of the app fits the presentation budget.
    static func processFrameCost(previous: PresentationCPUReading, current: PresentationCPUReading) -> TimeInterval? {
        guard previous.time.isFinite, current.time.isFinite, previous.time >= 0, current.time >= 0,
              previous.cpuSeconds.isFinite, current.cpuSeconds.isFinite,
              previous.cpuSeconds >= 0, current.cpuSeconds >= previous.cpuSeconds,
              current.drawnFrames >= previous.drawnFrames else { return nil }
        let elapsed = current.time - previous.time
        guard elapsed >= 1, elapsed <= 10 else { return nil }
        let frames = current.drawnFrames - previous.drawnFrames
        guard frames > 0 else { return nil }
        let cost = (current.cpuSeconds - previous.cpuSeconds) / Double(frames)
        return cost.isFinite && cost >= 0 ? cost : nil
    }

    static func smoothedProcessFrameCost(previous: TimeInterval?, sample: TimeInterval) -> TimeInterval? {
        let prior = previous.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil }
        guard sample.isFinite, sample >= 0 else { return prior }
        guard let prior = prior else { return sample }
        // React to increased cost promptly; recover slowly to avoid FPS churn.
        let weight = sample > prior ? 0.55 : 0.1
        return prior + (sample - prior) * weight
    }

    private func updateProcessFrameCost() {
        let now = ProcessInfo.processInfo.systemUptime
        if let last = lastCPUPoll, now >= last, now - last < 1 { return }
        lastCPUPoll = now
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { processCPUReading = nil; return }
        let cpu = Double(usage.ru_utime.tv_sec) + Double(usage.ru_utime.tv_usec) / 1_000_000 +
            Double(usage.ru_stime.tv_sec) + Double(usage.ru_stime.tv_usec) / 1_000_000
        let current = PresentationCPUReading(time: now, cpuSeconds: cpu, drawnFrames: drawnFrames)
        if let previous = processCPUReading, let cost = Self.processFrameCost(previous: previous, current: current) {
            processFrameCostEMA = Self.smoothedProcessFrameCost(previous: processFrameCostEMA, sample: cost)
        }
        processCPUReading = current
    }

    func recommendedFrameRate(visible: Bool) -> Double {
        guard visible, observationTime == nil else { return 0 }
        updateProcessFrameCost()
        let clock = visualNow
        let resourcePeak = resourceMarks.values.contains { channels in
            channels.contains { role, marks in
                marks.last.map { $0.amount >= Self.resourceReference(role) * 0.02 &&
                    clock - $0.time >= 0 && clock - $0.time < 1.5 } == true
            }
        } || dialogueMarks.values.contains { marks in
            marks.last.map { $0.amount > 0 && clock - $0.time >= 0 && clock - $0.time < 1.5 } == true
        }
        return Self.adaptiveFrameRate(visible: visible, mini: miniMode,
                                      reducedMotion: reduceMotion || NSWorkspace.shared.accessibilityDisplayShouldReduceMotion,
                                      fixedTime: observationTime != nil, recentPeak: resourcePeak,
                                      drawCost: [drawCostEMA, processFrameCostEMA].compactMap { $0 }.max())
    }

    /// The independent presentation clock only requests a redraw. It never
    /// republishes SwiftUI state, records counters or rebuilds session geometry.
    func presentationTick() {
        guard observationTime == nil else { return }
        needsDisplay = true
    }

    /// Explicit time drives both sampling and drawing for deterministic exports.
    /// Normal updates leave the clock live.
    func update(_ model: PanelModel, now explicitTime: TimeInterval? = nil) {
        self.model = model
        observationTime = explicitTime.flatMap { $0.isFinite ? $0 : nil }
        if observationTime != nil { processCPUReading = nil; lastCPUPoll = nil }
        let now = visualNow
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
        let sampleInterval = model.settings.interval
        dialogueObservationWindow = sampleInterval.isFinite && (0.25...5).contains(sampleInterval)
            ? max(4, sampleInterval * 3) : 4
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
        recordDialogueOutput(liveRows, eligible: eligible, now: now)
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
        waveTransitions = waveTransitions.filter { key, _ in
            workingKeys.contains(key.node) && now - key.bucketStart < 24
        }
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
        overviewToolTip = summary + "\n" + usageSummary + "\n" + descriptions + "\n名称旁细弧：等待色为空闲，运行色为工作；悬停查看具体时长。\n声浪仅显示运行会话近24秒活动，顺时针从旧到新" + (waveResources ? "；五通道同圈叠加，颜色区分来源，几何造型统一跟随声浪外观。" : "；会话组共享声浪。") + "\n单击切换大小，双击打开面板，拖动移动；右键可打开声浪图说明。"
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
            let retained = item.value.filter { now - $0.time < 24 && eligible.contains($0.paneId) && paneKeys[$0.paneId] == item.key }
            result[item.key] = retained
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

    private func clearGeometry(node: String, role: ResourceRole) {
        waveTransitions = waveTransitions.filter { $0.key.node != node || $0.key.role != role }
    }

    private func historyOwners(_ rows: [Pane], eligible: Set<String>, previous: [PanePosition: StreamOwnership],
                               identity: (Pane) -> String?, epoch: (Pane) -> String?,
                               producer: (Pane) -> String? = { _ in nil }) -> [PanePosition: StreamOwnership] {
        var result: [PanePosition: StreamOwnership] = [:]
        for pane in rows where eligible.contains(pane.paneId) {
            let position = PanePosition(source: pane.source ?? "remote", pane: pane.paneId)
            if let identity = identity(pane) {
                result[position] = StreamOwnership(identity: identity, epoch: epoch(pane),
                                                   producer: producer(pane), target: pane.orbitKey)
            } else if var owner = previous[position] {
                // Only the same live, running position may retain observed history.
                // A new group receives no copied marks and establishes its own baseline.
                owner.target = pane.orbitKey
                result[position] = owner
            }
        }
        return result
    }

    private func ownershipChanges(_ previous: [PanePosition: StreamOwnership],
                                  _ current: [PanePosition: StreamOwnership]) -> [String: Set<String>] {
        var changed: [String: Set<String>] = [:]
        for (position, old) in previous {
            guard let new = current[position],
                  old.identity != new.identity || old.epoch != new.epoch || old.producer != new.producer else { continue }
            changed[old.target, default: []].insert(old.identity)
        }
        return changed
    }

    private func observedDialogueIdentity(_ pane: Pane) -> String? {
        guard let epoch = pane.dialogueOutput?.epoch, !epoch.isEmpty else { return nil }
        return (pane.source ?? "remote") + ":" + epoch
    }

    private func dialogueIdentity(_ pane: Pane) -> String? {
        guard pane.dialogueOutput?.isFresh(maxAge: dialogueObservationWindow) == true else { return nil }
        return observedDialogueIdentity(pane)
    }

    /// This stream contains only visible assistant body characters. Its counters,
    /// epochs and baselines never depend on resource/token/tool availability.
    private func recordDialogueOutput(_ rows: [Pane], eligible: Set<String>, now: TimeInterval) {
        dialogueActivePanes = eligible
        let groups = Dictionary(grouping: rows.filter { dialogueIdentity($0) != nil }, by: { dialogueIdentity($0)! })
        let workingKeys = Set(rows.filter { eligible.contains($0.paneId) }.map(\.orbitKey))
        let owners = historyOwners(rows, eligible: eligible, previous: dialogueOwners,
                                   identity: observedDialogueIdentity, epoch: { $0.dialogueOutput?.epoch },
                                   producer: { $0.dialogueOutput?.source })
        let changed = ownershipChanges(dialogueOwners, owners)
        dialogueOwners = owners
        let validTargets = Dictionary(grouping: owners.values, by: \.identity).mapValues { Set($0.map(\.target)) }
        let freshTargets = groups.mapValues { panes in Set(panes.filter { eligible.contains($0.paneId) }.map(\.orbitKey)) }
        for key in Array(dialogueMarks.keys) {
            let old = dialogueMarks[key] ?? []
            let retained = workingKeys.contains(key) ? old.filter {
                now - $0.time >= 0 && now - $0.time < 24 && validTargets[$0.identity]?.contains(key) == true &&
                changed[key]?.contains($0.identity) != true
            } : []
            if retained.count != old.count { clearGeometry(node: key, role: .output) }
            dialogueMarks[key] = retained
        }
        dialogueMarks = dialogueMarks.filter { workingKeys.contains($0.key) }
        guard connected else {
            dialogueMarks.removeAll(); dialogueReadings.removeAll(); dialogueTargets.removeAll()
            dialogueReadyAt.removeAll(); dialogueActivePanes.removeAll(); dialogueOwners.removeAll(); return
        }
        dialogueReadings = dialogueReadings.filter { groups[$0.key] != nil }
        dialogueTargets = dialogueTargets.filter { groups[$0.key] != nil }
        dialogueReadyAt = dialogueReadyAt.filter { groups[$0.key] != nil }
        func clear(_ identity: String) {
            for key in Array(dialogueMarks.keys) where dialogueMarks[key]?.contains(where: { $0.identity == identity }) == true {
                dialogueMarks[key]?.removeAll { $0.identity == identity }
                clearGeometry(node: key, role: .output)
            }
        }
        for (identity, panes) in groups {
            guard let pane = panes.max(by: { ($0.dialogueOutput?.sampleId ?? -1) < ($1.dialogueOutput?.sampleId ?? -1) }),
                  let value = pane.dialogueOutput, let sample = value.sampleId, let total = value.totalChars else { continue }
            let targets = freshTargets[identity] ?? []
            let previousTargets = dialogueTargets[identity] ?? []
            let old = dialogueReadings[identity]
            let time = now - (value.sampleAgeSeconds ?? 0)
            let added = targets.subtracting(previousTargets)
            var ready = dialogueReadyAt[identity] ?? [:]
            ready = ready.filter { targets.contains($0.key) }
            for key in added { ready[key] = now }
            dialogueReadyAt[identity] = ready
            dialogueTargets[identity] = targets
            if let old, sample == old.sample, total == old.total, value.source == old.source {
                // A cached payload cannot refresh its own observation clock.
                if now - old.time > dialogueObservationWindow {
                    dialogueTargets[identity] = []
                    dialogueReadyAt[identity] = [:]
                }
                continue
            }
            if let old, sample > old.sample, total >= old.total, value.source == old.source,
               time > old.time, time - old.time <= dialogueObservationWindow {
                let amount = total - old.total
                if amount > 0 {
                    for key in targets.intersection(previousTargets) where old.time >= (ready[key] ?? now) {
                        dialogueMarks[key, default: []].append(ResourceMark(time: time, amount: Double(amount),
                            identity: identity, duration: time - old.time))
                    }
                }
            } else if let old, sample <= old.sample || total < old.total || value.source != old.source || time <= old.time {
                clear(identity)
            }
            dialogueReadings[identity] = DialogueReading(sample: sample, total: total, time: time, source: value.source)
        }
    }

    private func resourceIdentity(_ pane: Pane) -> String? {
        guard pane.resources?.isFresh == true else { return nil }
        return observedResourceIdentity(pane)
    }

    private func observedResourceIdentity(_ pane: Pane) -> String? {
        guard let identity = pane.resources?.identity, !identity.isEmpty else { return nil }
        return (pane.source ?? "remote") + ":" + identity
    }

    private func toolIdentity(_ pane: Pane) -> String? {
        guard let identity = pane.usageMetrics?.rootIdentity else { return nil }
        return (pane.source ?? "remote") + ":" + identity
    }

    private func resourceState(_ rows: [Pane], expectedRows: Int) -> ResourceState {
        var result = ResourceState()
        let activeOutputRows = rows.filter { dialogueActivePanes.contains($0.paneId) }
        let outputRows = activeOutputRows.filter { pane in
            guard let identity = dialogueIdentity(pane), let reading = dialogueReadings[identity] else { return false }
            return visualNow - reading.time <= dialogueObservationWindow
        }
        result.outputKnown = !outputRows.isEmpty
        result.outputPartial = outputRows.count < activeOutputRows.count || outputRows.contains { $0.dialogueOutput?.complete != true }
        result.outputFreshUntil = outputRows.compactMap { dialogueIdentity($0).flatMap { dialogueReadings[$0]?.time } }.max().map { $0 + dialogueObservationWindow }
        let outputIdentities = Set(outputRows.compactMap(dialogueIdentity))
        let recentOutput = dialogueMarks.values.flatMap { $0 }.filter {
            outputIdentities.contains($0.identity) && visualNow - $0.time >= 0 && visualNow - $0.time < 1.5
        }
        // One epoch may appear through aliases in more than one position.
        let latestOutput = Dictionary(grouping: recentOutput, by: \.identity).compactMapValues { $0.max { $0.time < $1.time } }
        if result.outputKnown {
            result.outputRate = latestOutput.values.reduce(0) { $0 + $1.amount / max(0.01, $1.duration) }
        }
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
        let cpu = resources.compactMap(\.observedCPUPercent)
        let cpuTotal = cpu.reduce(0, +)
        if !cpu.isEmpty && cpuTotal.isFinite { result.cpuPercent = cpuTotal }
        result.cpuPartial = missingResources || cpu.count < resources.count || resources.contains { $0.cpuComplete != true }
        let gpu = resources.compactMap(\.observedGPUPercent)
        let gpuTotal = gpu.reduce(0, +)
        if !gpu.isEmpty && gpuTotal.isFinite { result.gpuPercent = gpuTotal }
        result.gpuPartial = missingResources || gpu.count < resources.count || resources.contains { $0.gpuComplete != true }
        var hosts: [String: Double] = [:]
        for pane in unique.values {
            if let bytes = pane.resources?.hostMemoryBytes, bytes > 0 {
                let source = pane.source ?? "remote"; hosts[source] = max(hosts[source] ?? 0, Double(bytes))
            }
        }
        if !hosts.isEmpty { result.hostMemoryBytes = hosts.values.reduce(0, +) }
        func rate(_ value: Double?) -> Double? { value.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil } }
        func observedEvents(_ role: ResourceRole, available: (ResourceMetrics) -> Bool) -> Bool {
            unique.values.contains { pane in
                guard let resource = pane.resources, available(resource), let identity = resourceIdentity(pane) else { return false }
                return resourceMarks[pane.orbitKey]?[role]?.contains {
                    $0.identity == "resource:" + identity && $0.amount > 0
                } == true
            }
        }
        let io = resources.filter { $0.ioAvailable == true }
        let ioRates = io.compactMap { value -> Double? in
            let directions = [rate(value.diskReadPerSecond), rate(value.diskWritePerSecond)].compactMap { $0 }
            return directions.isEmpty ? nil : directions.reduce(0, +)
        }
        if !ioRates.isEmpty { result.diskRate = ioRates.reduce(0, +) }
        result.diskKnown = !ioRates.isEmpty || observedEvents(.disk, available: { $0.ioAvailable == true })
        result.diskPartial = missingResources || ioRates.count < resources.count || io.contains { $0.ioComplete != true || rate($0.diskReadPerSecond) == nil || rate($0.diskWritePerSecond) == nil }
        let network = resources.filter { $0.networkAvailable == true }
        let networkRates = network.compactMap { value -> Double? in
            let directions = [rate(value.networkRxPerSecond), rate(value.networkTxPerSecond)].compactMap { $0 }
            return directions.isEmpty ? nil : directions.reduce(0, +)
        }
        result.networkShared = network.contains { $0.networkShared == true }
        if !networkRates.isEmpty { result.networkRate = result.networkShared ? networkRates.max() : networkRates.reduce(0, +) }
        result.networkKnown = !networkRates.isEmpty || observedEvents(.network, available: { $0.networkAvailable == true })
        result.networkPartial = missingResources || networkRates.count < resources.count || network.contains { $0.networkComplete != true || rate($0.networkRxPerSecond) == nil || rate($0.networkTxPerSecond) == nil }
        result.processShared = resources.contains { $0.scope == "shared_process" || $0.attribution == "shared" }
        result.processUnattributed = resources.contains { $0.attribution == "unattributed" }
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
        let attribution = value.processShared ? "（共享进程，未分摊）" : value.processUnattributed ? "（归属待确认）" : ""
        let cpu = value.cpuPercent.map { "CPU " + ResourceMetrics.cpu($0) + "（单核100%）" + (value.cpuPartial ? "（部分）" : "") + attribution } ?? "CPU 未观测"
        let memory = value.memoryBytes.map { "内存 " + byteAmount($0) + (value.memoryPartial ? "（部分）" : "") + (value.processShared ? "（共享进程）" : "") } ?? "内存未观测"
        let io = value.diskRate.map { "磁盘 " + byteAmount($0) + "/s" + (value.diskPartial ? "（部分）" : "") } ?? "磁盘未观测"
        let network = value.networkRate.map { "网络 " + (value.networkShared ? "共享 " : "") + (value.networkShared || value.networkPartial ? "≥" : "") + byteAmount($0) + "/s" + (value.networkPartial ? "（部分）" : "") } ?? "网络未观测"
        let tools = value.activeTools.map { "未返回工具 \($0)" } ?? (value.toolsKnown ? value.toolsPartial ? "工具部分观测" : "工具已观测" : "工具未观测")
        let gpu = value.gpuPercent.map { "GPU SM " + ResourceMetrics.cpu($0) + "（进程采样合计）" + (value.gpuPartial ? "（部分）" : "") + attribution } ?? "GPU 未观测"
        let output = value.outputRate.map { "对话输出 " + String(format: "%.1f 观测字符/秒", $0) + (value.outputPartial ? "（观测下界）" : "") + "，非Token" } ?? "对话输出未观测"
        return [cpu, gpu, io, network, output, memory, tools].joined(separator: " · ")
    }

    private func byteAmount(_ value: Double?) -> String {
        guard let value = value, value.isFinite, value >= 0 else { return "—" }
        if value >= 1_073_741_824 { return String(format: "%.1f GiB", value / 1_073_741_824) }
        if value >= 1_048_576 { return String(format: "%.0f MiB", value / 1_048_576) }
        if value >= 1024 { return String(format: "%.1f KiB", value / 1024) }
        return String(format: "%.0f B", value)
    }

    private func recordResources(_ rows: [Pane], eligible: Set<String>, now: TimeInterval) {
        let groups = Dictionary(grouping: rows.filter { resourceIdentity($0) != nil }, by: { resourceIdentity($0)! })
        let workingKeys = Set(rows.filter { eligible.contains($0.paneId) }.map(\.orbitKey))
        let owners = historyOwners(rows, eligible: eligible, previous: resourceOwners,
                                   identity: observedResourceIdentity, epoch: { $0.resources?.epoch })
        let changed = ownershipChanges(resourceOwners, owners).mapValues { Set($0.map { "resource:" + $0 }) }
        resourceOwners = owners
        let validTargets = Dictionary(grouping: owners.values, by: { "resource:" + $0.identity })
            .mapValues { Set($0.map(\.target)) }
        var freshTargets: [String: Set<String>] = [:]
        for (identity, panes) in groups {
            freshTargets["resource:" + identity] = Set(panes.filter { eligible.contains($0.paneId) }.map(\.orbitKey))
        }
        resourceMarks = resourceMarks.filter { workingKeys.contains($0.key) }.mapValues { channels in
            channels.mapValues { $0.filter { now - $0.time >= 0 && now - $0.time < 24 } }
        }
        for key in Array(resourceMarks.keys) {
            for role in resourceMarks[key].map({ Array($0.keys) }) ?? [] {
                let marks = resourceMarks[key]?[role] ?? []
                let retained = marks.filter {
                    validTargets[$0.identity]?.contains(key) == true &&
                    changed[key]?.contains($0.identity) != true
                }
                if retained.count != marks.count { clearGeometry(node: key, role: role) }
                resourceMarks[key]?[role] = retained
            }
        }
        guard connected else {
            resourceReadings.removeAll(); resourceTargets.removeAll(); resourceReadyAt.removeAll(); resourceMarks.removeAll()
            resourceOwners.removeAll()
            return
        }
        resourceReadings = resourceReadings.filter { groups[$0.key] != nil }
        resourceTargets = resourceTargets.filter { groups[$0.key] != nil }
        resourceReadyAt = resourceReadyAt.filter { groups[$0.key] != nil }
        func delta(_ current: Int?, _ previous: Int?) -> Double? {
            guard let current, let previous, previous >= 0, current >= previous else { return nil }
            return Double(current - previous)
        }
        func clear(_ identity: String) {
            for key in Array(resourceMarks.keys) {
                for role in resourceMarks[key].map({ Array($0.keys) }) ?? [] {
                    let marks = resourceMarks[key]?[role] ?? []
                    if marks.contains(where: { $0.identity == identity }) { clearGeometry(node: key, role: role) }
                    resourceMarks[key]?[role] = marks.filter { $0.identity != identity }
                }
            }
        }
        func append(_ key: String, _ role: ResourceRole, _ amount: Double, _ identity: String, duration: TimeInterval = 0, observedAt: TimeInterval? = nil, sampleKey: String? = nil) {
            guard amount.isFinite, amount > 0, duration.isFinite, duration >= 0 else { return }
            resourceMarks[key, default: [:]][role, default: []].append(ResourceMark(time: observedAt ?? now, amount: amount, identity: identity, duration: duration, sampleKey: sampleKey))
        }
        var networkDeltas: [String: [(amount: Double, identity: String)]] = [:]
        var sharedNetworks = Set<String>()
        for (identity, panes) in groups {
            let markIdentity = "resource:" + identity
            let targets = freshTargets[markIdentity] ?? []
            let priorTargets = (resourceTargets[identity] ?? []).intersection(targets)
            let readyAt = resourceReadyAt[identity] ?? [:]
            resourceReadyAt[identity] = Dictionary(uniqueKeysWithValues: targets.map { key in
                (key, priorTargets.contains(key) ? readyAt[key] ?? now : now)
            })
            resourceTargets[identity] = priorTargets
            guard let value = panes.compactMap(\.resources).max(by: { ($0.sampleId ?? -1) < ($1.sampleId ?? -1) }),
                  let sample = value.sampleId else { clear(markIdentity); continue }
            let old = resourceReadings[identity]
            guard old?.sample != sample || old?.epoch != value.epoch || old?.gpuSample != value.gpuSampleId else { continue }
            let reset = old.map { previous in
                previous.epoch != value.epoch || sample < (previous.sample ?? -1) ||
                zip([value.diskReadBytes, value.diskWriteBytes, value.networkRxBytes, value.networkTxBytes],
                    [previous.read, previous.write, previous.received, previous.sent]).contains { current, prior in
                        if let current, let prior { return current < prior }
                        return false
                    }
            } ?? false
            if reset {
                clear(markIdentity)
                for key in targets.union(priorTargets) {
                    for role in Self.hardwareRoles { clearGeometry(node: key, role: role) }
                }
                resourceReadyAt[identity] = Dictionary(uniqueKeysWithValues: targets.map { ($0, now) })
            }
            if let old, !reset, old.epoch == value.epoch, sample >= (old.sample ?? -1) {
                for key in priorTargets {
                    // CPU/GPU are measured rates, not cumulative counters: integrate
                    // each unique sample's interval, never take their value difference.
                    let cpuAt = now - (value.sampleAgeSeconds ?? 0)
                    let gpuAt = cpuAt - (value.gpuSampleAgeSeconds ?? 0)
                    let began = resourceReadyAt[identity]?[key] ?? now
                    if sample > (old.sample ?? -1), let cpu = value.observedCPUPercent,
                       let interval = value.intervalSeconds, interval.isFinite, interval > 0, interval <= 5,
                       cpuAt - interval >= began {
                        append(key, .cpu, cpu * interval, markIdentity, duration: interval, observedAt: cpuAt)
                    }
                    if let gpuSample = value.gpuSampleId, let oldGPU = old.gpuSample, gpuSample > oldGPU,
                       let gpu = value.observedGPUPercent, gpuAt >= began {
                        // pmon provides a point/driver-window observation, not
                        // continuous activity throughout its polling interval.
                        let collector = panes.first?.source ?? "remote"
                        append(key, .gpu, gpu, markIdentity, observedAt: gpuAt, sampleKey: collector + ":" + String(gpuSample))
                    }
                    guard sample > (old.sample ?? -1) else { continue }
                    let disk = [delta(value.diskReadBytes, old.read), delta(value.diskWriteBytes, old.write)].compactMap { $0 }
                    if value.ioAvailable == true, !disk.isEmpty { append(key, .disk, disk.reduce(0, +), markIdentity) }
                    let network = [delta(value.networkRxBytes, old.received), delta(value.networkTxBytes, old.sent)].compactMap { $0 }
                    if value.networkAvailable == true, !network.isEmpty {
                        networkDeltas[key, default: []].append((network.reduce(0, +), markIdentity))
                        if value.networkShared == true { sharedNetworks.insert(key) }
                    }
                }
            }
            resourceReadings[identity] = ResourceReading(epoch: value.epoch, sample: sample, gpuSample: value.gpuSampleId,
                read: value.ioAvailable == true ? value.diskReadBytes : nil,
                write: value.ioAvailable == true ? value.diskWriteBytes : nil,
                received: value.networkAvailable == true ? value.networkRxBytes : nil,
                sent: value.networkAvailable == true ? value.networkTxBytes : nil)
            // Resuming or adding a group member establishes a fresh hardware baseline.
            resourceTargets[identity] = targets
        }
        for (key, amounts) in networkDeltas {
            let values = sharedNetworks.contains(key) ? amounts.max(by: { $0.amount < $1.amount }).map { [$0] } ?? [] : amounts
            for value in values { append(key, .network, value.amount, value.identity) }
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
            (waveResources ? "\n" + resourceSummary(node.resources) + "\n" + resourceScaleSummary(node) : "")
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
            let perRadian = CGFloat(indices.count) / (.pi * 2)
            return indices.map { index -> CGFloat in
                let name = label(nodes[index].name)
                let titleWidth = textLayouts.width(name, fontSize: size, mode: .arc)
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
        let drawingStarted = ProcessInfo.processInfo.systemUptime
        defer {
            if observationTime == nil {
                drawnFrames &+= 1
                let elapsed = ProcessInfo.processInfo.systemUptime - drawingStarted
                if elapsed.isFinite, elapsed > 0 {
                    drawCostEMA = drawCostEMA.map { $0 * 0.85 + elapsed * 0.15 } ?? elapsed
                }
            }
        }
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
        let time = reducedMotion ? 0 : CGFloat(visualNow)
        for (ringIndex, ring) in rings.enumerated() {
            drawWaves(center, ring: ring, inner: ringIndex > 0, now: visualNow, reducedMotion: reducedMotion)
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
                    let age = outputMarks[node.key]?.last.map { visualNow - $0.time }
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
        guard waveStyle != "off", waveResources else { return }
        drawResourceWaves(center, ring: ring, inner: inner, now: now, reducedMotion: reducedMotion)
    }


    static func resourceEnergy(_ rate: Double?, recentPeak: Double?, reference: Double,
                               sensitivity: Double = 1) -> CGFloat? {
        guard let rate = rate, rate.isFinite, rate >= 0, reference.isFinite, reference > 0,
              sensitivity.isFinite, sensitivity > 0 else { return nil }
        guard rate > 0 else { return 0 }
        let peak = recentPeak.flatMap { $0.isFinite && $0 > 0 ? $0 : nil } ?? rate
        let scale = max(reference, peak / 4)
        // Stable log(1 + rate * sensitivity / scale), including very large counters.
        let logRatio = log(rate) + log(sensitivity) - log(scale)
        let response = logRatio > 0 ? logRatio + log1p(exp(-logRatio)) : log1p(exp(logRatio))
        return CGFloat(response / (response + 0.72))
    }

    /// Geometry moves monotonically between heights backed by real data. A zero
    /// or unavailable target clears immediately instead of leaving a fake tail.
    static func interpolatedWaveHeight(from: CGFloat?, to target: CGFloat?, elapsed: TimeInterval) -> CGFloat? {
        guard let target = target, target.isFinite, target >= 0 else { return nil }
        guard target > 0 else { return 0 }
        let beginning = from.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil } ?? target
        let progress = elapsed.isFinite ? min(1, max(0, elapsed / waveTransitionDuration)) : 1
        let eased = CGFloat(progress * progress * (3 - 2 * progress))
        return beginning + (target - beginning) * eased
    }

    func presentedHeight(_ target: CGFloat, node: String, role: ResourceRole,
                         bucket: WaveBucket, now: TimeInterval, reducedMotion: Bool) -> CGFloat {
        let key = GeometryKey(node: node, role: role, bucketStart: bucket.startTime)
        guard target > 0 else { waveTransitions.removeValue(forKey: key); return 0 }
        guard !reducedMotion, observationTime == nil else { waveTransitions.removeValue(forKey: key); return target }
        if let transition = waveTransitions[key] {
            if abs(transition.target - target) > 0.01 {
                let current = transition.height(at: now)
                let next = GeometryTransition(from: current, target: target, startedAt: now)
                waveTransitions[key] = next
                return current
            }
            return transition.height(at: now)
        }
        let age = bucket.newest.map { now - $0 } ?? .infinity
        let isNewEvent = age >= 0 && age < Self.waveTransitionDuration
        let beginning = isNewEvent ? target * 0.35 : target
        let transition = GeometryTransition(from: beginning, target: target,
                                            startedAt: bucket.newest ?? now - Self.waveTransitionDuration)
        waveTransitions[key] = transition
        return transition.height(at: now)
    }

    static func resourceReference(_ role: ResourceRole) -> Double {
        switch role {
        case .cpu: return 100
        case .gpu: return 100
        case .disk: return 1_048_576
        case .network: return 4_096
        case .output: return 40
        }
    }

    /// One pass assigns genuine positive events to the shared old-to-new timeline.
    static func resourceBuckets(_ events: [WaveEvent], now: TimeInterval, count: Int,
                                maximumDuration: TimeInterval = 5) -> [WaveBucket] {
        guard now.isFinite, (1...128).contains(count), maximumDuration.isFinite,
              maximumDuration >= 0, maximumDuration <= 24 else { return [] }
        let width = 24 / Double(count)
        let end = floor(now / width) * width + width
        let start = end - 24
        var buckets = (0..<count).map { WaveBucket(startTime: start + Double($0) * width) }
        for event in events {
            guard event.time.isFinite, event.amount.isFinite, event.amount > 0, event.duration.isFinite, event.duration >= 0, event.duration <= maximumDuration,
                  event.time <= now, now - event.time < 24, event.time >= start else { continue }
            if event.duration > 0 {
                let observedStart = event.time - event.duration
                for index in buckets.indices {
                    let overlap = min(event.time, buckets[index].startTime + width) - max(observedStart, buckets[index].startTime)
                    guard overlap > 0 else { continue }
                    let addition = event.amount * (overlap / event.duration) / width
                    let total = buckets[index].rate + addition
                    buckets[index].rate = total.isFinite ? total : Double.greatestFiniteMagnitude
                    buckets[index].newest = max(buckets[index].newest ?? event.time, event.time)
                }
                continue
            }
            let index = Int(floor((event.time - start) / width))
            guard buckets.indices.contains(index) else { continue }
            let rate = event.pointValue ? event.amount : event.amount / width
            let total = event.pointValue ? max(buckets[index].rate, rate) : buckets[index].rate + rate
            buckets[index].rate = total.isFinite ? total : Double.greatestFiniteMagnitude
            buckets[index].newest = max(buckets[index].newest ?? event.time, event.time)
        }
        return buckets
    }

    /// Read-only signal profiles are also used by deterministic screenshot fixtures.
    /// Current coverage and already observed history are independent. Unknown
    /// channels retain only real marks until their original 24-second expiry.
    func resourceSignals(for key: String, count: Int = 30, now: TimeInterval? = nil) -> [ResourceRole: [WaveBucket]] {
        guard let node = nodes.first(where: { $0.key == key }), node.running else { return [:] }
        let clock = now ?? visualNow
        var result: [ResourceRole: [WaveBucket]] = [:]
        for role in Self.dynamicResourceRoles {
            let marks = role == .output ? dialogueMarks[key] ?? [] : resourceMarks[key]?[role] ?? []
            let known: Bool
            switch role {
            case .cpu: known = node.resources.cpuPercent != nil
            case .disk: known = node.resources.diskKnown
            case .gpu: known = node.resources.gpuPercent != nil
            case .network: known = node.resources.networkKnown
            case .output: known = node.resources.outputKnown && (node.resources.outputFreshUntil ?? 0) >= clock
            }
            let hasHistory = marks.contains { $0.amount > 0 && clock - $0.time >= 0 && clock - $0.time < 24 }
            guard known || hasHistory else { continue }
            let events: [WaveEvent]
            if role == .gpu {
                // Sum independent roots from one GPU sample, then retain the
                // peak observed sample in a visual bin; density cannot rescale %.
                let samples = Dictionary(grouping: marks, by: { $0.sampleKey ?? $0.identity + ":" + String($0.time) })
                events = samples.values.compactMap { sample in
                    guard let time = sample.map(\.time).max() else { return nil }
                    return WaveEvent(time: time, amount: sample.reduce(0) { $0 + $1.amount }, pointValue: true)
                }
            } else {
                events = marks.map { WaveEvent(time: $0.time, amount: $0.amount, duration: $0.duration) }
            }
            // Dialogue marks have already passed their configured continuity
            // check. Retain them if the user later shortens the polling period.
            result[role] = Self.resourceBuckets(events, now: clock, count: count,
                maximumDuration: role == .output ? Self.maximumDialogueObservationWindow : 5)
        }
        return result
    }

    func hasPendingToolMarker(for key: String) -> Bool {
        guard let node = nodes.first(where: { $0.key == key }), node.running else { return false }
        return node.resources.toolsKnown && (node.resources.activeTools ?? 0) > 0
    }

    private func resourceBucketCount(inner: Bool) -> Int {
        max(8, min(inner ? 30 : 56, Int((inner ? 19 : 30) * waveDensity)))
    }

    private func resourceScaleSummary(_ node: Node) -> String {
        guard node.running else { return "声浪已停止；仅运行中的会话显示活动。" }
        let inner = rings.dropFirst().contains { ring in ring.indices.contains { nodes[$0].key == node.key } }
        let profiles = resourceSignals(for: node.key, count: resourceBucketCount(inner: inner))
        let values = Self.dynamicResourceRoles.map { role -> String in
            guard let peak = profiles[role]?.map(\.rate).max() else { return role.title + "未观测" }
            let amount: String
            switch role {
            case .disk, .network: amount = byteAmount(peak) + "/s"
            case .cpu, .gpu: amount = ResourceMetrics.cpu(peak)
            case .output: amount = String(format: "%.1f 观测字符/秒（非Token）", peak)
            }
            return role.title + " " + amount
        }
        return "近24秒桶峰值：" + values.joined(separator: " · ") +
            "\n各通道整窗共享独立柔和标尺；高度不用于跨单位比较，百分比和字节速率不能直接比较。" +
            "\nCPU 单核100%；GPU 为已关联进程 SM 进程采样合计；磁盘为实际读写，网络为可归属 TCP；对话输出仅新增助手正文。"
    }

    /// Every channel uses this same geometry. The style selects the whole
    /// layered wave's appearance; channel identity supplies only its color.
    static func drawWaveProfile(_ heights: [CGFloat], center: NSPoint, radius: CGFloat,
                                theta: CGFloat, span: CGFloat, style: String,
                                color: NSColor, inner: Bool) {
        guard radius.isFinite, radius > 0, theta.isFinite, span.isFinite,
              !heights.isEmpty, heights.count <= 128,
              ["bars", "plasma", "aurora", "radar", "curve", "dots"].contains(style) else { return }
        let values = heights.map { $0.isFinite && $0 > 0 ? min($0, radius) : 0 }
        guard values.contains(where: { $0 > 0 }) else { return }
        func position(_ r: CGFloat, _ angle: CGFloat) -> NSPoint {
            NSPoint(x: center.x + cos(angle) * r, y: center.y + sin(angle) * r)
        }
        func direction(_ index: Int) -> CGFloat {
            theta + span * (0.4 - 0.8 * CGFloat(index) / CGFloat(max(1, values.count - 1)))
        }
        func stroke(_ path: NSBezierPath, width: CGFloat, alpha: CGFloat, round: Bool = false) {
            color.withAlphaComponent(alpha).setStroke()
            path.lineWidth = width; path.lineCapStyle = round ? .round : .butt
            path.lineJoinStyle = .round; path.stroke()
        }
        let baseWidth: CGFloat = inner ? 0.65 : 1.15
        if style == "aurora" || style == "curve" {
            // Positive runs are independent paths. No curve crosses a quiet or
            // unobserved bucket, and controls stay inside adjacent sample peaks.
            let half = span * 0.8 / CGFloat(max(1, values.count - 1)) * 0.38
            var first = 0
            while first < values.count {
                guard values[first] > 0 else { first += 1; continue }
                var last = first
                while last + 1 < values.count && values[last + 1] > 0 { last += 1 }
                let from = direction(first) + half, to = direction(last) - half
                let crest = NSBezierPath()
                crest.move(to: position(radius + values[first], from))
                crest.line(to: position(radius + values[first], direction(first)))
                if first < last {
                    for index in (first + 1)...last {
                        let middle = (direction(index - 1) + direction(index)) / 2
                        crest.curve(to: position(radius + values[index], direction(index)),
                                    controlPoint1: position(radius + values[index - 1], middle),
                                    controlPoint2: position(radius + values[index], middle))
                    }
                }
                crest.line(to: position(radius + values[last], to))
                if style == "aurora" {
                    let band = NSBezierPath()
                    band.append(crest)
                    band.line(to: position(radius, to))
                    band.appendArc(withCenter: center, radius: radius, startAngle: to * 180 / .pi,
                                   endAngle: from * 180 / .pi, clockwise: false)
                    band.close()
                    color.withAlphaComponent(0.15).setFill(); band.fill()
                    stroke(crest, width: baseWidth * 4.8, alpha: 0.07, round: true)
                    stroke(crest, width: baseWidth * 1.1, alpha: 0.72, round: true)
                } else {
                    stroke(crest, width: baseWidth * 0.9, alpha: 0.9, round: true)
                }
                first = last + 1
            }
            return
        }
        for index in values.indices where values[index] > 0 {
            let angle = direction(index), height = values[index]
            let tip = position(radius + height, angle)
            let column = NSBezierPath()
            column.move(to: position(radius, angle)); column.line(to: tip)
            switch style {
            case "bars":
                stroke(column, width: baseWidth * 1.5, alpha: 0.72)
                let tangent = NSPoint(x: -sin(angle), y: cos(angle))
                let half = baseWidth * 0.9
                let cap = NSBezierPath()
                cap.move(to: NSPoint(x: tip.x - tangent.x * half, y: tip.y - tangent.y * half))
                cap.line(to: NSPoint(x: tip.x + tangent.x * half, y: tip.y + tangent.y * half))
                stroke(cap, width: baseWidth * 0.7, alpha: 0.98)
            case "plasma":
                // Bounded extra strokes glow only around measured columns.
                stroke(column, width: baseWidth * 5, alpha: 0.07, round: true)
                stroke(column, width: baseWidth * 2.9, alpha: 0.16, round: true)
                stroke(column, width: baseWidth * 1.1, alpha: 0.88, round: true)
                let diameter = baseWidth * 1.5
                color.withAlphaComponent(0.98).setFill()
                NSBezierPath(ovalIn: NSRect(x: tip.x - diameter / 2, y: tip.y - diameter / 2,
                                           width: diameter, height: diameter)).fill()
            case "radar":
                stroke(column, width: baseWidth * 0.6, alpha: 0.65)
                let half: CGFloat = inner ? 0.85 : 1.55
                let diamond = NSBezierPath()
                for (offset, angular) in [(half, CGFloat(0)), (0, half / radius), (-half, 0), (0, -half / radius)] {
                    let vertex = position(radius + height + offset, angle + angular)
                    if diamond.isEmpty { diamond.move(to: vertex) } else { diamond.line(to: vertex) }
                }
                diamond.close()
                color.withAlphaComponent(0.13).setFill(); diamond.fill()
                stroke(diamond, width: baseWidth * 0.7, alpha: 0.95)
            case "dots":
                let diameter: CGFloat = inner ? 1.6 : 2.6
                color.withAlphaComponent(0.9).setFill()
                NSBezierPath(ovalIn: NSRect(x: tip.x - diameter / 2, y: tip.y - diameter / 2,
                                           width: diameter, height: diameter)).fill()
            default: break
            }
        }
    }

    private func drawResourceWaves(_ center: NSPoint, ring: Ring, inner: Bool, now: TimeInterval,
                                   reducedMotion: Bool) {
        let span = CGFloat.pi * 2 / CGFloat(max(1, ring.indices.count))
        let radius = ring.radius + Self.resourceBaselineOffset(inner: inner)
        let count = resourceBucketCount(inner: inner)
        let maximum: CGFloat = inner ? 4.2 : 12
        let style = ThemePalette.waveStyle(waveStyle)
        for (slot, nodeIndex) in ring.indices.enumerated() {
            let node = nodes[nodeIndex]
            guard node.running else { continue }
            let theta = angle(slot, ring)
            let profiles = resourceSignals(for: node.key, count: count, now: now)
            // One baseline, renderer and geometry for every role. Data sampling
            // semantics stay independent; transparency keeps overlapping colors visible.
            for role in [ResourceRole.cpu, .disk, .network, .gpu, .output] {
                guard let buckets = profiles[role], let peak = buckets.map(\.rate).max(), peak > 0 else { continue }
                let heights: [CGFloat] = buckets.map { bucket in
                    guard bucket.rate > 0, let energy = Self.resourceEnergy(bucket.rate, recentPeak: peak,
                          reference: Self.resourceReference(role), sensitivity: Double(waveSensitivity)) else { return 0 }
                    return presentedHeight(max(0.2, energy * maximum * waveHeight), node: node.key,
                                           role: role, bucket: bucket, now: now, reducedMotion: reducedMotion)
                }
                Self.drawWaveProfile(heights, center: center, radius: radius, theta: theta, span: span,
                                     style: style, color: ThemePalette.resourceColor(role), inner: inner)
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
        let hasRecentOutput = outputMarks.values.contains { $0.last.map { visualNow - $0.time < 2.5 } == true }
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
            return amounts.map { prefix + $0 + suffix }.first {
                textLayouts.width($0, fontSize: size) <= tokenWidth
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
        let now = visualNow
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
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        textLayouts.drawArc(text, context: context, center: center, radius: radius,
                            angle: angle, fontSize: size, color: color)
    }

    private func drawText(_ text: String, rect: NSRect, size: CGFloat, color: NSColor) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        textLayouts.draw(text, context: context, rect: rect, fontSize: size, color: color)
    }

    private func drawFittedText(_ text: String, rect: NSRect, size: CGFloat, minimumSize: CGFloat, color: NSColor) {
        guard OrbitTextLayoutCache.validRect(rect), OrbitTextLayoutCache.validFontSize(size),
              OrbitTextLayoutCache.validFontSize(minimumSize) else { return }
        let width = textLayouts.width(text, fontSize: size)
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
        let guide = menu.addItem(withTitle: "声浪图说明…", action: #selector(openWaveGuide(_:)), keyEquivalent: "")
        guide.target = self
        guide.identifier = NSUserInterfaceItemIdentifier("orbit.waveGuide")
        let open = menu.addItem(withTitle: "打开面板", action: #selector(openPanel(_:)), keyEquivalent: "")
        open.target = self
        let toggle = menu.addItem(withTitle: miniMode ? "放大为完整会话环" : "缩小为小号悬浮球", action: #selector(toggleSize(_:)), keyEquivalent: "")
        toggle.target = self
        return menu
    }

    @objc private func openWaveGuide(_ sender: Any?) {
        NotificationCenter.default.post(name: Notification.Name("AgentOrbit.OpenWaveGuide"), object: nil)
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
