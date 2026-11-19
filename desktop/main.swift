import AppKit
import SwiftUI

struct Pane: Decodable, Identifiable {
    var paneId: String
    var session: String
    var state: String
    var status: String
    var preview: [String]
    var working: String?
    var completedAt: Double?
    var completionId: String?
    var errorSummary: String?
    var errorKind: String?
    var attentionLevel: String?
    var agent: String?
    var errorSignature: String?
    var activityLevel: Double?
    var outputRate: Double?
    var outputTotal: Int?
    var activityEpoch: Int?
    var activityAge: Double?
    var activitySource: String?
    var source: String?
    var sourceLabel: String?
    var available: Bool?
    var groupId: String?
    var threadId: String?
    var monitorKey: String { groupId ?? session }
    var orbitName: String { source == "local" ? "本·" + String(session.prefix(12)) + "·" + String((threadId ?? paneId).suffix(4)) : session }
    var backgroundCount: Int?
    var backgroundAgents: Int?
    var backgroundTerminals: Int?
    var backgroundAgentsTotal: Int?
    var backgroundObservable: Bool?
    var backgroundAgentsObservable: Bool?
    var backgroundTerminalsObservable: Bool?
    var backgroundChecking: Bool?
    var workingIsBackgroundCount: Bool {
        working == "后台终端 \(backgroundTerminals ?? 0) · 子智能体 \(backgroundAgents ?? 0)"
    }
    var backgroundCountsText: String? {
        guard agent == "claude" || agent == "codex" else { return nil }
        if backgroundChecking == true { return "后台数量核对中…" }
        let agentsKnown = backgroundAgentsObservable ?? backgroundObservable ?? false
        if source == "local" { return agentsKnown ? "子智能体 \(backgroundAgents ?? 0)/\(backgroundAgentsTotal ?? 0) 运行" : nil }
        let terminalsKnown = backgroundTerminalsObservable ?? backgroundObservable ?? false
        var agents = agentsKnown ? "\(backgroundAgents ?? 0)" : "待核实"
        if agentsKnown, let total = backgroundAgentsTotal { agents += "/\(total) 运行" }
        let terminals = terminalsKnown ? "\(backgroundTerminals ?? 0)" : "待核实"
        return "子智能体 \(agents) · 保留终端 \(terminals)"
    }
    var isCritical: Bool { attentionLevel == "error" || ["error", "interrupted", "stopped", "missing"].contains(state) }
    var needsAttention: Bool { isCritical || attentionLevel == "warning" || ["approval", "input", "paused", "unknown"].contains(state) }
    var id: String { paneId }
}

struct Completion: Decodable, Identifiable {
    var id: String
    var paneId: String
    var session: String
    var completedAt: Double
}

struct SourceConnection: Decodable, Identifiable {
    var id: String
    var label: String
    var connected: Bool
    var connectionState: String
    var message: String
}
struct LocalSession: Decodable, Identifiable {
    var threadId: String?
    var session: String
    var id: String { threadId ?? session }
}

struct Snapshot: Decodable {
    var rows: [Pane]
    var events: [Completion]
    var errorEvents: [ErrorEvent]?
    var activeErrorEvents: [ErrorEvent]?
    var followed: [String]
    var connected: Bool
    var message: String
    var host: String
    var receivedFrames: Int
    var connectionState: String?
    var connections: [SourceConnection]?
    var localSessions: [LocalSession]?
}

struct ErrorEvent: Decodable, Identifiable {
    var id: String
    var paneId: String
    var session: String
    var summary: String
    var occurredAt: Double
    var kind: String?
    var severity: String?
    var signature: String?
    var source: String?
    var isCritical: Bool { severity != "warning" }
}

func shellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
}

func argument(_ name: String, fallback: String) -> String {
    guard let i = CommandLine.arguments.firstIndex(of: name), i + 1 < CommandLine.arguments.count else { return fallback }
    return CommandLine.arguments[i + 1]
}

