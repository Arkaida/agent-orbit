import AppKit
import SwiftUI

struct SessionResponse: Decodable, Equatable {
    var kind: String
    var episodeId: String?
    var since: Double?
    var elapsedSeconds: Double?
    var observed: Bool
    var overdue: Bool
    var scope: String?
    var idleSeconds: Double?
    var runSeconds: Double?
    var idleRunRatio: Double?
    var efficiencyPercent: Double?
    var reminderSuppressed: Bool?
    var isPending: Bool { kind == "waiting" || kind == "attention" }
    static func duration(_ elapsed: Double?) -> String {
        guard let elapsed = elapsed, elapsed.isFinite, elapsed >= 0 else { return "—" }
        let seconds = Int(min(elapsed, Double(Int.max / 2)))
        if seconds >= 3600 { return "\(seconds / 3600)时\(seconds % 3600 / 60)分" }
        return seconds >= 60 ? "\(seconds / 60)分\(seconds % 60)秒" : "\(seconds)秒"
    }
}

struct SessionResponseGroup: Decodable, Equatable {
    var id: String
    var name: String
    var members: [String]?
    var count: Int?
    var knownMembers: Int?
    var runningMembers: Int?
    var parallelConflict: Bool?
    var kind: String
    var episodeId: String?
    var since: Double?
    var elapsedSeconds: Double?
    var observed: Bool
    var overdue: Bool
    var scope: String?
    var idleSeconds: Double?
    var runSeconds: Double?
    var idleRunRatio: Double?
    var efficiencyPercent: Double?
    var reminderSuppressed: Bool?
    var response: SessionResponse {
        SessionResponse(kind: kind, episodeId: episodeId, since: since, elapsedSeconds: elapsedSeconds,
                        observed: observed, overdue: overdue, scope: scope, idleSeconds: idleSeconds,
                        runSeconds: runSeconds, idleRunRatio: idleRunRatio, efficiencyPercent: efficiencyPercent,
                        reminderSuppressed: reminderSuppressed)
    }
}

struct Pane: Decodable, Identifiable, Equatable {
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
    var response: SessionResponse?
    var responseGroup: SessionResponseGroup?
    var usageMetrics: UsageMetrics?
    var resources: ResourceMetrics?
    var manualRunning: Bool?
    var manualOverrideId: String?
    var resultMarker: String?
    var displayStatus: String { manualRunning == true && state == "running" ? "手动运行中" : status }
    var manualRunningHelp: String? {
        manualRunning == true ? "手动运行标记有效；收到新的回复结果后自动解除。错误或离线状态仍优先显示。" : nil
    }
    var effectiveResponse: SessionResponse? { responseGroup?.response ?? response }
    var responseMonitorKey: String { responseGroup.map { "group:" + $0.id } ?? (source ?? "remote") + ":" + monitorKey }
    var responseEpisodeKey: String? {
        guard let episode = effectiveResponse?.episodeId else { return nil }
        if let group = responseGroup { return "group:" + group.id + ":" + episode }
        return (source ?? "remote") + ":" + episode
    }
    var monitorKey: String { groupId ?? session }
    var orbitKey: String { responseGroup.map { "group:" + $0.id } ?? monitorKey }
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
    func visualState(connected: Bool) -> SessionVisualState {
        SessionVisualState.resolve(state: state, connected: connected, available: available, attentionLevel: attentionLevel)
    }
    var id: String { paneId }
}

struct Completion: Decodable, Identifiable, Equatable {
    var id: String
    var paneId: String
    var session: String
    var completedAt: Double
}

struct SourceConnection: Decodable, Identifiable, Equatable {
    var id: String
    var label: String
    var connected: Bool
    var connectionState: String
    var message: String
}
struct LocalSession: Decodable, Identifiable, Equatable {
    var threadId: String?
    var session: String
    var id: String { threadId ?? session }
}

/// Aggregate counters come only from CLI usage records, independent of visual activity.
struct UsageMetrics: Decodable, Equatable {
    var utilizationPercent: Double?
    var runningSessions: Int?
    var knownSessions: Int?
    var monitoredSessions: Int?
    var usageCovered: Int?
    var toolsCovered: Int?
    var usageSessions: Int?
    var totalTokens: Int?
    var inputTokens: Int?
    var outputTokens: Int?
    var cachedInputTokens: Int?
    var cacheWriteInputTokens: Int?
    var consumptionPerSecond: Double?
    var upstreamTokensPerSecond: Double?
    var windowTokens: Int?
    var windowOutputTokens: Int?
    var toolsPerMinute: Double?
    var toolCompletions: Int?
    var activeTools: Int?
    var toolsEpoch: Int?
    var liveUsageCovered: Int?
    var liveToolsCovered: Int?
    var windowSeconds: Int?
    var workWindowSeconds: Int?
    var scope: String?
    var rootIdentity: String?

