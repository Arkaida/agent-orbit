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
        ThemePalette.configure(theme: theme, statusStyle: settings.orbitLabelColor)
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
                "background_terminals_observable": !local
            ])
        }
        let connection: [[String: Any]] = ["remote", "local"].map {
            ["id": $0, "label": $0 == "local" ? "本机" : "远端", "connected": true, "connection_state": "live", "message": "演示数据"]
        }
        let running = states.filter { $0 == "running" }.count
        let usage: [String: Any] = [
            "utilization_percent": Double(running) / 3 * 100,
            "running_sessions": running, "known_sessions": 3, "monitored_sessions": 3,
            "usage_covered": 3, "tools_covered": 3, "usage_sessions": 3,
            "total_tokens": 186_420 + step * 280, "input_tokens": 172_320 + step * 240,
            "output_tokens": 14_100 + step * 40, "cached_input_tokens": 125_800,
            "cache_write_input_tokens": 11_200, "consumption_per_second": 62.4 + Double(step % 5) * 8.5,
            "upstream_tokens_per_second": 9.3 + Double(step % 4) * 2.1,
            "tools_per_minute": 4.0 + Double(step % 3), "live_usage_covered": 3,
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
    static func capture(_ view: NSView, size: NSSize, path: URL) throws {
        let window = NSWindow(contentRect: NSRect(origin: NSPoint(x: -5000, y: -5000), size: size), styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false; window.isOpaque = false; window.backgroundColor = .clear
        view.frame = NSRect(origin: .zero, size: size); window.contentView = view
        view.layoutSubtreeIfNeeded(); view.displayIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { throw NSError(domain: "Demo", code: 1) }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let bytes = bitmap.representation(using: .png, properties: [:]) else { throw NSError(domain: "Demo", code: 2) }
        try bytes.write(to: path)
        window.contentView = nil
    }
    static func export(_ model: PanelModel, folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        // A volatile override makes exports repeatable without changing saved preferences.
        let previousArguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        var demoArguments = previousArguments; demoArguments["usageOverviewExpanded"] = true
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
        try capture(manyBubble, size: NSSize(width: 240, height: 240), path: folder.appendingPathComponent("orbit-many-metrics.png"))
        demoArguments["usageOverviewExpanded"] = false
        UserDefaults.standard.setVolatileDomain(demoArguments, forName: UserDefaults.argumentDomain)
        model.apply(snapshot(9))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 300, height: 260)), size: NSSize(width: 300, height: 260), path: folder.appendingPathComponent("panel-minimum-collapsed.png"))
        demoArguments["usageOverviewExpanded"] = true
        UserDefaults.standard.setVolatileDomain(demoArguments, forName: UserDefaults.argumentDomain)
        try capture(NSHostingView(rootView: UsageOverview(metrics: model.metrics, connected: true).details), size: NSSize(width: 290, height: 530), path: folder.appendingPathComponent("usage-details.png"))
        prepare(model); model.apply(snapshot(3))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-running.png"))
        model.apply(snapshot(15))
        try capture(NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430)), size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-error.png"))
        model.apply(snapshot(9))
        try capture(NSHostingView(rootView: SettingsView(model: model, initialTab: 1) {}), size: NSSize(width: 540, height: 640), path: folder.appendingPathComponent("settings.png"))
        let bubble = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176))
        for step in 0..<24 {
            model.apply(snapshot(step)); bubble.update(model)
            try capture(bubble, size: NSSize(width: 176, height: 176), path: folder.appendingPathComponent(String(format: "orbit-%02d.png", step)))
            Thread.sleep(forTimeInterval: 1.02)
        }
        print("Exported privacy-safe native demo frames")
    }
}