final class PanelModel: ObservableObject {
    @Published var settings: AppSettings
    @Published var connections: [SourceConnection] = []
    @Published var localSessions: [LocalSession] = []
    @Published var rows: [Pane] = []
    @Published var followed: [String] = []
    @Published var connectionState = "connecting"
    @Published var connected = false
    @Published var message = "正在连接…"
    @Published var unread: [Completion] = []
    @Published var unreadErrors: [ErrorEvent] = []
    @Published var sound = UserDefaults.standard.object(forKey: "sound") as? Bool ?? true {
        didSet { settings.sound = sound; persistSettings(); onUpdate?() }
    }
    @Published var pinned = UserDefaults.standard.object(forKey: "pinned") as? Bool ?? true {
        didSet {
            settings.pinned = pinned; persistSettings()
            onPin?(pinned)
        }
    }
    var onSettings: (() -> Void)?
    var onPin: ((Bool) -> Void)?
    var onMinimize: (() -> Void)?
    var onUpdate: (() -> Void)?
    var onCompletion: (() -> Void)?
    var onError: (() -> Void)?
    let root: URL
    let dataRoot: URL
    var host: String { settings.host.isEmpty ? "本机" : settings.host }
    var socket: String { settings.tmuxSocket }
    private var process: Process?
    private var pipe: Pipe?
    private var errorPipe: Pipe?
    private var seen = Set<String>()
    private var seenErrors = Set<String>()
    private var dismissedErrors = Set<String>()
    private var timer: Timer?
    private var lastPacket = Date()
    private var stopped = false
    private var localOutageAlerted = false
    private let readQueue = DispatchQueue(label: "codex-remote.json")
    private var buffer = Data()
    var connectionNeedsAttention: Bool {
        ["config_error", "monitor_error", "remote_error", "reconnecting", "partial"].contains(connectionState)
    }
    var criticalCount: Int {
        Set(rows.filter { $0.isCritical && connected && $0.available != false }.map(\.paneId) + unreadErrors.filter { $0.isCritical && !isResolved($0) }.map(\.paneId)).count
    }
    var warningCount: Int {
        Set(rows.filter { $0.needsAttention && !$0.isCritical && connected && $0.available != false }.map(\.paneId) + unreadErrors.filter { !$0.isCritical && !isResolved($0) }.map(\.paneId)).count
    }

    func isResolved(_ event: ErrorEvent) -> Bool {
        guard connected else { return false }
        if event.paneId.hasSuffix("__connection__") {
            return connections.first(where: { $0.id == event.source })?.connected ?? connected
        }
        if event.paneId == "__local__" { return true }
        guard let pane = rows.first(where: { $0.paneId == event.paneId }) ?? rows.first(where: { $0.session == event.session }) else {
            return event.kind == "question" || event.kind == "approval"
        }
        guard pane.available != false else { return false }
        if event.kind == "unknown" { return pane.state != "unknown" }
        return !pane.needsAttention || pane.errorKind != event.kind ||
            (event.signature?.isEmpty == false && pane.errorSignature != event.signature)
    }