    static func amount(_ value: Int?) -> String {
        guard let value = value, value >= 0 else { return "—" }
        if value >= 1_000_000_000 { return String(format: "%.2fB", Double(value) / 1_000_000_000) }
        if value >= 1_000_000 { return String(format: "%.2fM", Double(value) / 1_000_000) }
        if value >= 1_000 { return String(format: "%.1fk", Double(value) / 1_000) }
        return "\(value)"
    }
    static func exactAmount(_ value: Int?) -> String {
        guard let value = value, value >= 0 else { return "—" }
        let digits = Array(String(value))
        return digits.enumerated().map { index, digit in
            (index > 0 && (digits.count - index) % 3 == 0 ? "," : "") + String(digit)
        }.joined()
    }
    static func rate(_ value: Double?) -> String {
        guard let value = value, value.isFinite, value >= 0 else { return "—" }
        if value >= 1_000_000_000 { return String(format: "%.2fB", value / 1_000_000_000) }
        if value >= 1_000_000 { return String(format: "%.2fM", value / 1_000_000) }
        if value >= 10_000 { return String(format: "%.1fk", value / 1_000) }
        return String(format: value >= 100 ? "%.0f" : "%.1f", value)
    }
    var utilizationText: String {
        guard let value = utilizationPercent, value.isFinite else { return "—" }
        return String(format: "%.0f%%", min(100, max(0, value)))
    }
    var usageCoverageText: String { "\(usageCovered ?? 0)/\(usageSessions ?? 0)" }
    var workCoverageText: String { "\(toolsCovered ?? 0)/\(usageSessions ?? 0)" }
    var usageIsPartial: Bool { (usageCovered ?? 0) < (usageSessions ?? 0) }
    var liveUsageIsPartial: Bool { (liveUsageCovered ?? 0) < (usageSessions ?? 0) }
    var liveWorkIsPartial: Bool { (liveToolsCovered ?? 0) < (usageSessions ?? 0) }
    func liveRate(_ value: Double?, connected: Bool) -> String {
        Self.rate(connected ? value : nil)
    }
    func liveWindowTokens(connected: Bool) -> String {
        Self.exactAmount(connected ? windowTokens : nil)
    }
    func accessibilitySummary(connected: Bool) -> String {
        "总运行率 \(connected ? utilizationText : "—")，近 \(windowSeconds ?? 30) 秒新增 \(liveWindowTokens(connected: connected)) tok，实时消耗 \(liveRate(consumptionPerSecond, connected: connected)) tok/s，累计 \(Self.exactAmount(totalTokens)) tok，上游输出 \(liveRate(upstreamTokensPerSecond, connected: connected)) tok/s，工作速度 \(liveRate(toolsPerMinute, connected: connected)) 工具次/min，用量覆盖 \(usageCoverageText)，实时覆盖 \(connected ? liveUsageCovered ?? 0 : 0)/\(usageSessions ?? 0)"
    }
    static func combining(_ rows: [Pane]) -> UsageMetrics? {
        guard rows.contains(where: { $0.usageMetrics != nil }) else { return nil }
        var roots: [String: UsageMetrics] = [:]
        for row in rows {
            let metrics = row.usageMetrics ?? UsageMetrics()
            let key = (row.source ?? "remote") + ":" + (metrics.rootIdentity ?? "unknown:" + row.monitorKey)
            let rank: (Int, Int, Int, Int, Int) = (metrics.usageCovered ?? 0, metrics.totalTokens ?? -1,
                                                  metrics.liveUsageCovered ?? 0, metrics.liveToolsCovered ?? 0, metrics.toolsCovered ?? 0)
            if let old = roots[key] {
                let previousRank: (Int, Int, Int, Int, Int) = (old.usageCovered ?? 0, old.totalTokens ?? -1,
                                                              old.liveUsageCovered ?? 0, old.liveToolsCovered ?? 0, old.toolsCovered ?? 0)
                if previousRank >= rank { continue }
            }
            roots[key] = metrics
        }
        let values = Array(roots.values)
        var result = UsageMetrics()
        result.scope = "response_group"
        result.usageSessions = values.count
        result.usageCovered = values.reduce(0) { $0 + ($1.usageCovered ?? 0) }
        result.toolsCovered = values.reduce(0) { $0 + ($1.toolsCovered ?? 0) }
        result.liveUsageCovered = values.reduce(0) { $0 + ($1.liveUsageCovered ?? 0) }
        result.liveToolsCovered = values.reduce(0) { $0 + ($1.liveToolsCovered ?? 0) }
        result.windowSeconds = values.compactMap(\.windowSeconds).first ?? 30
        result.workWindowSeconds = values.compactMap(\.workWindowSeconds).first ?? 60
        let covered = values.filter { ($0.usageCovered ?? 0) > 0 }
        func checkedSum(_ parts: [Int]) -> Int? {
            var sum = 0
            for part in parts {
                let (next, overflow) = sum.addingReportingOverflow(part)
                guard part >= 0, !overflow else { return nil }
                sum = next
            }
            return sum
        }
        func total(_ key: KeyPath<UsageMetrics, Int?>) -> Int? {
            let parts = covered.compactMap { $0[keyPath: key] }
            return parts.count == covered.count && !parts.isEmpty ? checkedSum(parts) : nil
        }
        result.totalTokens = total(\.totalTokens); result.inputTokens = total(\.inputTokens)
        result.outputTokens = total(\.outputTokens); result.cachedInputTokens = total(\.cachedInputTokens)
        result.cacheWriteInputTokens = total(\.cacheWriteInputTokens)
        let live = values.filter { ($0.liveUsageCovered ?? 0) > 0 }
        func window(_ key: KeyPath<UsageMetrics, Int?>) -> Int? {
            let parts = live.compactMap { $0[keyPath: key] }
            return parts.count == live.count && !parts.isEmpty ? checkedSum(parts) : nil
        }
        func rate(_ rows: [UsageMetrics], _ key: KeyPath<UsageMetrics, Double?>) -> Double? {
            let parts = rows.compactMap { $0[keyPath: key] }
            return parts.count == rows.count && !parts.isEmpty ? parts.reduce(0, +) : nil
        }
        result.windowTokens = window(\.windowTokens); result.windowOutputTokens = window(\.windowOutputTokens)
        result.consumptionPerSecond = rate(live, \.consumptionPerSecond)
        result.upstreamTokensPerSecond = rate(live, \.upstreamTokensPerSecond)
        result.toolsPerMinute = rate(values.filter { ($0.liveToolsCovered ?? 0) > 0 }, \.toolsPerMinute)
        let toolValues = values.filter { ($0.toolsCovered ?? 0) > 0 }
        let completions = toolValues.compactMap(\.toolCompletions)
        result.toolCompletions = completions.count == toolValues.count && !completions.isEmpty ? checkedSum(completions) : nil
        let active = values.compactMap(\.activeTools)
        result.activeTools = active.count == values.count && !active.isEmpty ? checkedSum(active) : nil
        return result
    }
}

