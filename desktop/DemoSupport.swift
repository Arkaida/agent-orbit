import AppKit
import SwiftUI

// All names, messages and identifiers below are synthetic. No collector runs here.
enum DemoSupport {
    static func prepare(_ model: PanelModel, theme: String = "neon") {
        var settings = AppSettings()
        settings.remoteEnabled = true; settings.localEnabled = true
        settings.host = "demo-server"; settings.localCodexHome = "/Users/demo/.codex"
        settings.theme = theme; settings.sound = false; settings.reduceMotion = true
        model.settings = settings; model.sound = false
        ThemePalette.configure(theme: theme, statusStyle: settings.orbitLabelColor, waveStyle: settings.waveStyle)
        model.followed = ["atlas", "nova"]
    }
    static func snapshot(_ step: Int) -> Snapshot {
        let completed = step >= 8
        let hasError = (14...18).contains(step)
        let amounts = [60, 450, 1100, 160, 2300, 600, 3000, 80]
        let amount = amounts[step % 8]
        let total = (0...step).reduce(0) { $0 + amounts[$1 % 8] }
        let states = [completed ? "idle" : "running", "running", hasError ? "error" : "idle"]
        let names = ["atlas", "Meridian", "nova"]
        var rows: [[String: Any]] = []
        for i in 0..<3 {
            let local = i == 1
            let isRunning = states[i] == "running"
            let isError = states[i] == "error"
            let elapsed = isRunning ? Double(640 + step * 2) : i == 0 ? Double(max(0, step - 8) * 45 + 20) : Double(420 + step * 5)
            let idleSeconds = Double(420 + i * 60 + step * 5)
            let runSeconds = Double(1200 + i * 100 + step * 2)
            let response: [String: Any] = [
                "kind": isError ? "unknown" : isRunning ? "running" : "waiting",
                "episode_id": isRunning || isError ? NSNull() : "demo-response-\(i)-1",
                "since": isError ? NSNull() : 1_700_000_000.0 - elapsed,
                "elapsed_seconds": isError ? NSNull() : elapsed,
                "observed": !isError && (isRunning || i == 2), "overdue": !isRunning && !isError && elapsed >= 300,
                "idle_seconds": idleSeconds, "run_seconds": runSeconds,
                "idle_run_ratio": idleSeconds / runSeconds, "efficiency_percent": runSeconds / (idleSeconds + runSeconds) * 100,
                "scope": "observed_monitoring", "reminder_suppressed": false
            ]
            let rootUsage: [String: Any] = [
                "usage_covered": 1, "tools_covered": 1, "usage_sessions": 1,
                "live_usage_covered": 1, "live_tools_covered": 1, "scope": "root_session",
                "input_tokens": 50_000 + i * 7_000 + step * 80,
                "output_tokens": 4_100 + i * 600 + step * 13,
                "total_tokens": 54_100 + i * 7_600 + step * 93,
                "cached_input_tokens": 35_000 + i * 6_000, "cache_write_input_tokens": 3_200,
                "consumption_per_second": isRunning ? 31.5 + Double(i * 8) : 0,
                "upstream_tokens_per_second": isRunning ? 5.8 + Double(i * 2) : 0,
                "window_tokens": isRunning ? 945 + i * 240 : 0,
                "window_output_tokens": isRunning ? 174 + i * 60 : 0,
                "tools_per_minute": isRunning ? 1.0 + Double(i) : 0,
                "window_seconds": 30, "work_window_seconds": 60
            ]
            rows.append([
                "pane_id": local ? "local:demo-ab12" : "remote:%\(i)",
                "group_id": local ? "local:demo-ab12" : "remote:\(names[i])",
                "thread_id": "demo-ab12", "session": names[i], "source": local ? "local" : "remote",
                "source_label": local ? "本机" : "远端", "available": true,
                "agent": i == 2 ? "claude" : "codex", "state": states[i],
                "status": states[i] == "running" ? "运行中" : states[i] == "error" ? "服务异常 · 503" : "等待输入",
                "preview": i == 0 ? [completed ? "检查完成，所有测试通过。" : "正在整理修改，检查接口与测试。"] : i == 1 ? ["正在分析任务，子智能体执行检查。"] : ["文档已更新，随时可以继续。"],
                "error_summary": hasError && i == 2 ? "503 Service Unavailable · 需要检查连接" : "",
                "error_kind": hasError && i == 2 ? "service" : "",
                "attention_level": hasError && i == 2 ? "error" : "",
                "activity_level": Double(amount) / 3200, "output_total": total,
                "activity_epoch": 1, "activity_age": 0.3, "activity_source": "log",
                "background_agents": i == 1 ? 2 : 0, "background_agents_total": i == 1 ? 3 : 0,
                "background_terminals": i == 0 ? 2 : 0, "background_agents_observable": true,
                "background_terminals_observable": !local,
                "response": response, "usage_metrics": rootUsage
            ])
        }
        let connection: [[String: Any]] = ["remote", "local"].map {
            ["id": $0, "label": $0 == "local" ? "本机" : "远端", "connected": true, "connection_state": "live", "message": "演示数据"]
        }
        let running = states.filter { $0 == "running" }.count
        let roots = rows.compactMap { $0["usage_metrics"] as? [String: Any] }
        func usageSum(_ key: String) -> Int { roots.reduce(0) { $0 + ($1[key] as? Int ?? 0) } }
        func rate(_ key: String) -> Double { roots.reduce(0) { $0 + ($1[key] as? Double ?? 0) } }
        let usage: [String: Any] = [
            "utilization_percent": Double(running) / 3 * 100,
            "running_sessions": running, "known_sessions": 3, "monitored_sessions": 3,
            "usage_covered": 3, "tools_covered": 3, "usage_sessions": 3,
            "total_tokens": usageSum("total_tokens"), "input_tokens": usageSum("input_tokens"),
            "output_tokens": usageSum("output_tokens"), "cached_input_tokens": usageSum("cached_input_tokens"),
            "cache_write_input_tokens": usageSum("cache_write_input_tokens"), "consumption_per_second": rate("consumption_per_second"),
            "upstream_tokens_per_second": rate("upstream_tokens_per_second"),
            "window_tokens": usageSum("window_tokens"), "window_output_tokens": usageSum("window_output_tokens"),
            "tools_per_minute": rate("tools_per_minute"), "live_usage_covered": 3,
            "live_tools_covered": 3, "window_seconds": 30, "work_window_seconds": 60,
            "scope": "followed_root_sessions"
        ]
        let data = try! JSONSerialization.data(withJSONObject: [
            "rows": rows, "events": [], "error_events": [], "active_error_events": [],
            "followed": ["atlas", "nova"], "connected": true, "message": "演示数据 · 本机与远端已连接",
            "host": "demo-server", "received_frames": step + 1, "connection_state": "live",
            "connections": connection, "local_sessions": [["thread_id": "demo-ab12", "session": "Meridian"]],
            "metrics": usage
        ])
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try! decoder.decode(Snapshot.self, from: data)
    }
    static func groupedSnapshot(waiting: Bool) -> Snapshot {
        var result = snapshot(12)
        let members = result.rows.prefix(2).map(\.monitorKey)
        let group = SessionResponseGroup(id: "demo-shared-task", name: "同一项目", members: members,
                                         count: 2, knownMembers: 2, runningMembers: waiting ? 0 : 1,
                                         parallelConflict: false, kind: waiting ? "waiting" : "running",
                                         episodeId: waiting ? "demo-shared-wait-1" : nil,
                                         since: 1_700_000_000 - (waiting ? 360 : 640),
                                         elapsedSeconds: waiting ? 360 : 640,
                                         observed: true, overdue: waiting, scope: "observed_monitoring",
                                         idleSeconds: waiting ? 480 : 120, runSeconds: waiting ? 1200 : 1800,
                                         idleRunRatio: waiting ? 0.4 : 1.0 / 15,
                                         efficiencyPercent: waiting ? 100.0 * 1200 / 1680 : 93.75,
                                         reminderSuppressed: false)
        for index in 0..<2 {
            if waiting || index == 0 {
                result.rows[index].state = "idle"; result.rows[index].status = "等待输入"
                result.rows[index].response = SessionResponse(kind: "waiting", episodeId: "individual-\(index)",
                                                              since: 1_700_000_000 - 400, elapsedSeconds: 400,
                                                              observed: true, overdue: false, scope: "observed_monitoring",
                                                              idleSeconds: 500, runSeconds: 1000, idleRunRatio: 0.5,
                                                              efficiencyPercent: 66.7, reminderSuppressed: true)
                result.rows[index].usageMetrics?.consumptionPerSecond = 0
                result.rows[index].usageMetrics?.upstreamTokensPerSecond = 0
                result.rows[index].usageMetrics?.windowTokens = 0
                result.rows[index].usageMetrics?.windowOutputTokens = 0
                result.rows[index].usageMetrics?.toolsPerMinute = 0
            }
            result.rows[index].responseGroup = group
        }
        result.rows = Array(result.rows.prefix(2)); result.followed = ["atlas"]
        result.metrics?.runningSessions = waiting ? 0 : 1
        result.metrics?.utilizationPercent = waiting ? 0 : 100
        result.metrics?.knownSessions = 1; result.metrics?.monitoredSessions = 1
        result.metrics?.usageSessions = 2; result.metrics?.usageCovered = 2; result.metrics?.toolsCovered = 2
        result.metrics?.liveUsageCovered = 2; result.metrics?.liveToolsCovered = 2
        result.metrics?.inputTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.inputTokens ?? 0) }
        result.metrics?.outputTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.outputTokens ?? 0) }
        result.metrics?.totalTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.totalTokens ?? 0) }
        result.metrics?.cachedInputTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.cachedInputTokens ?? 0) }
        result.metrics?.cacheWriteInputTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.cacheWriteInputTokens ?? 0) }
        result.metrics?.consumptionPerSecond = result.rows.reduce(0) { $0 + ($1.usageMetrics?.consumptionPerSecond ?? 0) }
        result.metrics?.upstreamTokensPerSecond = result.rows.reduce(0) { $0 + ($1.usageMetrics?.upstreamTokensPerSecond ?? 0) }
        result.metrics?.windowTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.windowTokens ?? 0) }
        result.metrics?.windowOutputTokens = result.rows.reduce(0) { $0 + ($1.usageMetrics?.windowOutputTokens ?? 0) }
        result.metrics?.toolsPerMinute = result.rows.reduce(0) { $0 + ($1.usageMetrics?.toolsPerMinute ?? 0) }
        return result
    }
    /// Deterministic fixture for the real signal renderer; never starts a collector.
    static func signalSnapshot(_ step: Int) -> Snapshot {
        var value = snapshot(3)
        let output = [0, 8, 40, 160, 12, 0, 380, 65, 25, 920, 100, 0]
        let io = [0, 1024, 8192, 16_384, 65_536, 2048, 0, 262_144, 4096, 131_072, 0, 8192]
        let network = [0, 140, 820, 8192, 12_288, 900, 0, 1600, 24_576, 2200, 0, 640]
        func sum(_ pattern: [Int], shift: Int) -> Int {
            guard step > 0 else { return 0 }
            return (1...step).reduce(0) { $0 + pattern[($1 + shift) % pattern.count] }
        }
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        for index in value.rows.indices {
            let shift = index * 3
            let identity = "demo-signals-\(index)"
            value.rows[index].outputTotal = 1000 + sum(output, shift: shift)
            value.rows[index].dialogueOutput = DialogueOutput(epoch: identity + "-body", sampleId: step + 1,
                totalChars: 1000 + sum(output, shift: shift), available: true, complete: false,
                sampleAgeSeconds: 0, source: "assistant_screen")
            value.rows[index].usageMetrics?.rootIdentity = identity
            value.rows[index].usageMetrics?.toolCompletions = 100 + (0...step).filter { ($0 + shift) % 9 == 4 }.count
            value.rows[index].usageMetrics?.toolsEpoch = 1
            value.rows[index].usageMetrics?.activeTools = step % 9 < 4 ? 1 : 0
            let dictionary: [String: Any] = [
                "identity": identity, "epoch": identity, "sample_id": step + 1, "sample_age_seconds": 0, "interval_seconds": 0.5,
                "cpu_percent": [0, 12, 35, 90, 160, 40, 0, 240, 70, 320, 25, 0][(step + shift) % 12],
                "cpu_available": true, "cpu_complete": true,
                "gpu_utilization_percent": [0, 20, 55, 90, 30, 0, 85, 60, 0, 40, 95, 15][(step + shift + 4) % 12],
                "gpu_available": true, "gpu_complete": true, "gpu_sample_id": step + 1, "gpu_sample_age_seconds": 0, "gpu_poll_interval_seconds": 0.5,
                "scope": "process_tree", "process_count": index + 2, "tree_complete": true,
                "memory_bytes": (240 + index * 140) * 1_048_576, "host_memory_bytes": 8_589_934_592,
                "memory_available": true, "memory_complete": true,
                "io_read_bytes": 1024 + sum(io, shift: shift), "io_write_bytes": 2048 + sum(io, shift: shift + 2) / 4,
                "disk_read_bytes": 1024 + sum(io, shift: shift), "disk_write_bytes": 2048 + sum(io, shift: shift + 2) / 4,
                "disk_read_per_second": io[(step + shift) % io.count] * 2, "disk_write_per_second": io[(step + shift + 2) % io.count] / 2,
                "io_read_per_second": io[(step + shift) % io.count] * 2,
                "io_write_per_second": io[(step + shift + 2) % io.count] / 2,
                "io_available": true, "io_complete": true,
                "network_rx_bytes": 1000 + sum(network, shift: shift),
                "network_tx_bytes": 1000 + sum(network, shift: shift + 5) / 3,
                "network_rx_per_second": network[(step + shift) % network.count] * 2,
                "network_tx_per_second": network[(step + shift + 5) % network.count] * 2 / 3,
                "network_available": true, "network_complete": true, "network_shared": false
            ]
            value.rows[index].resources = try! decoder.decode(ResourceMetrics.self, from: JSONSerialization.data(withJSONObject: dictionary))
        }
        return value
    }

    static func exportSignals(_ model: PanelModel, folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let suite = "agent-orbit-signal-export-" + UUID().uuidString
        guard let preferences = UserDefaults(suiteName: suite) else { return }
        defer { preferences.removePersistentDomain(forName: suite) }
        preferences.set(false, forKey: "orbitMiniMode")
        for theme in ThemePalette.names {
            prepare(model, theme: theme)
            model.settings.waveResources = true; model.settings.waveStyle = "bars"
            model.settings.waveHeight = 1.3
            let view = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176), preferences: preferences)
            for step in 0...48 {
                model.apply(signalSnapshot(step))
                view.update(model, now: 1000 + Double(step) * 0.5)
            }
            try capture(view, size: view.bounds.size, path: folder.appendingPathComponent("signals-" + theme + ".png"), scale: 2)
            if theme == "neon" {
                try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 470)), size: NSSize(width: 320, height: 470), path: folder.appendingPathComponent("signals-panel.png"))
                try capture(NSHostingView(rootView: WaveGuideView()), size: NSSize(width: 700, height: 760), path: folder.appendingPathComponent("signals-manual.png"))
            }
        }
        print("Exported synthetic signal screenshots using the native renderer")
    }

    static func capture(_ view: NSView, size: NSSize, path: URL, scale: CGFloat = 1) throws {
        let logicalBounds = view.bounds
        let isOrbit = view is BubbleView
        let physicalSize = isOrbit ? NSSize(width: ceil(logicalBounds.width * scale), height: ceil(logicalBounds.height * scale)) : size
        let window = NSWindow(contentRect: NSRect(origin: NSPoint(x: -5000, y: -5000), size: physicalSize), styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false; window.isOpaque = false; window.backgroundColor = .clear
        defer { window.orderOut(nil); window.contentView = nil; window.close() }
        view.frame = NSRect(origin: .zero, size: physicalSize); window.contentView = view
        if isOrbit { view.bounds = logicalBounds }
        window.orderFrontRegardless()
        RunLoop.current.run(until: Date().addingTimeInterval(0.02))
        view.layoutSubtreeIfNeeded(); view.displayIfNeeded()
        guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(physicalSize.width * 2), pixelsHigh: Int(physicalSize.height * 2), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { throw NSError(domain: "Demo", code: 1) }
        // The bitmap's point coordinates must match the view's logical bounds;
        // only its pixel dimensions use the scaled on-screen size.
        bitmap.size = view.bounds.size
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let bytes = bitmap.representation(using: .png, properties: [:]) else { throw NSError(domain: "Demo", code: 2) }
        try bytes.write(to: path)
    }
    static func export(_ model: PanelModel, folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        // A volatile override makes exports repeatable without changing saved preferences.
        let previousArguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        var demoArguments = previousArguments; demoArguments["sessionUsageOverviewExpanded"] = false
        UserDefaults.standard.setVolatileDomain(demoArguments, forName: UserDefaults.argumentDomain)
        defer { UserDefaults.standard.setVolatileDomain(previousArguments, forName: UserDefaults.argumentDomain) }
        for theme in ThemePalette.names {
            prepare(model, theme: theme); model.apply(snapshot(9))
            let view = NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430))
            try capture(view, size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-\(theme).png"))
        }
        // Exercise compact states without reading real sessions or starting a collector.
        prepare(model)
        var missing = snapshot(9); missing.metrics = nil
        model.apply(missing)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-no-metrics.png"))
        var offline = snapshot(9); offline.connected = false; offline.connectionState = "reconnecting"; offline.message = "连接中断，正在重连"
        offline.connections = offline.connections?.map { connection in
            var value = connection; value.connected = false; value.connectionState = "reconnecting"; return value
        }
        model.apply(offline)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-offline-metrics.png"))
        var partial = snapshot(9); partial.connectionState = "partial"; partial.message = "本机在线 · 远端重连中"
        partial.connections = partial.connections?.map { connection in
            var value = connection
            if value.id == "remote" { value.connected = false; value.connectionState = "reconnecting" }
            return value
        }
        partial.metrics?.usageCovered = 2; partial.metrics?.toolsCovered = 2
        partial.metrics?.liveUsageCovered = 1; partial.metrics?.liveToolsCovered = 1
        partial.metrics?.utilizationPercent = 100; partial.metrics?.knownSessions = 1; partial.metrics?.runningSessions = 1
        model.apply(partial)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-partial-metrics.png"))
        var many = snapshot(3)
        for index in 3..<8 {
            var row = many.rows[index % 3]
            row.paneId = "remote:demo-\(index)"; row.threadId = "demo-\(index)"
            row.source = "remote"; row.groupId = "remote:sector-\(index)"; row.session = "sector-\(index)"
            many.rows.append(row); many.followed.append(row.session)
        }
        let manyRunning = many.rows.filter { $0.state == "running" }.count
        many.metrics?.monitoredSessions = 8; many.metrics?.knownSessions = 8; many.metrics?.runningSessions = manyRunning
        many.metrics?.utilizationPercent = Double(manyRunning) / 8 * 100; many.metrics?.usageSessions = 8
        many.metrics?.usageCovered = 8; many.metrics?.toolsCovered = 8
        many.metrics?.liveUsageCovered = 8; many.metrics?.liveToolsCovered = 8
        many.metrics?.totalTokens = 9_876_543_210; many.metrics?.inputTokens = 9_000_000_000
        many.metrics?.outputTokens = 876_543_210; many.metrics?.cachedInputTokens = 2_000_000_000
        many.metrics?.cacheWriteInputTokens = 1_000_000_000; many.metrics?.consumptionPerSecond = 123_456.7
        many.metrics?.upstreamTokensPerSecond = 98_765.4; many.metrics?.toolsPerMinute = 456.7
        model.apply(many)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-many-metrics.png"))
        let manyBubble = BubbleView(frame: NSRect(x: 0, y: 0, width: 240, height: 240)); manyBubble.update(model)
        try capture(manyBubble, size: NSSize(width: 240, height: 240), path: folder.appendingPathComponent("orbit-many-metrics.png"), scale: CGFloat(model.settings.bubbleScale))
        demoArguments["sessionUsageOverviewExpanded"] = false
        UserDefaults.standard.setVolatileDomain(demoArguments, forName: UserDefaults.argumentDomain)
        model.apply(snapshot(9))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 300, height: 260)), size: NSSize(width: 300, height: 260), path: folder.appendingPathComponent("panel-minimum-collapsed.png"))
        demoArguments["sessionUsageOverviewExpanded"] = true
        UserDefaults.standard.setVolatileDomain(demoArguments, forName: UserDefaults.argumentDomain)
        try capture(NSHostingView(rootView: UsageOverview(metrics: model.metrics, connected: true).details), size: NSSize(width: 290, height: 530), path: folder.appendingPathComponent("usage-details.png"))
        model.apply(snapshot(9))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-usage-expanded.png"))
        demoArguments["sessionUsageOverviewExpanded"] = false
        UserDefaults.standard.setVolatileDomain(demoArguments, forName: UserDefaults.argumentDomain)
        var waiting = snapshot(9)
        for index in waiting.rows.indices where waiting.rows[index].response?.isPending == true {
            waiting.rows[index].response?.elapsedSeconds = Double(45 + index * 30)
            waiting.rows[index].response?.overdue = false
        }
        model.apply(waiting)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-response-waiting.png"))
        model.apply(snapshot(12))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-response-overdue.png"))
        var answered = snapshot(12)
        for index in answered.rows.indices {
            answered.rows[index].state = "running"; answered.rows[index].status = "运行中"
            answered.rows[index].response = SessionResponse(kind: "running", episodeId: nil, since: 1_700_000_000, elapsedSeconds: Double(10 + index * 8), observed: true, overdue: false)
            answered.rows[index].preview = ["已收到后续输入，继续执行新任务。"]
            answered.rows[index].working = "正在执行后续任务"
        }
        answered.metrics?.runningSessions = 3; answered.metrics?.utilizationPercent = 100
        model.apply(answered)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-response-answered.png"))
        let syntheticGroup = SessionGroup(id: "demo-shared-task", name: "同一项目", members: ["remote:atlas", "local:demo-ab12"])
        model.settings.sessionGroups = [syntheticGroup]
        model.apply(groupedSnapshot(waiting: false))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-response-group-busy.png"))
        model.apply(groupedSnapshot(waiting: true))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-response-group-waiting.png"))
        let groupSettings = NSHostingView(rootView: VStack(alignment: .leading, spacing: 0) {
            Text("监控设置 · 会话分组").font(.title3.bold()).foregroundColor(ThemePalette.ui(.text)).padding(18)
            SettingsView(model: model, initialTab: 0) {}.monitoring
        }.frame(width: 540, height: 1120).background(ThemePalette.ui(.surface)).preferredColorScheme(ThemePalette.scheme))
        try capture(groupSettings, size: NSSize(width: 540, height: 1120), path: folder.appendingPathComponent("settings-monitor-groups.png"))
        model.settings.sessionGroups = []
        model.apply(snapshot(12))
        let orbitSuite = "agent-orbit-demo-orbit-" + UUID().uuidString
        if let orbitPreferences = UserDefaults(suiteName: orbitSuite) {
            defer { orbitPreferences.removePersistentDomain(forName: orbitSuite) }
            orbitPreferences.set(false, forKey: "orbitMiniMode")
            let full = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176), preferences: orbitPreferences)
            full.update(model)
            try capture(full, size: NSSize(width: 176, height: 176), path: folder.appendingPathComponent("orbit-global.png"), scale: CGFloat(model.settings.bubbleScale))
            orbitPreferences.set(true, forKey: "orbitMiniMode")
            let mini = BubbleView(frame: NSRect(x: 0, y: 0, width: 72, height: 72), preferences: orbitPreferences)
            mini.update(model)
            try capture(mini, size: NSSize(width: 72, height: 72), path: folder.appendingPathComponent("orbit-mini.png"), scale: CGFloat(model.settings.bubbleScale))
        }
        model.apply(offline)
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 300, height: 260)), size: NSSize(width: 300, height: 260), path: folder.appendingPathComponent("panel-response-offline.png"))
        prepare(model); model.apply(snapshot(3))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-running.png"))
        model.apply(snapshot(15))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-error.png"))
        model.apply(snapshot(9))
        try capture(NSHostingView(rootView: SettingsView(model: model, initialTab: 1) {}), size: NSSize(width: 540, height: 640), path: folder.appendingPathComponent("settings.png"))
        let bubble = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176))
        for step in 0..<24 {
            model.apply(snapshot(step)); bubble.update(model)
            try capture(bubble, size: NSSize(width: 176, height: 176), path: folder.appendingPathComponent(String(format: "orbit-%02d.png", step)), scale: CGFloat(model.settings.bubbleScale))
            Thread.sleep(forTimeInterval: 1.02)
        }
        for style in ["bars", "curve", "dots", "plasma", "aurora", "radar"] {
            model.settings.waveStyle = style; model.settings.reduceMotion = false
            bubble.update(model)
            try capture(bubble, size: NSSize(width: 176, height: 176), path: folder.appendingPathComponent("orbit-wave-" + style + ".png"), scale: CGFloat(model.settings.bubbleScale))
        }
        print("Exported privacy-safe native demo frames")
    }
}