    init() {
        let bundled = Bundle.main.resourceURL?.appendingPathComponent("engine")
        root = URL(fileURLWithPath: argument("--project", fallback: bundled?.path ?? Bundle.main.bundleURL.deletingLastPathComponent().path))
        let support = NSHomeDirectory() + "/Library/Application Support/"
        let preferred = support + "Agent Orbit", legacyData = support + "Codex Remote"
        let demoMode = CommandLine.arguments.contains("--demo") || CommandLine.arguments.contains("--export-demo")
        let defaultData = demoMode ? FileManager.default.temporaryDirectory.appendingPathComponent("agent-orbit-demo-\(ProcessInfo.processInfo.processIdentifier)").path : FileManager.default.fileExists(atPath: legacyData) && !FileManager.default.fileExists(atPath: preferred) ? legacyData : preferred
        dataRoot = URL(fileURLWithPath: argument("--data-dir", fallback: demoMode ? defaultData : ProcessInfo.processInfo.environment["AGENT_ORBIT_DATA_DIR"] ?? ProcessInfo.processInfo.environment["CODEX_REMOTE_DATA_DIR"] ?? defaultData))
        var initial = AppSettings()
        let manager = FileManager.default
        let legacy = root.appendingPathComponent(demoMode ? ".demo-no-legacy-follow" : "follow.json")
        if manager.fileExists(atPath: legacy.path) {
            initial.remoteEnabled = true; initial.host = "myserver"; initial.tmuxSocket = ProcessInfo.processInfo.environment["CODEX_TMUX_SOCKET"] ?? ""
            initial.sound = UserDefaults.standard.object(forKey: "sound") as? Bool ?? true
            initial.pinned = UserDefaults.standard.object(forKey: "pinned") as? Bool ?? true
        }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        if let data = try? Data(contentsOf: dataRoot.appendingPathComponent("settings.json")), let saved = try? decoder.decode(AppSettings.self, from: data) { initial = saved }
        if CommandLine.arguments.contains("--host") { initial.host = argument("--host", fallback: initial.host); initial.remoteEnabled = !initial.host.isEmpty }
        if CommandLine.arguments.contains("--socket") { initial.tmuxSocket = argument("--socket", fallback: initial.tmuxSocket) }
        settings = initial
        ThemePalette.current = initial.theme
        sound = initial.sound; pinned = initial.pinned
        try? manager.createDirectory(at: dataRoot, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let followURL = dataRoot.appendingPathComponent("follow.json")
        if !manager.fileExists(atPath: followURL.path) {
            let data = (try? Data(contentsOf: legacy)) ?? Data("[]".utf8)
            try? data.write(to: followURL, options: .atomic)
        }
        if let data = try? Data(contentsOf: followURL), let names = try? JSONDecoder().decode([String].self, from: data) { followed = names }
        if !manager.fileExists(atPath: dataRoot.appendingPathComponent("settings.json").path) || CommandLine.arguments.contains("--host") { persistSettings() }
    }

    func persistSettings() {
        if let data = try? settings.encoded() { try? data.write(to: dataRoot.appendingPathComponent("settings.json"), options: .atomic) }
    }

    func saveSettings(_ value: AppSettings, follow: String) throws {
        try value.validate()
        let encoded = try value.encoded()
        try saveFollow(follow)
        try encoded.write(to: dataRoot.appendingPathComponent("settings.json"), options: .atomic)
        ThemePalette.current = value.theme
        settings = value; sound = value.sound; pinned = value.pinned
        onUpdate?()
    }

    func start() {
        lastPacket = Date()
        connectionState = "connecting"
        readQueue.async { [weak self] in self?.buffer = Data() }
        let child = Process()
        child.executableURL = Bundle.main.resourceURL?.appendingPathComponent("python/bin/python3")
        child.arguments = ["-u", root.appendingPathComponent("desktop_bridge.py").path]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        environment.removeValue(forKey: "PYTHONHOME")
        environment.removeValue(forKey: "PYTHONPATH")
        environment["CODEX_REMOTE_DATA_DIR"] = dataRoot.path
        environment["AGENT_ORBIT_DATA_DIR"] = dataRoot.path
        // Finder launches do not inherit a login shell's search path.
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
        child.environment = environment
        let output = Pipe()
        let errors = Pipe()
        child.standardOutput = output
        child.standardError = errors
        child.standardInput = FileHandle.nullDevice
        process = child
        pipe = output
        errorPipe = errors
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            self?.readQueue.async { [weak self] in self?.consume(data) }
        }
        errors.fileHandleForReading.readabilityHandler = { handle in
            // Drain stderr so a failing child cannot block on a full pipe.
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
        child.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, !self.stopped else { return }
                self.connected = false
                self.connectionState = "reconnecting"
                self.message = "连接已退出，正在重试…"
                self.onUpdate?()
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    if !self.stopped { self.start() }
                }
            }
        }
        do {
            try child.run()
        } catch {
            connected = false
            connectionState = "monitor_error"
            message = "无法启动监控：\(error.localizedDescription)"
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                guard let self = self, !self.stopped else { return }
                self.start()
            }
        }
        if timer == nil {
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                if Date().timeIntervalSince(self.lastPacket) > 15 {
                    self.connected = false
                    self.connectionState = "monitor_error"
                    self.message = "本地监控无响应，正在恢复…"
                    if !self.localOutageAlerted {
                        self.localOutageAlerted = true
                        self.unreadErrors.insert(ErrorEvent(id: UUID().uuidString, paneId: "__local__", session: "本地监控", summary: "监控程序无响应，远端状态暂不可确认", occurredAt: Date().timeIntervalSince1970, kind: "monitor", severity: "error"), at: 0)
                        if self.sound && self.settings.errorSound { NSSound(named: NSSound.Name("Basso"))?.play() }
                    }
                    if Date().timeIntervalSince(self.lastPacket) > 20 && self.process?.isRunning == true {
                        self.process?.terminate()
                    }
                    self.onUpdate?()
                }
            }
        }
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        while let range = buffer.range(of: Data([10])) {
            let line = buffer.subdata(in: 0..<range.lowerBound)
            buffer.removeSubrange(0..<range.upperBound)
            guard let snapshot = try? decoder.decode(Snapshot.self, from: line) else { continue }
            DispatchQueue.main.async { [weak self] in self?.apply(snapshot) }
        }
    }

    func apply(_ snapshot: Snapshot) {
        lastPacket = Date()
        localOutageAlerted = false
        connections = snapshot.connections ?? []
        localSessions = snapshot.localSessions ?? []
        rows = snapshot.rows
        followed = snapshot.followed
        connected = snapshot.connected
        connectionState = snapshot.connectionState ?? (snapshot.connected ? "live" : "reconnecting")
        message = snapshot.message
        let fresh = snapshot.events.filter { !seen.contains($0.id) }
        for event in fresh { seen.insert(event.id) }
        if !fresh.isEmpty {
            let paneIds = Set(fresh.map(\.paneId))
            unread.removeAll { paneIds.contains($0.paneId) }
            unread.insert(contentsOf: fresh.reversed(), at: 0)
            unread = Array(unread.prefix(100))
            onCompletion?()
        }
        unread.removeAll { event in
            !rows.contains { $0.paneId == event.paneId && $0.state == "idle" && $0.completionId == event.id }
        }
        let incomingErrors = (snapshot.errorEvents ?? []).filter { !seenErrors.contains($0.id) }
        for event in incomingErrors { seenErrors.insert(event.id) }
        let freshErrors = incomingErrors.filter { !(($0.kind == "question" || $0.kind == "approval") && isResolved($0)) }
        if !freshErrors.isEmpty {
            onError?()
        }
        // The delivery queue drives sounds; the active list drives cards.
        // Resolved incidents never become history cards in the panel.
        let localAlerts = unreadErrors.filter { $0.paneId == "__local__" && !connected }
        let activeErrors = snapshot.activeErrorEvents ?? (unreadErrors + freshErrors).filter { !isResolved($0) }
        if !activeErrors.isEmpty || !unreadErrors.isEmpty {
            var latest: [String: ErrorEvent] = [:]
            for event in (activeErrors + localAlerts).filter({ !dismissedErrors.contains($0.id) }).sorted(by: { $0.occurredAt < $1.occurredAt }) {
                let source = event.session == "本地监控" || event.session == "监控连接" ? event.session : event.paneId
                latest[source] = event
            }
            unreadErrors = Array(latest.values.sorted(by: { $0.occurredAt > $1.occurredAt }).prefix(20))
        }
        unreadErrors.removeAll { isResolved($0) }
        // One sound per update; interruptions take priority over completions.
        if sound && ((!freshErrors.isEmpty && settings.errorSound) || (!fresh.isEmpty && settings.completionSound)) {
            let critical = settings.errorSound && freshErrors.contains(where: \.isCritical)
            NSSound(named: NSSound.Name(critical ? "Basso" : "Glass"))?.play()
        }
        onUpdate?()
    }

    func acknowledge(_ pane: Pane) {
        dismissedErrors.formUnion(unreadErrors.filter { $0.paneId == pane.paneId }.map(\.id))
        unread.removeAll { $0.paneId == pane.paneId }
        unreadErrors.removeAll { $0.paneId == pane.paneId }
        onUpdate?()
    }

    func clearAlerts() {
        dismissedErrors.formUnion(unreadErrors.map(\.id))
        unread.removeAll()
        unreadErrors.removeAll()
        onUpdate?()
    }

    func saveFollow(_ text: String) throws {
        let names = text.components(separatedBy: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",，、")))
            .filter { !$0.isEmpty }
        var unique: [String] = []
        for name in names where !unique.contains(name) { unique.append(name) }
        let data = try JSONSerialization.data(withJSONObject: unique, options: [.prettyPrinted, .withoutEscapingSlashes])
        try data.write(to: dataRoot.appendingPathComponent("follow.json"), options: .atomic)
        followed = unique
    }

    func open(_ pane: Pane) {
        guard pane.state != "missing" else { return }
        if pane.source == "local" {
            if let application = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.openai.codex") ?? NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.openai.chat") {
                NSWorkspace.shared.open(application)
            } else { message = "请在本机会话所属的 Codex 客户端中查看" }
            acknowledge(pane)
            return
        }
        let command = [root.appendingPathComponent("codex-remote").path, "--host", host, "--socket", socket, "attach", pane.session]
            .map(shellQuote).joined(separator: " ")
        // Pass the command as argv: never interpolate a session name into AppleScript.
        let source = "on run argv\ntell application \"Terminal\"\nactivate\ndo script (item 1 of argv)\nend tell\nend run"
        let launcher = Process()
        launcher.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        launcher.arguments = ["-e", source, command]
        launcher.standardOutput = FileHandle.nullDevice
        launcher.standardError = FileHandle.nullDevice
        do { try launcher.run(); acknowledge(pane) }
        catch { message = "无法打开终端：\(error.localizedDescription)" }
    }

    func stop() {
        stopped = true
        timer?.invalidate()
        pipe?.fileHandleForReading.readabilityHandler = nil
        errorPipe?.fileHandleForReading.readabilityHandler = nil
        if process?.isRunning == true { process?.terminate() }
    }
}