/// Resource observations describe process ownership, independently of task state.
struct ResourceMetrics: Decodable, Equatable {
    var identity: String?
    var epoch: String?
    var sampleId: Int?
    var sampleAgeSeconds: Double?
    var scope: String?
    var processCount: Int?
    var sharedSessions: Int?
    var treeComplete: Bool?
    var memoryBytes: Int?
    var hostMemoryBytes: Int?
    var memoryAvailable: Bool?
    var memoryComplete: Bool?
    var memoryReason: String?
    var ioReadBytes: Int?
    var ioWriteBytes: Int?
    var ioReadPerSecond: Double?
    var ioWritePerSecond: Double?
    var ioAvailable: Bool?
    var ioComplete: Bool?
    var ioScope: String?
    var ioKind: String?
    var ioReason: String?
    var networkRxBytes: Int?
    var networkTxBytes: Int?
    var networkRxPerSecond: Double?
    var networkTxPerSecond: Double?
    var networkAvailable: Bool?
    var networkComplete: Bool?
    var networkShared: Bool?
    var networkScope: String?
    var networkReason: String?
    var isFresh: Bool { sampleAgeSeconds.map { $0.isFinite && $0 >= 0 && $0 <= 4 } ?? true }
    static func bytes(_ value: Int?) -> String {
        guard let value = value, value >= 0 else { return "—" }
        return ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .binary)
    }
    static func rate(_ value: Double?) -> String {
        guard let value = value, value.isFinite, value >= 0 else { return "—" }
        let units = ["B/s", "KiB/s", "MiB/s", "GiB/s"]
        var amount = value, index = 0
        while amount >= 1024 && index < units.count - 1 { amount /= 1024; index += 1 }
        return String(format: amount >= 100 ? "%.0f %@" : "%.1f %@", amount, units[index])
    }
    func summary(connected: Bool) -> String {
        guard connected && isFresh else { return "资源未观测" }
        var lines: [String] = []
        if scope == "shared_process" { lines.append("共享进程资源，无法拆分到单个会话") }
        let memory = memoryAvailable == true ? Self.bytes(memoryBytes) : "—"
        lines.append("进程树 RSS \(memory)" + (memoryAvailable == true && memoryComplete != true ? "（部分）" : "") + "；共享内存页可能重复计入")
        let net = networkAvailable == true
            ? "TCP ↓\(Self.rate(networkRxPerSecond)) ↑\(Self.rate(networkTxPerSecond))" + (networkComplete == true ? "" : "（部分）")
            : "TCP 流量未观测"
        lines.append(net + (networkShared == true ? "；共享连接未重复分摊" : ""))
        lines.append(ioAvailable == true ? "根进程 I/O 读 \(Self.rate(ioReadPerSecond))，写 \(Self.rate(ioWritePerSecond))（系统调用字节，含已回收子进程）" : "根进程 I/O 未观测")
        return lines.joined(separator: "\n")
    }
}

