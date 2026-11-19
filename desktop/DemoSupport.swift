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
        let data = try! JSONSerialization.data(withJSONObject: [
            "rows": rows, "events": [], "error_events": [], "active_error_events": [],
            "followed": ["atlas", "nova"], "connected": true, "message": "演示数据 · 本机与远端已连接",
            "host": "demo-server", "received_frames": step + 1, "connection_state": "live",
            "connections": connection, "local_sessions": [["thread_id": "demo-ab12", "session": "Meridian"]]
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
        for theme in ThemePalette.names {
            prepare(model, theme: theme); model.apply(snapshot(9))
            let view = NSHostingView(rootView: PanelView(model: model).frame(width: 320, height: 430))
            try capture(view, size: NSSize(width: 320, height: 430), path: folder.appendingPathComponent("panel-\(theme).png"))
        }
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