final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let model = PanelModel()
    var panel: FloatingPanel!
    var statusItem: NSStatusItem!
    var bubble: NSPanel!
    var bubbleView: BubbleView!
    var settingsWindow: NSWindow?
    var demoTimer: Timer?
    private var minimizedByUser = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        if CommandLine.arguments.contains("--export-demo") {
            do { try DemoSupport.export(model, folder: URL(fileURLWithPath: argument("--export-demo", fallback: "/tmp/agent-orbit-demo"))) }
            catch { fputs("Demo export failed: \(error)\n", stderr) }
            NSApp.terminate(nil); return
        }
        if CommandLine.arguments.contains("--demo") { DemoSupport.prepare(model) }
        if !UserDefaults.standard.bool(forKey: "agentOrbitPreferencesMigrated"), let old = UserDefaults(suiteName: "local.zcy.codex-remote.panel") {
            for key in ["bubbleOrigin", "compactOrbitPanelV1", "NSWindow Frame CodexRemoteFloatingPanel"] {
                if let value = old.object(forKey: key) { UserDefaults.standard.set(value, forKey: key) }
            }
            UserDefaults.standard.set(true, forKey: "agentOrbitPreferencesMigrated")
        }
        let mainMenu = NSMenu()
        let edit = NSMenuItem(); edit.title = "编辑"; edit.submenu = NSMenu(title: "编辑")
        for (title, action, key) in [("撤销", Selector(("undo:")), "z"), ("剪切", #selector(NSText.cut(_:)), "x"), ("复制", #selector(NSText.copy(_:)), "c"), ("粘贴", #selector(NSText.paste(_:)), "v"), ("全选", #selector(NSText.selectAll(_:)), "a")] {
            edit.submenu?.addItem(withTitle: title, action: action, keyEquivalent: key)
        }
        let redo = NSMenuItem(title: "重做", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.submenu?.insertItem(redo, at: 1)
        mainMenu.addItem(edit); NSApp.mainMenu = mainMenu
        let menu = NSMenu()
        menu.addItem(withTitle: "显示悬浮窗", action: #selector(showPanel), keyEquivalent: "")
        menu.addItem(withTitle: "收起为悬浮球", action: #selector(minimizePanel), keyEquivalent: "m")
        menu.addItem(withTitle: "设置…", action: #selector(showSettings), keyEquivalent: ",")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "退出 Agent Orbit", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items { item.target = self }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.menu = menu
        statusItem.button?.title = "AO"
        panel = FloatingPanel(contentRect: NSRect(x: 0, y: 0, width: 320, height: 430),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView, .nonactivatingPanel],
                              backing: .buffered, defer: false)
        panel.title = "Agent Orbit"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.appearance = NSAppearance(named: .darkAqua)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.isFloatingPanel = true
        panel.level = model.pinned ? .floating : .normal
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.minSize = NSSize(width: 300, height: 260)
        panel.contentView = NSHostingView(rootView: PanelView(model: model).ignoresSafeArea(.container, edges: .top))
        for button in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            panel.standardWindowButton(button)?.isHidden = true
        }
        panel.delegate = self
        if let minimize = panel.standardWindowButton(.miniaturizeButton) {
            minimize.target = self
            minimize.action = #selector(minimizePanel)
            minimize.isEnabled = true
        }
        panel.setFrameAutosaveName("CodexRemoteFloatingPanel")
        if !panel.setFrameUsingName("CodexRemoteFloatingPanel"), let screen = NSScreen.main {
            let rect = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: rect.maxX - 414, y: rect.maxY - 594))
        }
        if !UserDefaults.standard.bool(forKey: "compactOrbitPanelV1") {
            let frame = panel.frame
            let size = NSSize(width: min(320, max(300, frame.width)), height: min(430, max(280, frame.height)))
            panel.setFrame(NSRect(x: frame.maxX - size.width, y: frame.maxY - size.height, width: size.width, height: size.height), display: false)
            UserDefaults.standard.set(true, forKey: "compactOrbitPanelV1")
        }
        bubble = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 176, height: 176),
                         styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        bubble.title = "Agent Orbit 悬浮球"
        bubble.isOpaque = false
        bubble.backgroundColor = .clear
        bubble.hasShadow = false
        bubble.hidesOnDeactivate = false
        bubble.acceptsMouseMovedEvents = true
        bubble.isReleasedWhenClosed = false
        bubble.isFloatingPanel = true
        bubble.level = .floating
        bubble.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        bubbleView = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176))
        bubbleView.restore = { [weak self] in self?.showPanel() }
        bubbleView.moved = { [weak self] in self?.keepBubbleOnScreen() }
        bubble.contentView = bubbleView
        bubbleView.sizeChanged = { [weak self] diameter in
            guard let self = self else { return }
            let frame = self.bubble.frame
            self.bubble.setFrame(NSRect(x: frame.midX - diameter / 2, y: frame.midY - diameter / 2, width: diameter, height: diameter), display: true)
            self.keepBubbleOnScreen()
        }
        if let saved = UserDefaults.standard.string(forKey: "bubbleOrigin") {
            bubble.setFrameOrigin(NSPointFromString(saved))
        } else {
            bubble.setFrameOrigin(NSPoint(x: panel.frame.maxX - 176, y: panel.frame.maxY - 176))
        }
        keepBubbleOnScreen()
        bubbleView.update(model)
        NotificationCenter.default.addObserver(self, selector: #selector(screensChanged), name: NSApplication.didChangeScreenParametersNotification, object: nil)
        model.onSettings = { [weak self] in self?.showSettings() }
        model.onPin = { [weak self] pinned in self?.panel.level = pinned ? .floating : .normal }
        model.onMinimize = { [weak self] in self?.minimizePanel() }
        model.onUpdate = { [weak self] in self?.updateStatus() }
        model.onCompletion = { [weak self] in
            // Keep the bubble visible without expanding it on every alert.
            guard let self = self, !self.minimizedByUser, !self.panel.isMiniaturized else { return }
            let panel = self.panel!
            panel.orderFrontRegardless()
        }
        model.onError = model.onCompletion
        if model.settings.launchCompact { minimizePanel() } else { showPanel() }
        if CommandLine.arguments.contains("--demo") {
            var step = 0
            model.apply(DemoSupport.snapshot(step))
            demoTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                step = (step + 1) % 24
                self?.model.apply(DemoSupport.snapshot(step))
            }
        } else { model.start() }
    }

    func updateStatus() {
        panel.alphaValue = model.settings.opacity; bubble.alphaValue = model.settings.opacity
        panel.level = model.pinned ? .floating : .normal; bubble.level = model.pinned ? .floating : .normal
        panel.appearance = NSAppearance(named: ThemePalette.light ? .aqua : .darkAqua)
        let idle = model.rows.filter { $0.state == "idle" && $0.available != false }.count
        let errors = model.criticalCount
        statusItem.button?.title = !model.connected ? (model.connectionState == "delayed" ? "AO …" : "AO !") : errors > 0 ? "AO !\(errors)" : model.warningCount > 0 ? "AO ?\(model.warningCount)" : "AO \(idle)"
        statusItem.button?.toolTip = !model.connected ? model.message : errors > 0 ? "\(errors) 个异常或中断待查看" : "\(idle) 个会话等待输入"
        bubbleView.update(model)
    }

    @objc func showSettings() {
        if let window = settingsWindow, window.isVisible { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 640), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Agent Orbit 设置"; window.isReleasedWhenClosed = false
        window.level = .floating
        window.contentView = NSHostingView(rootView: SettingsView(model: model) { [weak window] in window?.close() })
        window.center(); settingsWindow = window
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }

    @objc func showPanel() {
        minimizedByUser = false
        bubble.orderOut(nil)
        if panel.isMiniaturized { panel.deminiaturize(nil) }
        panel.orderFrontRegardless()
    }
    @objc func minimizePanel() {
        minimizedByUser = true
        panel.orderOut(nil)
        keepBubbleOnScreen()
        bubbleView.update(model)
        bubble.orderFrontRegardless()
        // A native titlebar minimize can reorder the panel after its action.
        DispatchQueue.main.async { [weak self] in
            guard let self = self, self.minimizedByUser else { return }
            self.panel.orderOut(nil)
            self.bubble.orderFrontRegardless()
        }
    }
    @objc func screensChanged() { keepBubbleOnScreen() }
    func keepBubbleOnScreen() {
        guard let bubble = bubble else { return }
        let frame = bubble.frame
        let center = NSPoint(x: frame.midX, y: frame.midY)
        let screen = NSScreen.screens.first { NSMouseInRect(center, $0.visibleFrame, false) } ?? NSScreen.main
        guard let area = screen?.visibleFrame.insetBy(dx: 4, dy: 4) else { return }
        bubble.setFrameOrigin(NSPoint(x: min(max(frame.minX, area.minX), area.maxX - frame.width),
                                      y: min(max(frame.minY, area.minY), area.maxY - frame.height)))
        UserDefaults.standard.set(NSStringFromPoint(bubble.frame.origin), forKey: "bubbleOrigin")
    }
    @objc func quit() { NSApp.terminate(nil) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showPanel(); return true }
    func applicationWillTerminate(_ notification: Notification) { model.stop() }
    func windowShouldClose(_ sender: NSWindow) -> Bool { minimizePanel(); return false }
    func windowDidMiniaturize(_ notification: Notification) {
        minimizedByUser = true
        panel.orderOut(nil)
        bubble.orderFrontRegardless()
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