/// A configured group occupies one visual position; member panes remain available to open.
struct SessionUnit: Identifiable {
    var id: String
    var rows: [Pane]
    var group: SessionResponseGroup? { rows.compactMap(\.responseGroup).first }
    var isGroup: Bool { group != nil }
    var name: String { group?.name ?? rows.first?.orbitName ?? "会话" }
    static func collect(_ rows: [Pane]) -> [SessionUnit] {
        var order: [String] = []
        var grouped: [String: [Pane]] = [:]
        for row in rows {
            let key = row.responseGroup != nil ? row.orbitKey : "pane:" + row.paneId
            if grouped[key] == nil { order.append(key) }
            grouped[key, default: []].append(row)
        }
        return order.map { SessionUnit(id: $0, rows: grouped[$0] ?? []) }
    }
    func visualState(connected: (Pane) -> Bool) -> SessionVisualState {
        let states = rows.map { $0.visualState(connected: connected($0)) }
        guard isGroup else { return .mostImportant(states) }
        let live = states.filter { $0 != .offline }
        guard !live.isEmpty else { return .offline }
        if live.contains(.error) { return .error }
        if live.contains(.attention) { return .attention }
        if live.contains(.running) { return .running }
        if live.count != states.count || live.contains(.unknown) || ["unknown", "offline"].contains(group?.kind ?? "") { return .unknown }
        return .waiting
    }
    func summaryPane(connected: (Pane) -> Bool) -> Pane? {
        guard var pane = rows.min(by: { $0.visualState(connected: connected($0)).priority < $1.visualState(connected: connected($1)).priority }) else { return nil }
        guard isGroup else { return pane }
        let state = visualState(connected: connected)
        pane.paneId = id; pane.groupId = id; pane.session = name
        pane.state = state == .waiting ? "idle" : state == .attention ? "approval" : state == .offline ? "unknown" : state.rawValue
        pane.available = state != .offline
        let labels: [SessionVisualState: String] = [.running: "运行中", .waiting: "等待输入", .attention: "待处理", .error: "异常", .unknown: "待核实", .offline: "离线"]
        pane.status = (labels[state] ?? "待核实") + " · \(Set(rows.map(\.monitorKey)).count) 会话"
        pane.attentionLevel = state == .error ? "error" : state == .attention ? "warning" : nil
        pane.responseGroup = group; pane.response = group?.response
        pane.manualRunning = rows.contains { $0.manualRunning == true }
        pane.manualOverrideId = rows.compactMap(\.manualOverrideId).first
        pane.source = nil; pane.threadId = nil
        let agents = Set(rows.compactMap(\.agent)); pane.agent = agents.count == 1 ? agents.first : nil
        pane.preview = []; pane.working = nil
        let errors = rows.filter { connected($0) }.compactMap { row -> String? in
            guard let error = row.errorSummary, !error.isEmpty else { return nil }
            return row.session + ": " + error
        }
        pane.errorSummary = errors.isEmpty ? nil : errors.joined(separator: "；")
        pane.usageMetrics = UsageMetrics.combining(rows)
        return pane
    }
}

enum ManualRunAction: String, Codable { case running, clear }

struct ManualRunTarget: Equatable {
    var key: String
    var members: [String]
}

/// The collector consumes fresh command IDs; it owns lifecycle and timing changes.
struct ManualRunCommand: Codable {
    var id: String
    var action: ManualRunAction
    var target: String
    var members: [String]
    var createdAt: String
    enum CodingKeys: String, CodingKey { case id, action, target, members; case createdAt = "created_at" }
    init(action: ManualRunAction, target: ManualRunTarget) {
        id = UUID().uuidString
        self.action = action
        self.target = target.key
        members = Array(Set(target.members)).sorted()
        createdAt = ISO8601DateFormatter().string(from: Date())
    }
}

struct ManualRunCommandQueue: Codable {
    var version = 1
    var commands: [ManualRunCommand] = []
    static let capacity = 128
    static func append(_ command: ManualRunCommand, at url: URL) throws {
        var queue = ManualRunCommandQueue()
        if FileManager.default.fileExists(atPath: url.path) {
            queue = try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
            guard queue.version == 1 else {
                throw NSError(domain: "AgentOrbit.ManualRun", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "手动运行命令格式不兼容"])
            }
        }
        queue.commands.append(command)
        queue.commands = Array(queue.commands.suffix(capacity))
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        try encoder.encode(queue).write(to: url, options: .atomic)
    }
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
    var metrics: UsageMetrics?
}

struct ErrorEvent: Decodable, Identifiable, Equatable {
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
    var visualState: SessionVisualState { isCritical ? .error : kind == "unknown" ? .unknown : .attention }
}

func shellQuote(_ value: String) -> String {
    "'" + value.replacingOccurrences(of: "'", with: "'\"'\"'") + "'"
}

func argument(_ name: String, fallback: String) -> String {
    guard let i = CommandLine.arguments.firstIndex(of: name), i + 1 < CommandLine.arguments.count else { return fallback }
    return CommandLine.arguments[i + 1]
}

/// Notification state stays local; it never marks a CLI question as answered.
struct WaitReminderTracker {
    struct Candidate {
        var key: String
        var elapsed: Double
    }
    private struct Episode {
        var notified = false
        var opened = false
        var snoozeUntil: Double?
    }
    private var episodes: [String: Episode] = [:]

    mutating func update(_ candidates: [Candidate], enabled: Bool, soundAllowed: Bool,
                         minutes: Double, now: Double, deliveryBlocked: Bool = false) -> [String] {
        let threshold = max(60, min(7200, minutes.isFinite ? minutes * 60 : 300))
        // Changing preferences never replays an already delivered reminder.
        // Untouched episodes naturally become eligible when sound is enabled
        // or the threshold is lowered; only an explicit snooze re-arms a sound.
        let active = Set(candidates.map(\.key))
        episodes = episodes.filter { active.contains($0.key) }
        guard enabled else { return [] }
        var due: [String] = []
        for candidate in candidates where candidate.elapsed.isFinite && candidate.elapsed >= threshold {
            var episode = episodes[candidate.key] ?? Episode()
            if let until = episode.snoozeUntil {
                guard now >= until else { episodes[candidate.key] = episode; continue }
                episode.snoozeUntil = nil; episode.notified = false
            }
            guard !episode.opened, !episode.notified else { episodes[candidate.key] = episode; continue }
            if soundAllowed && !deliveryBlocked {
                episode.notified = true; due.append(candidate.key)
            }
            episodes[candidate.key] = episode
        }
        return due
    }
    mutating func snooze(_ key: String, now: Double, minutes: Double) {
        var episode = episodes[key] ?? Episode()
        episode.opened = false; episode.notified = false
        episode.snoozeUntil = now + max(60, min(7200, minutes.isFinite ? minutes * 60 : 300))
        episodes[key] = episode
    }
    mutating func opened(_ key: String) {
        var episode = episodes[key] ?? Episode()
        episode.opened = true; episode.notified = true; episode.snoozeUntil = nil
        episodes[key] = episode
    }
    func isSnoozed(_ key: String, now: Double) -> Bool {
        (episodes[key]?.snoozeUntil ?? 0) > now
    }
    func wasOpened(_ key: String) -> Bool { episodes[key]?.opened == true }
}

final class PanelModel: ObservableObject {
    @Published var settings: AppSettings
    @Published var connections: [SourceConnection] = []
    @Published var localSessions: [LocalSession] = []
    @Published var metrics: UsageMetrics?
    @Published private(set) var reminderRevision = 0
    @Published var rows: [Pane] = []
    @Published var followed: [String] = []
    @Published var connectionState = "connecting"
    @Published var connected = false
    @Published var message = "正在连接…"
    @Published var unread: [Completion] = []
    @Published var unreadErrors: [ErrorEvent] = []
    @Published var sound = UserDefaults.standard.object(forKey: "sound") as? Bool ?? true {
        didSet {
            guard sound != oldValue else { return }
            if settings.sound != sound { settings.sound = sound; persistSettings() }
            onUpdate?()
        }
    }
    @Published var pinned = UserDefaults.standard.object(forKey: "pinned") as? Bool ?? true {
        didSet {
            guard pinned != oldValue else { return }
            if settings.pinned != pinned { settings.pinned = pinned; persistSettings() }
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
    func isConnected(_ pane: Pane) -> Bool {
        guard connected else { return false }
        if let source = pane.source, let connection = connections.first(where: { $0.id == source }) {
            return connection.connected
        }
        return connected
    }
    private var process: Process?
    private var pipe: Pipe?
    private var errorPipe: Pipe?
    private var seen = Set<String>()
    private var seenErrors = Set<String>()
    private var dismissedErrors = Set<String>()
    private var waitReminders = WaitReminderTracker()
    private var lastPrioritySoundAt = -Double.infinity
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
        Set(rows.filter { $0.visualState(connected: isConnected($0)) == .error }.map(\.paneId) + unreadErrors.filter { $0.isCritical && !isResolved($0) }.map(\.paneId)).count
    }
    var warningCount: Int {
        Set(rows.filter { [.attention, .unknown].contains($0.visualState(connected: isConnected($0))) }.map(\.paneId) + unreadErrors.filter { !$0.isCritical && !isResolved($0) }.map(\.paneId)).count
    }
    var attentionVisualState: SessionVisualState {
        let states = rows.map { $0.visualState(connected: isConnected($0)) }.filter { [.error, .attention, .unknown].contains($0) }
        return .mostImportant(states + unreadErrors.filter { !isResolved($0) }.map(\.visualState))
    }
    var visibleCompletions: [Completion] {
        unread.filter { event in
            rows.contains { $0.paneId == event.paneId && $0.visualState(connected: isConnected($0)) == .waiting && $0.completionId == event.id }
        }
    }

    var pendingPanes: [Pane] {
        var seen = Set<String>()
        return rows.filter { pane in
            isConnected(pane) && pane.available != false && !pane.isCritical &&
            pane.effectiveResponse?.isPending == true && pane.effectiveResponse?.reminderSuppressed != true && seen.insert(pane.responseMonitorKey).inserted
        }.sorted { ($0.effectiveResponse?.elapsedSeconds ?? -1) > ($1.effectiveResponse?.elapsedSeconds ?? -1) }
    }
    var longestPending: Double? { pendingPanes.compactMap { $0.effectiveResponse?.elapsedSeconds }.max() }
    var pendingSummary: String {
        let pending = pendingPanes
        guard !pending.isEmpty else { return "" }
        let oldest = pending.first?.effectiveResponse
        let age = SessionResponse.duration(oldest?.elapsedSeconds)
        return "待响应 \(pending.count) · 最久" + (oldest?.observed == true ? "≥" : "") + age
    }
    var priorityPane: Pane? {
        rows.first { isConnected($0) && $0.isCritical } ?? pendingPanes.first
    }
    var displayRows: [Pane] {
        func priority(_ pane: Pane) -> Int {
            guard isConnected(pane), pane.available != false else { return 6 }
            if pane.isCritical { return 0 }
            if pane.effectiveResponse?.isPending == true {
                if pane.effectiveResponse?.overdue == true { return 1 }
                return pane.effectiveResponse?.kind == "attention" ? 2 : 3
            }
            return pane.state == "running" ? 4 : 5
        }
        return rows.enumerated().sorted { left, right in
            let a = priority(left.element), b = priority(right.element)
            if a != b { return a < b }
            if (1...3).contains(a) {
                let elapsedA = left.element.effectiveResponse?.elapsedSeconds ?? -1
                let elapsedB = right.element.effectiveResponse?.elapsedSeconds ?? -1
                if elapsedA != elapsedB { return elapsedA > elapsedB }
            }
            return left.offset < right.offset
        }.map(\.element)
    }
    func reminderIsSnoozed(_ pane: Pane) -> Bool {
        guard let key = pane.responseEpisodeKey else { return false }
        return waitReminders.isSnoozed(key, now: ProcessInfo.processInfo.systemUptime)
    }
    func reminderWasOpened(_ pane: Pane) -> Bool {
        guard let key = pane.responseEpisodeKey else { return false }
        return waitReminders.wasOpened(key)
    }
    func snooze(_ pane: Pane) {
        guard isConnected(pane), pane.available != false, !pane.isCritical,
              pane.effectiveResponse?.isPending == true, pane.effectiveResponse?.reminderSuppressed != true,
              let key = pane.responseEpisodeKey else { return }
        waitReminders.snooze(key, now: ProcessInfo.processInfo.systemUptime, minutes: settings.waitReminderMinutes)
        reminderRevision += 1; onUpdate?()
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
        let demoMode = CommandLine.arguments.contains("--demo") || CommandLine.arguments.contains("--export-demo") || CommandLine.arguments.contains("--export-signals")
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
        ThemePalette.configure(theme: initial.theme, statusStyle: initial.orbitLabelColor)
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
        let url = dataRoot.appendingPathComponent("settings.json")
        if let data = try? settings.encoded(), (try? Data(contentsOf: url)) != data {
            try? data.write(to: url, options: .atomic)
        }
    }

    func saveSettings(_ value: AppSettings, follow: String) throws {
        try value.validate()
        let encoded = try value.encoded()
        try saveFollow(follow)
        let url = dataRoot.appendingPathComponent("settings.json")
        if (try? Data(contentsOf: url)) != encoded { try encoded.write(to: url, options: .atomic) }
        if settings.theme != value.theme || settings.orbitLabelColor != value.orbitLabelColor {
            ThemePalette.configure(theme: value.theme, statusStyle: value.orbitLabelColor)
        }
        if (try? settings.encoded()) != encoded { settings = value }
        if sound != value.sound { sound = value.sound }
        if pinned != value.pinned { pinned = value.pinned }
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
                        if self.sound && self.settings.errorSound {
                            self.lastPrioritySoundAt = ProcessInfo.processInfo.systemUptime
                            NSSound(named: NSSound.Name("Basso"))?.play()
                        }
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
        // Heartbeats keep freshness/reminder clocks alive without republishing
        // identical arrays into every SwiftUI subscriber. Every real edge and
        // event still passes through this method, independently of drawing FPS.
        var presentationChanged = false
        let newConnections = snapshot.connections ?? []
        let newLocalSessions = snapshot.localSessions ?? []
        let newConnectionState = snapshot.connectionState ?? (snapshot.connected ? "live" : "reconnecting")
        if connections != newConnections { connections = newConnections; presentationChanged = true }
        if localSessions != newLocalSessions { localSessions = newLocalSessions; presentationChanged = true }
        if metrics != snapshot.metrics { metrics = snapshot.metrics; presentationChanged = true }
        if rows != snapshot.rows { rows = snapshot.rows; presentationChanged = true }
        if followed != snapshot.followed { followed = snapshot.followed; presentationChanged = true }
        if connected != snapshot.connected { connected = snapshot.connected; presentationChanged = true }
        if connectionState != newConnectionState { connectionState = newConnectionState; presentationChanged = true }
        if message != snapshot.message { message = snapshot.message; presentationChanged = true }
        let fresh = snapshot.events.filter { !seen.contains($0.id) }
        for event in fresh { seen.insert(event.id) }
        if !fresh.isEmpty {
            let paneIds = Set(fresh.map(\.paneId))
            unread = Array((Array(fresh.reversed()) + unread.filter { !paneIds.contains($0.paneId) }).prefix(100))
            presentationChanged = true
            onCompletion?()
        }
        let currentUnread = unread.filter { event in
            rows.contains { $0.paneId == event.paneId && $0.visualState(connected: isConnected($0)) == .waiting && $0.completionId == event.id }
        }
        if unread != currentUnread { unread = currentUnread; presentationChanged = true }
        let incomingErrors = (snapshot.errorEvents ?? []).filter { !seenErrors.contains($0.id) }
        for event in incomingErrors { seenErrors.insert(event.id) }
        let freshErrors = incomingErrors.filter { !(($0.kind == "question" || $0.kind == "approval") && isResolved($0)) }
        if !freshErrors.isEmpty {
            presentationChanged = true
            onError?()
        }
        // The delivery queue drives sounds; the active list drives cards.
        // Resolved incidents never become history cards in the panel.
        let localAlerts = unreadErrors.filter { $0.paneId == "__local__" && !connected }
        let activeErrors = snapshot.activeErrorEvents ?? (unreadErrors + freshErrors).filter { !isResolved($0) }
        var currentErrors = unreadErrors
        if !activeErrors.isEmpty || !currentErrors.isEmpty {
            var latest: [String: ErrorEvent] = [:]
            for event in (activeErrors + localAlerts).filter({ !dismissedErrors.contains($0.id) }).sorted(by: { $0.occurredAt < $1.occurredAt }) {
                let source = event.session == "本地监控" || event.session == "监控连接" ? event.session : event.paneId
                latest[source] = event
            }
            currentErrors = Array(latest.values.sorted(by: {
                $0.occurredAt == $1.occurredAt ? $0.id < $1.id : $0.occurredAt > $1.occurredAt
            }).prefix(20))
        }
        currentErrors.removeAll { isResolved($0) }
        if unreadErrors != currentErrors { unreadErrors = currentErrors; presentationChanged = true }
        // Deliver at most one sound per update. Critical events and fresh
        // completions are heard before a waiting reminder; postponed reminders
        // remain eligible on the next fresh frame.
        let eventSound = sound && ((!freshErrors.isEmpty && settings.errorSound) || (!fresh.isEmpty && settings.completionSound))
        let candidates = pendingPanes.compactMap { pane -> WaitReminderTracker.Candidate? in
            guard let key = pane.responseEpisodeKey, let elapsed = pane.effectiveResponse?.elapsedSeconds else { return nil }
            return WaitReminderTracker.Candidate(key: key, elapsed: elapsed)
        }
        let reminderNow = ProcessInfo.processInfo.systemUptime
        if eventSound { lastPrioritySoundAt = reminderNow }
        let due = waitReminders.update(candidates, enabled: settings.waitReminderEnabled,
                                       soundAllowed: sound && settings.waitReminderSound,
                                       minutes: settings.waitReminderMinutes,
                                       now: reminderNow, deliveryBlocked: reminderNow - lastPrioritySoundAt < 3)
        if eventSound {
            let critical = settings.errorSound && freshErrors.contains(where: \.isCritical)
            NSSound(named: NSSound.Name(critical ? "Basso" : "Glass"))?.play()
        } else if !due.isEmpty { NSSound(named: NSSound.Name("Glass"))?.play() }
        if presentationChanged || !due.isEmpty { onUpdate?() }
    }

    func acknowledge(_ pane: Pane) {
        if let key = pane.responseEpisodeKey, pane.effectiveResponse?.isPending == true {
            waitReminders.opened(key); reminderRevision += 1
        }
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

    func requestManualRunning(_ action: ManualRunAction, target: ManualRunTarget) {
        guard !target.key.isEmpty, !target.members.isEmpty else { return }
        do {
            try ManualRunCommandQueue.append(ManualRunCommand(action: action, target: target),
                                             at: dataRoot.appendingPathComponent("manual-overrides.json"))
        } catch {
            message = "无法保存手动运行标记：\(error.localizedDescription)"
            onUpdate?()
        }
    }

    func saveFollow(_ text: String) throws {
        let names = text.components(separatedBy: CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn: ",，、")))
            .filter { !$0.isEmpty }
        var unique: [String] = []
        for name in names where !unique.contains(name) { unique.append(name) }
        let data = try JSONSerialization.data(withJSONObject: unique, options: [.prettyPrinted, .withoutEscapingSlashes])
        let url = dataRoot.appendingPathComponent("follow.json")
        if (try? Data(contentsOf: url)) != data { try data.write(to: url, options: .atomic) }
        if followed != unique { followed = unique }
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
    private var bubbleAnimationTimer: Timer?
    private var bubbleAnimationInterval: TimeInterval = 0
    private var minimizedByUser = false
    private var bubbleWasHidden = true

    func applicationDidFinishLaunching(_ notification: Notification) {
        if CommandLine.arguments.contains("--export-signals") {
            do { try DemoSupport.exportSignals(model, folder: URL(fileURLWithPath: argument("--export-signals", fallback: "/tmp/agent-orbit-signals"))) }
            catch { fputs("Signal export failed: \(error)\n", stderr) }
            NSApp.terminate(nil); return
        }
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
        bubble.delegate = self
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
        NotificationCenter.default.addObserver(self, selector: #selector(showWaveGuide), name: NSNotification.Name("AgentOrbit.OpenWaveGuide"), object: nil)
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
        if panel.alphaValue != model.settings.opacity { panel.alphaValue = model.settings.opacity }
        if bubble.alphaValue != model.settings.opacity { bubble.alphaValue = model.settings.opacity }
        let level: NSWindow.Level = model.pinned ? .floating : .normal
        if panel.level != level { panel.level = level }
        if bubble.level != level { bubble.level = level }
        let appearance: NSAppearance.Name = ThemePalette.light ? .aqua : .darkAqua
        if panel.appearance?.name != appearance { panel.appearance = NSAppearance(named: appearance) }
        let idle = model.rows.filter { $0.visualState(connected: model.isConnected($0)) == .waiting }.count
        let errors = model.criticalCount
        let warnings = model.warningCount
        let title = !model.connected ? (model.connectionState == "delayed" ? "AO …" : "AO !") : errors > 0 ? "AO !\(errors)" : warnings > 0 ? "AO ?\(warnings)" : "AO \(idle)"
        let tooltip = !model.connected ? model.message : errors > 0 ? "\(errors) 个异常或中断待查看" : "\(idle) 个会话等待输入"
        if statusItem.button?.title != title { statusItem.button?.title = title }
        if statusItem.button?.toolTip != tooltip { statusItem.button?.toolTip = tooltip }
        if bubble.isVisible && !NSApp.isHidden {
            if bubbleWasHidden { bubbleView.resumePresentation(); bubbleWasHidden = false }
            bubbleView.update(model)
        } else { bubbleWasHidden = true }
        updateBubbleAnimationSchedule()
    }

    /// Draw ticks never poll, publish models, group sessions or infer state.
    /// The view lowers its own requested FPS after expensive draws.
    private func updateBubbleAnimationSchedule() {
        let visible = bubble?.isVisible == true && !NSApp.isHidden
        let fps = bubbleView?.recommendedFrameRate(visible: visible) ?? 0
        let interval: TimeInterval = fps.isFinite && fps > 0 ? 1 / min(24, fps) : 0
        guard abs(interval - bubbleAnimationInterval) > 0.001 ||
                (interval > 0 && bubbleAnimationTimer == nil) else { return }
        bubbleAnimationTimer?.invalidate()
        bubbleAnimationTimer = nil
        bubbleAnimationInterval = interval
        guard interval > 0 else { return }
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            guard let self else { return }
            if self.bubble.isVisible && !NSApp.isHidden { self.bubbleView.presentationTick() }
            self.updateBubbleAnimationSchedule()
        }
        timer.tolerance = interval * 0.15
        bubbleAnimationTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    @objc func showWaveGuide() {
        WaveGuidePresenter.show()
    }

    @objc func showSettings() {
        if let window = settingsWindow, window.isVisible { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 640), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Agent Orbit 设置"; window.isReleasedWhenClosed = false
        window.delegate = self
        window.level = .floating
        window.contentView = NSHostingView(rootView: SettingsView(model: model) { [weak window] in window?.close() })
        window.center(); settingsWindow = window
        window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }

    @objc func showPanel() {
        minimizedByUser = false
        bubble.orderOut(nil)
        bubbleWasHidden = true
        updateBubbleAnimationSchedule()
        if panel.isMiniaturized { panel.deminiaturize(nil) }
        panel.orderFrontRegardless()
    }
    @objc func minimizePanel() {
        minimizedByUser = true
        panel.orderOut(nil)
        keepBubbleOnScreen()
        if bubbleWasHidden { bubbleView.resumePresentation(); bubbleWasHidden = false }
        bubbleView.update(model)
        bubble.orderFrontRegardless()
        updateBubbleAnimationSchedule()
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
    func applicationWillTerminate(_ notification: Notification) {
        bubbleAnimationTimer?.invalidate()
        demoTimer?.invalidate()
        model.stop()
    }
    func applicationDidHide(_ notification: Notification) {
        bubbleWasHidden = true
        updateBubbleAnimationSchedule()
    }
    func applicationDidUnhide(_ notification: Notification) { updateStatus() }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard sender === panel else { return true }
        minimizePanel(); return false
    }
    func windowWillClose(_ notification: Notification) {
        if let window = notification.object as? NSWindow, window === settingsWindow {
            // The closed settings window must not keep an ObservedObject host
            // rebuilding its forms on every monitoring update.
            window.contentView = nil
            settingsWindow = nil
        }
    }
    func windowDidMiniaturize(_ notification: Notification) {
        guard notification.object as? NSWindow === panel else { return }
        minimizePanel()
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
