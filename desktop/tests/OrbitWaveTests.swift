import AppKit

/// Only the view's inputs are replaced. The view, DTOs and palette are shipped code.
/// No process, collector, real session, network connection or personal setting is read.
final class PanelModel {
    var settings = AppSettings()
    var connected = true
    var connectionState = "live"
    var metrics: UsageMetrics?
    var rows: [Pane] = []
    var followed: [String] = []
    var message = "Synthetic orbit wave fixture"
    var disconnectedSources = Set<String>()
    var manualRequests: [(ManualRunAction, ManualRunTarget)] = []
    func isConnected(_ pane: Pane) -> Bool {
        connected && !disconnectedSources.contains(pane.source ?? "remote")
    }
    func requestManualRunning(_ action: ManualRunAction, target: ManualRunTarget) {
        manualRequests.append((action, target))
    }
}

@MainActor
private final class OrbitWaveTestSuite {
    var assertions = 0
    var failures: [String] = []
    private let preferences: UserDefaults
    private let domain: String

    init() {
        domain = "AgentOrbit.OrbitWaveTests." + UUID().uuidString
        preferences = UserDefaults(suiteName: domain)!
        preferences.register(defaults: ["orbitMiniMode": false])
    }

    func cleanUp() { preferences.removePersistentDomain(forName: domain) }

    func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        if !condition() { failures.append(message) }
    }

    private func field(_ value: Any, _ name: String) -> Any? {
        Mirror(reflecting: value).children.first { $0.label == name }?.value
    }

    private func decode<T: Decodable>(_ value: [String: Any], as type: T.Type) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: JSONSerialization.data(withJSONObject: value))
    }

    private func group(id: String = "pair", idle: Double? = 125, run: Double? = 375) -> [String: Any] {
        var value: [String: Any] = ["id": id, "name": "Synthetic long grouped session", "kind": "running",
                                    "observed": true, "overdue": false, "count": 2]
        if let idle = idle { value["idle_seconds"] = idle }
        if let run = run { value["run_seconds"] = run }
        return value
    }

    private func pane(_ name: String, state: String = "running", source: String = "remote",
                      group: [String: Any]? = nil, idle: Double? = 125, run: Double? = 375) throws -> Pane {
        var response: [String: Any] = ["kind": state == "idle" ? "waiting" : state,
                                       "observed": true, "overdue": false]
        if let idle = idle { response["idle_seconds"] = idle }
        if let run = run { response["run_seconds"] = run }
        let identity = "synthetic-resource-" + name
        var value: [String: Any] = [
            "pane_id": "%synthetic-" + name, "session": name, "source": source,
            "group_id": source + ":" + name, "state": state, "status": state,
            "preview": [], "agent": "codex", "available": true,
            "activity_epoch": 1, "output_total": 100, "activity_age": 0,
            "activity_source": "log", "response": response,
            "usage_metrics": ["root_identity": "synthetic-root-" + name,
                              "tools_covered": 1, "live_tools_covered": 1,
                              "tool_completions": 100, "active_tools": 0, "tools_epoch": 1],
            "resources": ["identity": identity, "epoch": identity, "sample_id": 1,
                          "sample_age_seconds": 0, "interval_seconds": 1.0,
                          "cpu_percent": 0, "cpu_available": true, "cpu_complete": true,
                          "gpu_utilization_percent": 0, "gpu_available": true, "gpu_complete": true,
                          "gpu_sample_id": 1, "gpu_sample_age_seconds": 0, "gpu_interval_seconds": 1.0,
                          "disk_read_bytes": 100, "disk_write_bytes": 100,
                          "disk_read_per_second": 0, "disk_write_per_second": 0,
                          "memory_bytes": 67_108_864,
                          "host_memory_bytes": 8_589_934_592,
                          "memory_available": true, "memory_complete": true,
                          "io_read_bytes": 100, "io_write_bytes": 100,
                          "io_read_per_second": 0, "io_write_per_second": 0,
                          "io_available": true, "io_complete": true,
                          "network_rx_bytes": 100, "network_tx_bytes": 100,
                          "network_rx_per_second": 0, "network_tx_per_second": 0,
                          "network_available": true, "network_complete": true,
                          "network_shared": false]
        ]
        if let group = group { value["response_group"] = group }
        return try decode(value, as: Pane.self)
    }

    private func fixture(_ rows: [Pane], now: TimeInterval? = nil) -> (PanelModel, BubbleView) {
        let model = PanelModel()
        model.rows = rows
        model.settings.waveResources = true
        model.settings.waveStyle = "bars"
        model.settings.reduceMotion = true
        let view = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176), preferences: preferences)
        view.update(model, now: now)
        return (model, view)
    }

    private func advance(_ pane: inout Pane, io: Int = 0, network: Int = 0, tools: Int = 0, output: Int = 0) {
        if var resources = pane.resources {
            resources.sampleId = (resources.sampleId ?? 0) + 1
            resources.diskReadBytes = (resources.diskReadBytes ?? 0) + io
            resources.diskReadPerSecond = Double(io)
            resources.cpuPercent = Double(output)
            resources.gpuUtilizationPercent = Double(tools)
            resources.gpuSampleId = (resources.gpuSampleId ?? 0) + 1
            resources.networkRxBytes = (resources.networkRxBytes ?? 0) + network
            resources.networkRxPerSecond = Double(network)
            pane.resources = resources
        }
        if var usage = pane.usageMetrics {
            usage.toolCompletions = (usage.toolCompletions ?? 0) + tools
            pane.usageMetrics = usage
        }
        pane.outputTotal = (pane.outputTotal ?? 0) + output
    }

    /// Reflect observations without copying the implementation's reducer.
    private func amounts(_ value: Any) -> Double {
        if let amount = field(value, "amount") as? Double { return amount }
        if let amount = field(value, "amount") as? Int { return Double(amount) }
        return Mirror(reflecting: value).children.reduce(0) { $0 + amounts($1.value) }
    }

    private func roleAmounts(_ value: Any, _ role: ResourceRole) -> Double {
        let mirror = Mirror(reflecting: value)
        if mirror.displayStyle == .dictionary {
            return mirror.children.reduce(0) { sum, child in
                let tuple = Array(Mirror(reflecting: child.value).children)
                guard tuple.count == 2 else { return sum }
                if let key = tuple[0].value as? ResourceRole {
                    return sum + (key == role ? amounts(tuple[1].value) : 0)
                }
                return sum + roleAmounts(tuple[1].value, role)
            }
        }
        return mirror.children.reduce(0) { $0 + roleAmounts($1.value, role) }
    }

    private func marks(_ view: BubbleView, _ role: ResourceRole) -> Double {
        guard let value = field(view, "resourceMarks") else {
            failures.append("Could not inspect the view's resource observations")
            return .nan
        }
        return roleAmounts(value, role)
    }

    private func output(_ view: BubbleView) -> Double {
        field(view, "outputMarks").map(amounts) ?? .nan
    }

    private func body(_ epoch: String, sample: Int = 1, total: Int = 100) -> DialogueOutput {
        DialogueOutput(epoch: epoch, sampleId: sample, totalChars: total, available: true,
                       complete: false, sampleAgeSeconds: 0, source: "assistant_screen")
    }

    private func nextBody(_ pane: inout Pane, amount: Int) {
        guard var body = pane.dialogueOutput else { return }
        body.sampleId = (body.sampleId ?? 0) + 1
        body.totalChars = (body.totalChars ?? 0) + amount
        pane.dialogueOutput = body
    }

    private func bodyAmount(_ view: BubbleView, key: String) -> Double {
        view.resourceSignals(for: key, count: 24)[.output]?.reduce(0) { $0 + $1.rate } ?? 0
    }

    private func observedBodyAmount(_ view: BubbleView) -> Double {
        field(view, "dialogueMarks").map(amounts) ?? .nan
    }

    private func renderedPixels(width: Int = 200, height: Int = 200, draw: () -> Void) -> Data {
        let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: width * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
        let old = NSGraphicsContext.current
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
        defer { NSGraphicsContext.current = old }
        draw()
        return Data(bytes: context.data!, count: width * height * 4)
    }

    private func bubblePixels(_ view: BubbleView) -> Data {
        renderedPixels(width: max(1, Int(ceil(view.bounds.width))), height: max(1, Int(ceil(view.bounds.height)))) {
            view.draw(view.bounds)
        }
    }

    private func alphaPixels(_ pixels: Data) -> [UInt8] {
        pixels.enumerated().compactMap { $0.offset % 4 == 3 ? $0.element : nil }
    }

    func waitingAndResume() throws {
        let (model, view) = fixture([try pane("waiting", state: "idle")], now: 1000)
        var clock: TimeInterval = 1000
        func tick() { clock += 1; view.update(model, now: clock) }
        advance(&model.rows[0], io: 10, network: 11, tools: 1, output: 12)
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0,
              "A waiting process's I/O, TCP and tool counters must not create work waves")
        check(output(view) == 0, "Waiting terminal output must not become a work wave")
        model.rows[0].state = "running"
        advance(&model.rows[0], io: 1000, network: 1000, tools: 10, output: 1000)
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0 && output(view) == 0,
              "Resume establishes fresh baselines without replaying idle counters")
        advance(&model.rows[0], io: 7, network: 11, tools: 1, output: 13)
        tick()
        check(marks(view, .disk) == 7 && marks(view, .network) == 11 && marks(view, .gpu) == 1 && output(view) == 13,
              "Continuous running samples contribute only their confirmed deltas")
        tick()
        check(marks(view, .disk) == 7 && marks(view, .network) == 11 && marks(view, .gpu) == 1 && output(view) == 13,
              "Repeated bridge refreshes must not duplicate a real sample")
        model.rows[0].state = "idle"
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0 && output(view) == 0,
              "Completion immediately clears waves, rather than freezing recent work")
        model.rows[0].state = "running"
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0 && output(view) == 0,
              "An immediate new run must not revive a previous turn's waves")
    }

    func groupedMembersAndContinuity() throws {
        let shared = group()
        let (model, view) = fixture([try pane("alpha", group: shared),
                                     try pane("beta", state: "idle", source: "local", group: shared)], now: 1000)
        var clock: TimeInterval = 1000
        func tick() { clock += 1; view.update(model, now: clock) }
        advance(&model.rows[1], io: 500, network: 600, tools: 4, output: 700)
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0 && output(view) == 0,
              "An idle member's heartbeat must not animate its running group")
        advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
        tick()
        check(marks(view, .disk) == 10 && marks(view, .network) == 20 && marks(view, .gpu) == 1 && output(view) == 30,
              "A running group member contributes its own work")
        model.rows[1].state = "running"
        advance(&model.rows[1], io: 1000, network: 1000, tools: 10, output: 1000)
        tick()
        advance(&model.rows[1], io: 15, network: 25, tools: 2, output: 35)
        tick()
        check(marks(view, .disk) == 25 && marks(view, .network) == 45 && marks(view, .gpu) == 3 && output(view) == 65,
              "A resumed member joins after its own baseline without replacing its partner")
        model.rows[0].resources?.identity = "synthetic-reconnected-alpha"
        model.rows[0].resources?.epoch = "synthetic-reconnected-alpha"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.diskReadBytes = 100_000
        model.rows[0].resources?.networkRxBytes = 100_000
        tick()
        check(marks(view, .disk) == 15 && marks(view, .network) == 25,
              "A new resource identity clears only its old history and creates no catch-up pulse")
        advance(&model.rows[0], io: 9, network: 13)
        tick()
        check(marks(view, .disk) == 24 && marks(view, .network) == 38,
              "A new identity contributes work after its own continuous baseline")
        model.rows[0].resources?.epoch = "synthetic-new-epoch-same-identity"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.diskReadBytes = 200_000
        model.rows[0].resources?.networkRxBytes = 200_000
        tick()
        check(marks(view, .disk) == 15 && marks(view, .network) == 25,
              "An epoch reset also clears its member's history when the resource identity string stays the same")
        model.rows[0].usageMetrics?.toolsEpoch = 2
        model.rows[0].usageMetrics?.toolCompletions = 100_000
        tick()
        check(marks(view, .gpu) == 2, "A tool continuity reset preserves another group member's history")
        model.disconnectedSources.insert("remote")
        tick()
        check(marks(view, .disk) == 15 && marks(view, .network) == 25 && marks(view, .gpu) == 2,
              "Disconnecting one source must preserve the live partner's observations")
        model.disconnectedSources.insert("local")
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0,
              "A wholly disconnected group cannot retain live work observations")
    }

    func cachedResumeBaseline() throws {
        let (model, view) = fixture([try pane("cached-resume", state: "idle")], now: 1000)
        var clock: TimeInterval = 1000
        func tick() { clock += 1; view.update(model, now: clock) }
        advance(&model.rows[0], io: 100, network: 100)
        tick()
        model.rows[0].state = "running"
        // The bridge can redraw the same resource sample many times after a state change.
        for _ in 0..<4 { tick() }
        advance(&model.rows[0], io: 1000, network: 1000)
        tick()
        check(marks(view, .disk) == 0 && marks(view, .network) == 0,
              "A cached idle sample cannot arm a delta spanning the first fresh resumed sample")
        advance(&model.rows[0], io: 7, network: 11)
        tick()
        check(marks(view, .disk) == 7 && marks(view, .network) == 11,
              "Activity resumes after two proven running resource samples")
    }

    func independentStateGates() throws {
        for state in ["idle", "approval", "paused", "unknown", "error", "interrupted", "missing"] {
            let (model, view) = fixture([try pane("state-gate", state: state)])
            advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
            view.update(model)
            check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0 && output(view) == 0,
                  "Nonrunning state \(state) must remain quiet even when process counters grow")
        }
        let (model, view) = fixture([try pane("warning")])
        model.rows[0].attentionLevel = "warning"
        advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
        view.update(model)
        check(marks(view, .disk) == 0 && marks(view, .network) == 0 && marks(view, .gpu) == 0 && output(view) == 0,
              "A running raw state with an attention warning must not animate as normal work")
    }

    func nameRingsAndExactTiming() throws {
        let rows = try (0..<8).map { try pane("synthetic-long-session-name-\($0)") }
        let (_, view) = fixture(rows)
        let rings = field(view, "rings").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
        check(rings.count == 2, "Eight monitored positions must retain the two name circles")
        let radii = rings.compactMap { field($0, "radius") as? CGFloat }
        check(radii.count == 2 && radii[0] > radii[1] && radii[1] > 0,
              "Both name circles must have valid distinct radii")
        let indices = rings.flatMap { field($0, "indices") as? [Int] ?? [] }
        check(indices.sorted() == Array(rows.indices), "Each monitored position belongs to exactly one name circle")
        let description = view.accessibilityLabel() ?? ""
        check(description.contains("2分5秒") && description.contains("6分15秒"),
              "Removing the time caption must preserve observed idle and run totals to the second")
        let (grouped, groupedView) = fixture([try pane("group-a", group: group()),
                                            try pane("group-b", source: "local", group: group())])
        check(groupedView.accessibilityLabel()?.contains("2分5秒") == true,
              "A group exposes its shared timing values without summing duplicate member totals")
        check(grouped.rows[0].orbitKey == grouped.rows[1].orbitKey, "Synthetic group inputs must share their visual slot")
        let (_, manyGroupedView) = fixture(Array(rows.prefix(7)) + grouped.rows)
        let groupRings = field(manyGroupedView, "rings").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
        let groupedIndices = groupRings.flatMap { field($0, "indices") as? [Int] ?? [] }
        check(groupRings.count == 2 && groupedIndices.sorted() == Array(0..<8),
              "A long group name and seven independent positions must keep two name circles and one group slot")
    }

    func timingFractionsAndCommonBaseline() throws {
        for (idle, run, expected) in [(30.0, 70.0, 0.3), (300.0, 700.0, 0.3),
                                      (0.0, 50.0, 0.0), (50.0, 0.0, 1.0),
                                      (Double.greatestFiniteMagnitude, Double.greatestFiniteMagnitude, 0.5)] {
            let fraction = BubbleView.idleFraction(idle: idle, run: run)
            check(fraction.map { $0.isFinite && abs(Double($0) - expected) < 0.000_001 } == true,
                  "Idle/run share must reflect observed durations without overflow: \(idle)/\(run)")
        }
        let unknown: [(Double?, Double?)] = [(nil, nil), (nil, 10), (10, nil), (0, 0),
                                            (-1, 10), (10, -1), (.nan, 10), (10, .nan),
                                            (.infinity, 10), (10, .infinity)]
        for (idle, run) in unknown {
            check(BubbleView.idleFraction(idle: idle, run: run) == nil,
                  "Missing, invalid and zero-total timing must use a neutral track")
        }
        for (idle, run, expected) in [(nil, 10.0 as Double?, nil as CGFloat?),
                                      (0.0 as Double?, 0.0 as Double?, nil as CGFloat?),
                                      (30.0 as Double?, 70.0 as Double?, 0.3 as CGFloat?)] {
            let (_, view) = fixture([try pane("ratio", idle: idle, run: run)])
            let nodes = field(view, "nodes").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
            let raw = nodes.first.flatMap { field($0, "idleFraction") }
            let actual = raw.flatMap { Mirror(reflecting: $0).children.first?.value as? CGFloat }
            let matches = actual == nil && expected == nil || actual.flatMap { actual in
                expected.map { abs(actual - $0) < 0.000_001 }
            } == true
            check(matches, "The rendered node must use the shared ratio helper, including unknown timing")
        }
        let rows = try (0..<8).map { try pane("geometry-session-\($0)") }
        let (_, view) = fixture(rows)
        let rings = field(view, "rings").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
        let radii = rings.compactMap { field($0, "radius") as? CGFloat }
        let innerOffset = BubbleView.resourceBaselineOffset(inner: true)
        let outerOffset = BubbleView.resourceBaselineOffset(inner: false)
        check(innerOffset.isFinite && innerOffset > 0 && outerOffset.isFinite && outerOffset > 0,
              "All resource roles share one valid baseline per name circle")
        if radii.count == 2 {
            // The renderer's largest inner peak at the maximum allowed height is 6.3 pt.
            // Keep it below the outer name circle's inner status arc (radius - 20).
            check(radii[1] + innerOffset + 6.3 < radii[0] - 20,
                  "The maximum inner resource wave must not touch the outer circle's status arc")
        } else {
            check(false, "Common-baseline geometry requires the two-circle fixture")
        }
    }

    func manualRunMenusAndHitTesting() throws {
        preferences.set(false, forKey: "orbitMiniMode")
        var alpha = try pane("alpha", state: "idle")
        alpha.manualRunning = false
        let shared = group(id: "manual-pair")
        var beta = try pane("beta", group: shared)
        beta.manualRunning = true
        beta.manualOverrideId = "synthetic-manual"
        let gamma = try pane("gamma", group: shared)
        let (model, view) = fixture([alpha, beta, gamma])
        let rings = field(view, "rings").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
        guard let ring = rings.first, let radius = field(ring, "radius") as? CGFloat,
              let indices = field(ring, "indices") as? [Int], !indices.isEmpty else {
            check(false, "Menu fixture must have a populated name ring"); return
        }
        let phase = field(ring, "phase") as? CGFloat ?? 0
        let center = NSPoint(x: view.bounds.midX, y: view.bounds.midY)
        func point(_ slot: Int) -> NSPoint {
            let angle = CGFloat.pi / 2 - CGFloat(slot) * .pi * 2 / CGFloat(indices.count) + phase
            return NSPoint(x: center.x + cos(angle) * radius, y: center.y + sin(angle) * radius)
        }
        func event(_ type: NSEvent.EventType, at location: NSPoint) -> NSEvent {
            NSEvent.mouseEvent(with: type, location: view.convert(location, to: nil), modifierFlags: [],
                               timestamp: 0, windowNumber: 0, context: nil, eventNumber: 1,
                               clickCount: 1, pressure: 0)!
        }
        func manual(_ menu: NSMenu?, _ prefix: String) -> NSMenuItem? {
            menu?.items.first { $0.identifier?.rawValue.hasPrefix(prefix) == true }
        }
        let key0 = view.targetKey(at: point(0))
        let key1 = view.targetKey(at: point(1))
        check(key0 != nil && key1 != nil && key0 != key1, "Each label sector must resolve its own target")
        check(view.targetKey(at: center) == nil, "The center must never impersonate a chat label")
        view.mouseMoved(with: event(.mouseMoved, at: point(0)))
        let menu = view.menu(for: event(.rightMouseDown, at: point(1)))
        let mark = manual(menu, "manual.running.")
        let clear = manual(menu, "manual.clear.")
        let target = mark?.representedObject as? ManualRunTarget
        check(target?.key == key1 && target?.key != key0,
              "Right-click must use its event position even when another chat is still hovered")
        if let mark = mark, let action = mark.action { _ = NSApp.sendAction(action, to: mark.target, from: mark) }
        if let clear = clear, let action = clear.action { _ = NSApp.sendAction(action, to: clear.target, from: clear) }
        check(model.manualRequests.count == 2 && model.manualRequests[0].0 == .running && model.manualRequests[1].0 == .clear,
              "Actual native menu actions must send mark and cancellation independently")
        check(model.manualRequests.allSatisfy { $0.1.key == key1 }, "Both actions must retain the clicked identity")
        let groupSlot = key0 == "group:manual-pair" ? 0 : 1
        let groupMenu = view.menu(for: event(.rightMouseDown, at: point(groupSlot)))
        let groupMark = manual(groupMenu, "manual.running.")
        check((groupMark?.representedObject as? ManualRunTarget)?.members == ["remote:beta", "remote:gamma"],
              "A grouped target must include every member monitor key")
        check(groupMark?.state == .on, "Confirmed manual state must appear as a checked menu action")
        check(groupMenu?.items.first?.title == (shared["name"] as? String), "Group menu must retain the complete configured name")
        let nodes = field(view, "nodes").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
        check(nodes.contains { (field($0, "key") as? String) == "group:manual-pair" && (field($0, "status") as? String) == "手动运行中" },
              "A confirmed running override must have a compact manual status")
        if let menu = view.menu(for: event(.rightMouseDown, at: center)), let toggle = menu.items.last, let action = toggle.action {
            _ = NSApp.sendAction(action, to: toggle.target, from: toggle)
        }
        check(view.targetKey(at: point(0)) == nil, "Mini mode must not invent a hidden label hit")
        let mini = view.menu(for: event(.rightMouseDown, at: NSPoint(x: view.bounds.midX, y: view.bounds.midY)))
        let targets = mini?.items.first { $0.title == "会话与互斥组" }?.submenu
        check(targets?.items.count == 2 && targets?.items.allSatisfy { $0.submenu?.items.count == 2 } == true,
              "Mini mode must expose each chat/group with mark and clear submenus")
        check(targets?.items.contains { $0.title.contains("Synthetic long grouped session") } == true,
              "Mini target menus must retain long group names")
        preferences.set(false, forKey: "orbitMiniMode")
    }

    func partialGroupManualMenus() throws {
        preferences.set(false, forKey: "orbitMiniMode")
        var shared = group(id: "partial-pair")
        shared["members"] = ["remote:visible", "local:excluded"]
        shared["known_members"] = 1
        let visible = try pane("visible", group: shared)
        let (model, view) = fixture([visible])
        let rings = field(view, "rings").map { Array(Mirror(reflecting: $0).children.map(\.value)) } ?? []
        guard let ring = rings.first, let radius = field(ring, "radius") as? CGFloat else {
            check(false, "Partial-group fixture must have a name ring"); return
        }
        let phase = field(ring, "phase") as? CGFloat ?? 0
        let point = NSPoint(x: view.bounds.midX + cos(.pi / 2 + phase) * radius,
                            y: view.bounds.midY + sin(.pi / 2 + phase) * radius)
        func event(at point: NSPoint) -> NSEvent {
            NSEvent.mouseEvent(with: .rightMouseDown, location: view.convert(point, to: nil), modifierFlags: [],
                               timestamp: 0, windowNumber: 0, context: nil, eventNumber: 1,
                               clickCount: 1, pressure: 0)!
        }
        let menu = view.menu(for: event(at: point))
        let actions = menu?.items.filter { $0.identifier?.rawValue.hasPrefix("manual.") == true } ?? []
        check(actions.count == 2, "A partially visible group must expose both native manual actions")
        check(actions.allSatisfy { ($0.representedObject as? ManualRunTarget)?.members == ["local:excluded", "remote:visible"] },
              "Native mark and clear targets must keep configured group members absent from current rows")
        for item in actions {
            if let action = item.action { _ = NSApp.sendAction(action, to: item.target, from: item) }
        }
        check(model.manualRequests.count == 2 && model.manualRequests.allSatisfy {
            $0.1.key == "group:partial-pair" && $0.1.members == ["local:excluded", "remote:visible"]
        }, "Actual menu actions must send the complete mixed-source group to backend validation")
        shared["members"] = []
        model.rows = [try pane("visible", group: shared)]
        view.update(model)
        let fallback = view.menu(for: event(at: point))?.items.first { $0.identifier?.rawValue.hasPrefix("manual.running.") == true }
        check((fallback?.representedObject as? ManualRunTarget)?.members == ["remote:visible"],
              "An absent or empty configured membership must fall back to observed monitor keys")
    }

    func independentSignalProfilesAndClock() throws {
        let (model, view) = fixture([try pane("signal-profile")], now: 1000)
        advance(&model.rows[0], io: 400, network: 900, tools: 2, output: 30)
        view.update(model, now: 1001)
        func sums(_ profile: [ResourceRole: [BubbleView.WaveBucket]]) -> [ResourceRole: Double] {
            profile.mapValues { $0.reduce(0) { $0 + $1.rate } }
        }
        let first = view.resourceSignals(for: model.rows[0].orbitKey, count: 24)
        check(Set(first.keys) == Set([ResourceRole.cpu, .gpu, .disk, .network]),
              "Four separately observed dynamic channels must survive into the actual renderer inputs")
        check(sums(first) == [.cpu: 30, .disk: 400, .gpu: 2, .network: 900],
              "Output activity, I/O bytes, tools and network must never mix counters or units")
        check(first.values.allSatisfy { $0.count == 24 && $0.compactMap(\.newest).max() == 1001 },
              "Every channel uses the same old-to-new timeline while interval measurements fill their observed span")
        advance(&model.rows[0], io: 10)
        view.update(model, now: 1002)
        let second = view.resourceSignals(for: model.rows[0].orbitKey, count: 24)
        check(sums(second) == [.cpu: 30, .disk: 410, .gpu: 2, .network: 900],
              "A new I/O-only event must alter no other channel")
        check(second[.disk]?.last?.newest == 1002 && second[.cpu]?.last?.rate == 0,
              "A quiet channel must not copy the active channel's most recent column")
        view.update(model, now: 1002)
        check(sums(view.resourceSignals(for: model.rows[0].orbitKey, count: 24)) == sums(second),
              "A fixed-time bridge heartbeat must not manufacture any new event")
        view.update(model, now: 1030)
        check(view.resourceSignals(for: model.rows[0].orbitKey, count: 24).values.allSatisfy { $0.allSatisfy { $0.rate == 0 && $0.newest == nil } },
              "Controlled time must expire all 24-second wave histories without sleeping")
        model.rows[0].resources = nil; model.rows[0].usageMetrics = nil
        model.rows[0].outputTotal = nil; model.rows[0].activityEpoch = nil
        view.update(model, now: 1031)
        check(view.resourceSignals(for: model.rows[0].orbitKey).isEmpty,
              "Unknown channels must be absent rather than drawn as invented quiet signals")
        model.rows[0].state = "idle"
        view.update(model, now: 1032)
        check(view.resourceSignals(for: model.rows[0].orbitKey).isEmpty,
              "Waiting sessions have no dynamic resource profile even in a deterministic export")
        check(field(view, "observationTime") as? TimeInterval? == 1032,
              "An explicit observation clock must remain frozen for subsequent drawing")
        view.update(model)
        let clock = field(view, "observationTime")
        check(clock.map { Mirror(reflecting: $0).children.isEmpty } == true,
              "Returning to a normal update must restore the live drawing clock")
    }

    func softChannelScalesAndBuckets() {
        check(BubbleView.dynamicResourceRoles == [.cpu, .gpu, .disk, .network, .output],
              "Dynamic channels include four hardware signals and independent body output, excluding static memory")
        for role in BubbleView.dynamicResourceRoles {
            let reference = BubbleView.resourceReference(role)
            let peak = reference * 100_000
            let low = BubbleView.resourceEnergy(reference * 100, recentPeak: peak, reference: reference) ?? -1
            let medium = BubbleView.resourceEnergy(reference * 10_000, recentPeak: peak, reference: reference) ?? -1
            let high = BubbleView.resourceEnergy(peak, recentPeak: peak, reference: reference) ?? -1
            check(low >= 0 && medium > low + 0.1 && high > medium + 0.1 && high < 1,
                  "\(role): inputs far above the old hard cap must still have visibly different heights on one shared scale")
            let larger = BubbleView.resourceEnergy(peak * 10, recentPeak: peak, reference: reference) ?? -1
            check(larger > high && larger < 1, "\(role): high amplitudes must have a soft shoulder, never a hard ceiling")
            check(BubbleView.resourceEnergy(peak, recentPeak: peak, reference: reference) == high,
                  "\(role): identical real values on an identical scale must retain identical heights")
        }
        check(BubbleView.resourceEnergy(0, recentPeak: 100, reference: 1) == 0,
              "Zero activity must have zero amplitude instead of a decorative floor")
        for rate: Double? in [nil, -1, .nan, .infinity] {
            check(BubbleView.resourceEnergy(rate, recentPeak: 100, reference: 1) == nil,
                  "Unknown or invalid activity must not receive a fabricated height")
        }
        let huge = BubbleView.resourceEnergy(Double.greatestFiniteMagnitude, recentPeak: Double.greatestFiniteMagnitude,
                                             reference: 1, sensitivity: 1.8)
        check(huge.map { $0.isFinite && $0 > 0 && $0 < 1 } == true,
              "The soft mapping must remain finite with the largest finite input and sensitivity")
        let buckets = BubbleView.resourceBuckets([
            .init(time: 978.5, amount: 2), .init(time: 979.5, amount: 7),
            .init(time: 1001, amount: 11), .init(time: 1001.2, amount: 100),
            .init(time: 977, amount: 100), .init(time: 980, amount: 0),
            .init(time: .nan, amount: 100), .init(time: 980, amount: .nan)
        ], now: 1001, count: 24)
        check(buckets.count == 24 && buckets[0].rate == 2 && buckets[1].rate == 7 && buckets[23].rate == 11,
              "Window buckets must preserve genuine magnitude and chronological position")
        check(buckets.reduce(0) { $0 + $1.rate } == 20,
              "Future, expired, zero and invalid observations must not invent activity")
        check(BubbleView.resourceBuckets([], now: 1000, count: 24).allSatisfy { $0.rate == 0 && $0.newest == nil },
              "An empty history must render no columns or peaks")
    }

    func waveGuideMenuNotification() throws {
        let (_, view) = fixture([try pane("guide")])
        let event = NSEvent.mouseEvent(with: .rightMouseDown, location: NSPoint(x: view.bounds.midX, y: view.bounds.midY),
                                        modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil,
                                        eventNumber: 1, clickCount: 1, pressure: 0)!
        let item = view.menu(for: event)?.items.first { $0.identifier?.rawValue == "orbit.waveGuide" }
        check(item?.title == "声浪图说明…", "The native context menu must expose the discoverable wave guide")
        var received = false
        let observer = NotificationCenter.default.addObserver(forName: Notification.Name("AgentOrbit.OpenWaveGuide"), object: nil, queue: nil) { _ in received = true }
        defer { NotificationCenter.default.removeObserver(observer) }
        if let item = item, let action = item.action { _ = NSApp.sendAction(action, to: item.target, from: item) }
        check(received, "The guide action must notify the app without depending on its window implementation")
    }

    func resourceBaselinesAndPendingTools() throws {
        var initial = try pane("resource-baseline")
        initial.resources?.diskReadPerSecond = nil; initial.resources?.diskWritePerSecond = nil
        initial.resources?.networkRxPerSecond = nil; initial.resources?.networkTxPerSecond = nil
        initial.usageMetrics?.activeTools = 2
        let (model, view) = fixture([initial], now: 2000)
        let key = initial.orbitKey
        let baseline = view.resourceSignals(for: key, count: 24)
        check(baseline[.disk] == nil && baseline[.network] == nil,
              "A first counter-only sample has no observable rate and must not be labelled known zero")
        check(view.hasPendingToolMarker(for: key), "Known unfinished tools must expose their static marker independently of completion events")
        check(marks(view, .gpu) == 0 && baseline[.gpu]?.allSatisfy { $0.rate == 0 } == true,
              "A pending-tool marker must never append activity or completed-tool results")
        view.update(model, now: 2001)
        check(view.hasPendingToolMarker(for: key) && marks(view, .gpu) == 0,
              "A heartbeat preserves a static unfinished-tool marker without making it pulse")
        advance(&model.rows[0], io: 12, network: 18)
        model.rows[0].resources?.networkRxPerSecond = nil
        view.update(model, now: 2002)
        let confirmed = view.resourceSignals(for: key, count: 24)
        check(confirmed[.disk]?.reduce(0, { $0 + $1.rate }) == 12 && confirmed[.network]?.reduce(0, { $0 + $1.rate }) == 18,
              "Genuine continuous counter deltas must stay visible even when the collector omits instantaneous rate fields")
        model.rows[0].usageMetrics?.activeTools = 0
        view.update(model, now: 2003)
        check(!view.hasPendingToolMarker(for: key), "Returning tools must immediately clear the static pending marker")
        model.rows[0].usageMetrics?.activeTools = 1
        model.rows[0].usageMetrics?.liveToolsCovered = 0
        view.update(model, now: 2004)
        check(!view.hasPendingToolMarker(for: key), "Unknown live tool coverage cannot fabricate a pending marker")
        model.rows[0].usageMetrics?.liveToolsCovered = 1
        model.rows[0].state = "idle"
        view.update(model, now: 2005)
        check(!view.hasPendingToolMarker(for: key), "A waiting session must remain quiet even with retained tool counts")
    }

    func adaptivePresentationAndMonotonicGeometry() throws {
        func fps(visible: Bool = true, mini: Bool = false, reduced: Bool = false,
                 fixed: Bool = false, peak: Bool = true, cost: Double? = nil) -> Double {
            BubbleView.adaptiveFrameRate(visible: visible, mini: mini, reducedMotion: reduced,
                                         fixedTime: fixed, recentPeak: peak, drawCost: cost)
        }
        check(fps(visible: false) == 0 && fps(fixed: true) == 0, "Hidden views and fixed-time exports must schedule no presentation frames")
        check(fps() == 24 && fps(peak: false) == 3, "Only a recent genuine peak may request 24 FPS; quiet views settle to 3 FPS")
        check(fps(mini: true) == 1.5 && fps(reduced: true) == 2,
              "Mini mode and reduced motion must cap independent drawing to low frame rates")
        check(fps(cost: 0.01) == 6 && fps(cost: 0.03) == 2 && fps(cost: 1) == 1,
              "Expensive frames must lower the presentation budget without queuing model updates")
        check(fps(peak: false, cost: 0.01) == 3 && fps(cost: .nan) == 24,
              "Adaptive cost cannot raise the target frame rate or turn an invalid measurement into a stall")
        let times: [Double] = [0, 0.03, 0.06, 0.09, 0.12, 0.15, 0.18, 0.4]
        let rising = times.compactMap { BubbleView.interpolatedWaveHeight(from: 2, to: 10, elapsed: $0) }
        let falling = times.compactMap { BubbleView.interpolatedWaveHeight(from: 10, to: 2, elapsed: $0) }
        check(rising.count == times.count && zip(rising, rising.dropFirst()).allSatisfy { $0 <= $1 } && rising.last == 10,
              "Real event geometry must rise monotonically and finish within 180 milliseconds")
        check(falling.count == times.count && zip(falling, falling.dropFirst()).allSatisfy { $0 >= $1 } && falling.last == 2,
              "A rescaled real peak may fall smoothly but must never oscillate or overshoot")
        check(times.allSatisfy { BubbleView.interpolatedWaveHeight(from: 10, to: 0, elapsed: $0) == 0 },
              "Zero targets clear at once instead of fading into fictional activity")
        check(BubbleView.interpolatedWaveHeight(from: 10, to: nil, elapsed: 0) == nil &&
              BubbleView.interpolatedWaveHeight(from: 10, to: .nan, elapsed: 0.1) == nil,
              "Unavailable and invalid channels never acquire interpolated peaks")
        let (model, view) = fixture([try pane("presentation")])
        advance(&model.rows[0], io: 1000, tools: 1, output: 100)
        view.update(model)
        let key = model.rows[0].orbitKey
        let before = view.resourceSignals(for: key, count: 24).mapValues { $0.reduce(0) { $0 + $1.rate } }
        let names = field(view, "nodes").map { Mirror(reflecting: $0).children.count }
        for _ in 0..<8 { view.presentationTick() }
        let after = view.resourceSignals(for: key, count: 24).mapValues { $0.reduce(0) { $0 + $1.rate } }
        check(before == after && names == field(view, "nodes").map({ Mirror(reflecting: $0).children.count }),
              "Lightweight ticks cannot record counters, regroup chats or republish model state")
        let bucket = BubbleView.WaveBucket(rate: 1, newest: ProcessInfo.processInfo.systemUptime,
                                           startTime: ProcessInfo.processInfo.systemUptime)
        check(view.presentedHeight(5, node: key, role: .disk, bucket: bucket,
                                   now: ProcessInfo.processInfo.systemUptime, reducedMotion: true) == 5,
              "Reduced motion must bypass geometry interpolation entirely")
        view.update(model, now: 2000)
        check(view.recommendedFrameRate(visible: true) == 0 && view.presentedHeight(5, node: key, role: .disk,
              bucket: bucket, now: 2000, reducedMotion: false) == 5,
              "An explicit export clock must produce deterministic target geometry with no scheduled animation")
        view.resumePresentation()
        advance(&model.rows[0], io: 9999, network: 9999, tools: 99, output: 9999)
        view.update(model, now: 2001)
        check(marks(view, .disk) == 0 && marks(view, .gpu) == 0 && marks(view, .network) == 0 && output(view) == 0,
              "Restoring a hidden view establishes new baselines instead of replaying hidden work as a fresh peak")
        advance(&model.rows[0], io: 7, network: 8, tools: 1, output: 9)
        view.update(model, now: 2002)
        check(marks(view, .disk) == 7 && marks(view, .network) == 8 && marks(view, .gpu) == 1 && output(view) == 9,
              "Visible continuous work must resume normally after the restored baseline")
        model.rows[0].state = "error"
        view.update(model, now: 2002.01)
        check(view.resourceSignals(for: key).isEmpty && output(view) == 0,
              "An error must immediately clear presented activity, regardless of interpolation state")
    }

    func sameBucketEpochGeometryReset() throws {
        let (model, view) = fixture([try pane("geometry-reset")])
        model.settings.reduceMotion = false
        advance(&model.rows[0], io: 1000, output: 1000)
        view.update(model)
        let key = model.rows[0].orbitKey
        let clock = ProcessInfo.processInfo.systemUptime
        let bucket = BubbleView.WaveBucket(rate: 1000, newest: clock, startTime: clock)
        _ = view.presentedHeight(10, node: key, role: .disk, bucket: bucket, now: clock, reducedMotion: false)
        _ = view.presentedHeight(10, node: key, role: .cpu, bucket: bucket, now: clock, reducedMotion: false)
        check(field(view, "waveTransitions").map { Mirror(reflecting: $0).children.count } == 2,
              "The actual geometry reducer must retain independent output and I/O transitions")
        model.rows[0].resources?.epoch = "new-geometry-epoch"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.diskReadBytes = 50_000
        model.rows[0].activityEpoch = 2; model.rows[0].outputTotal = 50_000
        view.update(model)
        check(field(view, "waveTransitions").map { Mirror(reflecting: $0).children.isEmpty } == true,
              "A resource epoch reset immediately discards old hardware geometry and marks")
        advance(&model.rows[0], io: 1, output: 1)
        view.update(model)
        let newHeight = view.presentedHeight(1, node: key, role: .disk, bucket: bucket,
                                             now: clock + 0.05, reducedMotion: false)
        check(newHeight <= 1 && newHeight >= 0,
              "A small new event in the same time bucket cannot replay a larger old-epoch height")
    }

    func processCPUCostFeedback() {
        typealias Reading = BubbleView.PresentationCPUReading
        let previous = Reading(time: 100, cpuSeconds: 20, drawnFrames: 100)
        let current = Reading(time: 101, cpuSeconds: 20.24, drawnFrames: 124)
        let cost = BubbleView.processFrameCost(previous: previous, current: current)
        check(cost.map { abs($0 - 0.01) < 0.000_001 } == true,
              "CPU feedback must include all process CPU time per actually drawn frame, including deferred raster work")
        let fps = BubbleView.adaptiveFrameRate(visible: true, mini: false, reducedMotion: false,
                                               fixedTime: false, recentPeak: true, drawCost: cost)
        check(abs(fps - 6) < 0.000_001,
              "A high process cost hidden from the direct draw timer must reduce the presentation frame budget")
        let quietCPU = BubbleView.processFrameCost(previous: previous,
            current: Reading(time: 101, cpuSeconds: 20, drawnFrames: 124))
        check(quietCPU == 0, "A valid zero CPU increment may be observed without manufacturing cost")
        let invalid: [Reading] = [
            Reading(time: 100.99, cpuSeconds: 20.2, drawnFrames: 124),
            Reading(time: 99, cpuSeconds: 20.2, drawnFrames: 124),
            Reading(time: 111, cpuSeconds: 20.2, drawnFrames: 124),
            Reading(time: 101, cpuSeconds: 19, drawnFrames: 124),
            Reading(time: 101, cpuSeconds: 20.2, drawnFrames: 99),
            Reading(time: 101, cpuSeconds: 20.2, drawnFrames: 100),
            Reading(time: .nan, cpuSeconds: 20.2, drawnFrames: 124),
            Reading(time: 101, cpuSeconds: .infinity, drawnFrames: 124)
        ]
        for reading in invalid {
            check(BubbleView.processFrameCost(previous: previous, current: reading) == nil,
                  "Too-short, stale, reset, undrawn or invalid CPU windows must not alter the adaptive cost")
        }
        check(BubbleView.processFrameCost(previous: Reading(time: -1, cpuSeconds: 20, drawnFrames: 100), current: current) == nil,
              "An invalid CPU baseline must be discarded before the next measurement")
        let reset = Reading(time: 101, cpuSeconds: 1, drawnFrames: 2)
        let rebased = BubbleView.processFrameCost(previous: reset,
            current: Reading(time: 102, cpuSeconds: 1.03, drawnFrames: 5))
        check(rebased.map { abs($0 - 0.01) < 0.000_001 } == true,
              "A fresh baseline must resume normal feedback after process or frame-counter resets")
        let increase = BubbleView.smoothedProcessFrameCost(previous: 0.01, sample: 0.03)
        let recovery = BubbleView.smoothedProcessFrameCost(previous: 0.03, sample: 0.01)
        check(increase.map { abs($0 - 0.021) < 0.000_001 } == true && recovery.map { abs($0 - 0.028) < 0.000_001 } == true,
              "Cost feedback must react faster to increased cost and recover slowly without FPS churn")
        check(BubbleView.smoothedProcessFrameCost(previous: 0.02, sample: .nan) == 0.02 &&
              BubbleView.smoothedProcessFrameCost(previous: 0.02, sample: -1) == 0.02,
              "Invalid cost samples must preserve the previous valid EMA")
        check(BubbleView.smoothedProcessFrameCost(previous: .infinity, sample: 0.01) == 0.01 &&
              BubbleView.smoothedProcessFrameCost(previous: nil, sample: 0.01) == 0.01,
              "The first valid process sample must establish an EMA without inheriting invalid state")
        check(BubbleView.adaptiveFrameRate(visible: false, mini: false, reducedMotion: false, fixedTime: false,
              recentPeak: true, drawCost: cost) == 0 && BubbleView.adaptiveFrameRate(visible: true, mini: true,
              reducedMotion: false, fixedTime: false, recentPeak: true, drawCost: cost) <= 1.5 &&
              BubbleView.adaptiveFrameRate(visible: true, mini: false, reducedMotion: true, fixedTime: false,
              recentPeak: true, drawCost: cost) <= 2,
              "Process feedback cannot override hidden, mini or reduced-motion presentation caps")
    }

    func observedProcessCPUAndScope() throws {
        let legacy = try decode(["identity": "legacy", "io_available": true, "io_complete": true,
                                 "io_scope": "root_process_with_reaped_children",
                                 "io_read_per_second": 1, "io_write_per_second": 2], as: ResourceMetrics.self)
        check(legacy.observedCPUPercent == nil && legacy.summary(connected: true).contains("CPU 未观测"),
              "Older collectors without CPU fields must keep CPU unknown rather than zero")
        check(legacy.summary(connected: true).contains("根进程 I/O") && legacy.summary(connected: true).contains("含已回收子进程"),
              "Legacy root-only I/O must retain its actual accounting scope")
        var observed = try decode(["identity": "new", "sample_age_seconds": 0,
                                   "cpu_seconds": 4.25, "cpu_percent": 250.5,
                                   "cpu_available": true, "cpu_complete": true, "cpu_covered": 3,
                                   "cpu_scope": "process_tree", "attribution": "exclusive",
                                   "attribution_source": "tmux_process_tree",
                                   "io_available": true, "io_complete": true,
                                   "io_scope": "observed_process_tree_threads", "io_kind": "thread_syscall_bytes",
                                   "io_lower_bound": true, "io_accounting": "live_threads_no_reaped_children",
                                   "io_read_per_second": 5, "io_write_per_second": 7], as: ResourceMetrics.self)
        check(observed.cpuSeconds == 4.25 && observed.cpuCovered == 3 && observed.attributionSource == "tmux_process_tree",
              "The native resource DTO must decode process CPU and attribution evidence")
        check(observed.summary(connected: true).contains("250.5%（单核 100%）"),
              "A process tree using multiple cores must show CPU above 100% without clamping")
        check(observed.ioLowerBound == true && observed.summary(connected: true).contains("活跃线程系统调用字节，观测下界") &&
              !observed.summary(connected: true).contains("含已回收子进程"),
              "Thread-tree I/O must show its lower-bound accounting instead of the old root-reaping scope")
        check(observed.summary(connected: false) == "资源未观测", "Disconnected CPU must not remain live")
        observed.cpuComplete = false
        check(observed.summary(connected: true).contains("（单核 100%）（部分）"),
              "A partly covered process tree must expose partial CPU coverage")
        for invalid in [Double.nan, .infinity, -1] {
            observed.cpuPercent = invalid
            check(observed.observedCPUPercent == nil && observed.summary(connected: true).contains("CPU 未观测"),
                  "Invalid process CPU must not become a displayed percentage")
        }
        observed.cpuPercent = 0
        check(observed.observedCPUPercent == 0 && observed.summary(connected: true).contains("CPU 0.0%"),
              "An observed zero CPU rate must remain distinguishable from unknown")
        observed.cpuAvailable = false
        check(observed.observedCPUPercent == nil, "CPU availability must gate a retained numeric field")
        observed.cpuAvailable = true; observed.cpuPercent = nil
        check(observed.observedCPUPercent == nil, "An initial CPU baseline without a rate must remain unknown")
        observed.cpuPercent = 80
        for age in [Double.nan, -1, 4.1] {
            observed.sampleAgeSeconds = age
            check(observed.observedCPUPercent == nil && observed.summary(connected: true) == "资源未观测",
                  "Stale or invalid resource ages must hide CPU observations")
        }

        var first = try pane("cpu-a", group: group())
        first.resources?.cpuAvailable = true; first.resources?.cpuComplete = true; first.resources?.cpuPercent = 125
        var duplicate = try pane("cpu-b", group: group())
        duplicate.resources = first.resources
        let (model, view) = fixture([first, duplicate], now: 3000)
        check(view.accessibilityLabel()?.contains("CPU 125.0%") == true && view.accessibilityLabel()?.contains("CPU 250.0%") != true,
              "One resource identity shared by group members must not double its CPU")
        model.rows[1].source = "local"
        view.update(model, now: 3000.5)
        check(view.accessibilityLabel()?.contains("CPU 250.0%") == true,
              "Matching identity strings from independent local and remote collectors must remain separate")
        model.rows[1].source = "remote"
        model.rows[1].resources?.identity = "different-resource"
        model.rows[1].resources?.cpuPercent = 50
        view.update(model, now: 3001)
        check(view.accessibilityLabel()?.contains("CPU 175.0%") == true,
              "Independent group resources must aggregate their observed process CPU")
        model.rows[1].resources?.sampleAgeSeconds = 5
        view.update(model, now: 3002)
        check(view.accessibilityLabel()?.contains("CPU 125.0%（单核100%）（部分）") == true,
              "A stale member must not contribute CPU, and must mark the group partial")
        model.rows[1].resources?.sampleAgeSeconds = 0
        for index in model.rows.indices { model.rows[index].resources?.attribution = "shared" }
        view.update(model, now: 3003)
        check(view.accessibilityLabel()?.contains("CPU 175.0%（单核100%）（共享进程，未分摊）") == true &&
              model.rows[0].resources?.summary(connected: true).contains("无法拆分到单个会话") == true,
              "Shared CPU must remain explicitly unallocated beside the value and in the row tooltip")
        for index in model.rows.indices { model.rows[index].resources?.attribution = "unattributed" }
        view.update(model, now: 3004)
        check(view.accessibilityLabel()?.contains("CPU 175.0%（单核100%）（归属待确认）") == true &&
              model.rows[0].resources?.summary(connected: true).contains("不能视为此会话的专属用量") == true,
              "Unattributed resources must not imply exact session ownership")
        model.rows[1].resources?.cpuPercent = .infinity
        view.update(model, now: 3005)
        check(view.accessibilityLabel()?.contains("CPU 125.0%（单核100%）（部分）") == true,
              "An invalid group member CPU must not poison or inflate valid observations")

        var waiting = try pane("cpu-waiting", state: "idle")
        waiting.resources?.cpuAvailable = true; waiting.resources?.cpuComplete = true; waiting.resources?.cpuPercent = 400
        let (idleModel, idleView) = fixture([waiting], now: 4000)
        idleModel.rows[0].resources?.cpuPercent = 800
        idleModel.rows[0].resources?.cpuSeconds = 9
        idleModel.rows[0].resources?.sampleId = 2
        idleView.update(idleModel, now: 4001)
        check(idleView.accessibilityLabel()?.contains("0 个运行中，1 个等待输入") == true,
              "Background CPU cannot turn a completed input-ready session back into running")
        check(marks(idleView, .disk) == 0 && marks(idleView, .network) == 0 && marks(idleView, .gpu) == 0 && output(idleView) == 0 &&
              idleView.resourceSignals(for: waiting.orbitKey).isEmpty,
              "CPU-only observations must never fabricate any existing waveform channel")
    }

    func hardwareMetricsAndIndependentSamples() throws {
        let (model, view) = fixture([try pane("hardware")], now: 5000)
        let key = model.rows[0].orbitKey
        model.rows[0].resources?.sampleId = 2
        model.rows[0].resources?.cpuPercent = 250
        model.rows[0].resources?.gpuUtilizationPercent = 80
        model.rows[0].resources?.gpuSampleId = 2
        model.rows[0].resources?.gpuIntervalSeconds = 3
        view.update(model, now: 5003)
        let first = view.resourceSignals(for: key, count: 24)
        check(marks(view, .cpu) == 250 && marks(view, .gpu) == 80,
              "CPU execution intervals and GPU point percentages are measurements, never rate differences")
        check(first[.gpu]?.filter { $0.rate > 0 }.map(\.rate) == [80],
              "GPU point samples do not fill a three-second polling interval, even with a legacy interval field")
        check(first[.cpu]?.map(\.rate).max() == 250 && first[.disk]?.allSatisfy { $0.rate == 0 } == true,
              "CPU and GPU percentages must remain separate from physical disk bytes")
        model.rows[0].resources?.sampleId = 3
        model.rows[0].resources?.cpuPercent = 0
        model.rows[0].resources?.ioReadBytes = 90_000_000
        model.rows[0].outputTotal = 90_000_000
        model.rows[0].usageMetrics?.toolCompletions = 90_000_000
        view.update(model, now: 5004)
        check(marks(view, .cpu) == 250 && marks(view, .gpu) == 80 && marks(view, .disk) == 0,
              "CLI output, tool returns and syscall bytes cannot manufacture hardware wave activity")
        model.rows[0].resources?.sampleId = 4
        model.rows[0].resources?.diskReadBytes = 1124
        model.rows[0].resources?.diskReadPerSecond = 1024
        view.update(model, now: 5005)
        check(marks(view, .disk) == 1024 && marks(view, .gpu) == 80,
              "Actual disk counter growth creates only a disk wave; cached GPU samples are not replayed")
        let resampled = view.resourceSignals(for: key, count: 30)[.gpu] ?? []
        check(resampled.filter { $0.rate > 0 }.count == 1 && resampled.map(\.rate).max() == 80,
              "Changing visual density cannot rescale a measured GPU percentage or invent covered time")
        model.rows[0].resources?.gpuSampleAgeSeconds = 5
        view.update(model, now: 5006)
        check(view.resourceSignals(for: key)[.gpu]?.map(\.rate).max() == 80 &&
              view.accessibilityLabel()?.contains("GPU 未观测") == true,
              "A stale current GPU observation preserves only the original real historical point")
        model.rows[0].resources?.gpuSampleAgeSeconds = 0
        model.rows[0].resources?.gpuAvailable = false
        view.update(model, now: 5007)
        check(view.resourceSignals(for: key)[.gpu]?.map(\.rate).max() == 80 && marks(view, .gpu) == 80,
              "An unavailable GPU retains history without replaying its numeric utilization as new work")
        model.rows[0].state = "idle"
        view.update(model, now: 5008)
        check(view.resourceSignals(for: key).isEmpty && marks(view, .cpu) == 0 && marks(view, .gpu) == 0,
              "Finishing clears all hardware histories immediately")
        model.rows[0].state = "running"
        model.rows[0].resources?.gpuAvailable = true
        model.rows[0].resources?.gpuSampleId = 3
        model.rows[0].resources?.sampleId = 5
        view.update(model, now: 5009)
        model.rows[0].resources?.gpuSampleId = 4
        model.rows[0].resources?.sampleId = 6
        view.update(model, now: 5010)
        check(marks(view, .gpu) == 80, "A fresh GPU point after resuming is valid without inventing a historical interval")
        model.rows[0].resources?.gpuSampleId = 5
        model.rows[0].resources?.sampleId = 7
        view.update(model, now: 5013)
        check(marks(view, .gpu) == 160, "A later independent GPU point resumes normal sampling without a filled interval")
        let membership = group(id: "hardware-pair")
        let (paired, pairedView) = fixture([try pane("gpu-left", group: membership), try pane("gpu-right", group: membership)], now: 6000)
        advance(&paired.rows[0], tools: 20)
        advance(&paired.rows[1], tools: 30)
        pairedView.update(paired, now: 6001)
        check(pairedView.resourceSignals(for: paired.rows[0].orbitKey, count: 24)[.gpu]?.map(\.rate).max() == 50,
              "A same-source GPU query aggregates independent grouped roots without parsing opaque resource identities")
        let scoped: ResourceMetrics = try decode([
            "scope": "session_processes", "cpu_scope": "session_processes", "gpu_scope": "session_processes",
            "associated_process_count": 2, "associated_gpu_process_count": 1,
            "cpu_available": true, "cpu_percent": 150, "cpu_complete": true,
            "gpu_available": true, "gpu_utilization_percent": 120, "gpu_complete": true,
            "memory_available": true, "memory_bytes": 4096, "memory_complete": true,
            "io_available": true, "io_scope": "observed_session_process_threads", "io_complete": true,
            "io_read_per_second": 1, "io_write_per_second": 2
        ], as: ResourceMetrics.self)
        let scopedSummary = scoped.summary(connected: true)
        check(scoped.associatedProcessCount == 2 && scoped.associatedGpuProcessCount == 1 &&
              scopedSummary.contains("关联后台进程 2 个") && scopedSummary.contains("会话关联进程 CPU") &&
              scopedSummary.contains("会话关联进程 GPU SM 120.0%（进程采样合计）") && scopedSummary.contains("会话关联进程 RSS") &&
              scopedSummary.contains("会话关联进程 I/O") && scopedSummary.contains("活跃线程系统调用字节，观测下界") &&
              !scopedSummary.contains("采集范围未标明"),
              "Detached session-associated resource scopes decode and describe their actual coverage")
        var gpu = model.rows[0].resources!
        gpu.gpuUtilizationPercent = 240
        check(gpu.observedGPUPercent == 240 && gpu.summary(connected: true).contains("GPU SM 240.0%"),
              "Real multi-device GPU utilization may exceed one hundred percent")
        gpu.gpuSampleAgeSeconds = 3; gpu.sampleAgeSeconds = 2
        check(gpu.observedGPUPercent == nil, "GPU source age and transport cache age combine before freshness validation")
        gpu.gpuSampleAgeSeconds = 0; gpu.sampleAgeSeconds = 0
        for invalid in [-1.0, Double.nan, Double.infinity] {
            gpu.gpuUtilizationPercent = invalid
            check(gpu.observedGPUPercent == nil, "Invalid GPU rates must not produce a signal")
        }
    }

    func independentDialogueLifecycle() throws {
        var row = try pane("body-only")
        row.resources = nil; row.usageMetrics = nil
        row.dialogueOutput = body("assistant-body-stream")
        let (model, view) = fixture([row], now: 7000)
        let key = row.orbitKey
        var time: TimeInterval = 7000
        func tick(_ gap: Double = 1) { time += gap; view.update(model, now: time) }
        check(Set(view.resourceSignals(for: key).keys) == Set([ResourceRole.output]) && bodyAmount(view, key: key) == 0,
              "Assistant body output is independently available without hardware, tools or usage data")
        nextBody(&model.rows[0], amount: 40); tick()
        check(bodyAmount(view, key: key) == 40, "New assistant characters animate even with no CPU/GPU/disk/TCP measurement")
        model.rows[0].outputTotal = Int.max
        model.rows[0].activityEpoch = 999
        model.rows[0].activityAge = 0
        tick()
        check(bodyAmount(view, key: key) == 40, "Legacy output metadata and cached sample delivery cannot manufacture body characters")
        check(view.resourceSignals(for: key, now: time + 5)[.output]?.contains { $0.rate > 0 } == true,
              "A frozen bridge payload leaves its already observed body history at its original time")
        tick(5)
        check(bodyAmount(view, key: key) == 40 && view.accessibilityLabel()?.contains("对话输出未观测") == true,
              "A cached sample loses current coverage without deleting its real recent history")
        nextBody(&model.rows[0], amount: 100_000); tick()
        check(bodyAmount(view, key: key) == 40, "The first observation after a stale gap establishes a baseline without historical replay")
        nextBody(&model.rows[0], amount: 11); tick()
        check(bodyAmount(view, key: key) == 51, "Body observations resume after their own fresh baseline")
        model.rows[0].resources = try pane("new-hardware").resources
        nextBody(&model.rows[0], amount: 7); tick()
        check(bodyAmount(view, key: key) == 58, "A new hardware identity must not reset the independent body stream")
        model.rows[0].dialogueOutput?.sampleAgeSeconds = 5; tick()
        check(bodyAmount(view, key: key) == 58 && view.accessibilityLabel()?.contains("对话输出未观测") == true,
              "A stale body source keeps historical characters while current coverage is unknown")
        model.rows[0].dialogueOutput?.sampleAgeSeconds = 0
        nextBody(&model.rows[0], amount: 1000); tick()
        check(bodyAmount(view, key: key) == 58, "Recovery from an unavailable body stream starts a new baseline")
        nextBody(&model.rows[0], amount: 13); tick()
        check(bodyAmount(view, key: key) == 71, "Fresh source observations contribute confirmed character differences")
        model.rows[0].state = "idle"; nextBody(&model.rows[0], amount: 1000); tick()
        check(view.resourceSignals(for: key).isEmpty && bodyAmount(view, key: key) == 0,
              "Completion clears body geometry immediately along with hardware activity")
        model.rows[0].state = "running"; nextBody(&model.rows[0], amount: 1000); tick()
        check(bodyAmount(view, key: key) == 0, "Resuming never replays idle body output")
        nextBody(&model.rows[0], amount: 17); tick()
        check(bodyAmount(view, key: key) == 17, "The next continuous run receives a fresh body sample")
        model.connected = false; tick()
        check(view.resourceSignals(for: key).isEmpty, "Disconnect immediately hides body activity")
        model.connected = true; nextBody(&model.rows[0], amount: 100_000); tick()
        check(bodyAmount(view, key: key) == 0, "Reconnect cannot replay cached body history")
        nextBody(&model.rows[0], amount: 19); tick()
        check(bodyAmount(view, key: key) == 19, "Body observations continue normally after reconnect baseline")
        model.rows[0].dialogueOutput = body("new-body-epoch", total: 100_000); tick()
        check(bodyAmount(view, key: key) == 0, "A new body epoch clears previous waveform geometry")
        nextBody(&model.rows[0], amount: 23); tick()
        check(bodyAmount(view, key: key) == 23, "A new epoch contributes only its forward observations")
        model.rows[0].dialogueOutput?.sampleId = 0; tick()
        check(bodyAmount(view, key: key) == 0, "A backward body sample ID resets its baseline")
        nextBody(&model.rows[0], amount: 29); tick()
        check(bodyAmount(view, key: key) == 29, "A restarted sample sequence may resume after baseline")
        model.rows[0].dialogueOutput?.source = "assistant_log"; tick()
        check(bodyAmount(view, key: key) == 0, "A body observation source switch cannot turn a re-scan into output")
        nextBody(&model.rows[0], amount: 31); tick()
        check(bodyAmount(view, key: key) == 31, "Independent assistant log body observations can animate")
        model.rows[0].dialogueOutput?.totalChars = 0; nextBody(&model.rows[0], amount: 0); tick()
        check(bodyAmount(view, key: key) == 0, "Counter rollback clears stale body geometry")
        nextBody(&model.rows[0], amount: 37); tick()
        check(bodyAmount(view, key: key) == 37, "Body observation recovers after counter rollback")
        view.resumePresentation(); nextBody(&model.rows[0], amount: 100_000); tick()
        check(bodyAmount(view, key: key) == 0, "Returning from a hidden window baselines independent body characters")
        nextBody(&model.rows[0], amount: 41); tick()
        check(bodyAmount(view, key: key) == 41, "The visible presentation resumes with confirmed output")
        for _ in 0..<6 { nextBody(&model.rows[0], amount: 0); tick() }
        check(view.resourceSignals(for: key)[.output] != nil && bodyAmount(view, key: key) == 41,
              "Real observation IDs keep a silent body source fresh without manufacturing character activity")
        nextBody(&model.rows[0], amount: 43); tick()
        check(bodyAmount(view, key: key) == 84,
              "New body output after more than four seconds of genuinely observed silence appears immediately")
        model.rows[0].state = "error"; tick()
        check(view.resourceSignals(for: key).isEmpty, "An error state suppresses dialogue activity immediately")
        let decoded: Pane = try decode(["pane_id": "%body", "session": "body", "state": "running", "status": "running", "preview": [],
            "dialogue_output": ["epoch": "decode-body", "sample_id": 4, "total_chars": 123, "available": true,
                                "complete": false, "sample_age_seconds": 0.5, "source": "assistant_log"]], as: Pane.self)
        check(decoded.dialogueOutput?.isFresh == true && decoded.dialogueOutput?.totalChars == 123,
              "The native DTO decodes the backend snake_case dialogue_output contract")
        for age in [-1.0, 5, .nan, .infinity] {
            var invalid = body("invalid"); invalid.sampleAgeSeconds = age
            check(!invalid.isFresh, "Invalid or stale dialogue ages must remain unobserved")
        }
    }

    func lowOverheadDialogueCadence() throws {
        for interval in [3.0, 5.0] {
            var row = try pane("slow-body-\(interval)")
            row.resources = nil; row.usageMetrics = nil
            row.dialogueOutput = body("slow-body-epoch-\(interval)")
            let (model, view) = fixture([row], now: 20_000)
            model.settings.interval = interval
            view.update(model, now: 20_000)
            let key = row.orbitKey
            let window = interval * 3
            var time: TimeInterval = 20_000
            func tick(_ gap: Double) { time += gap; view.update(model, now: time) }
            func close(_ expected: Double) -> Bool { abs(observedBodyAmount(view) - expected) < 0.000_001 }
            nextBody(&model.rows[0], amount: 30); tick(interval + 0.1)
            check(close(30) && abs(bodyAmount(view, key: key) - 30) < 0.000_001,
                  "\(interval)s sampling with scheduler jitter preserves the actual assistant character delta through rendering")
            nextBody(&model.rows[0], amount: 20); tick(interval)
            check(close(50), "\(interval)s continuously sampled output accumulates without changing its epoch or baseline")
            tick(window - 1)
            check(close(50) && view.accessibilityLabel()?.contains("对话输出未观测") == false,
                  "\(interval)s sampling keeps current coverage between configured observations without replaying a cached sample")
            tick(2)
            check(close(50) && view.accessibilityLabel()?.contains("对话输出未观测") == true,
                  "\(interval)s cached output still expires after the bounded continuity window")
            nextBody(&model.rows[0], amount: 100_000); tick(1)
            check(close(50), "\(interval)s cached-gap recovery creates a fresh baseline and never counts backlog")
            nextBody(&model.rows[0], amount: 11); tick(interval)
            check(close(interval == 5 ? 31 : 61),
                  "\(interval)s output resumes after its own gap baseline while older measurements retain their original expiry")

            var delayed = body("delayed-body"); delayed.sampleAgeSeconds = interval + 0.1
            check(delayed.isFresh(maxAge: window), "\(interval)s source ages are validated against the configured observation window")
            for age in [-1.0, window + 1, .nan, .infinity] {
                delayed.sampleAgeSeconds = age
                check(!delayed.isFresh(maxAge: window), "Invalid and expired dialogue ages remain unavailable at \(interval)s sampling")
            }
        }

        var row = try pane("slow-body-real-gap")
        row.resources = nil; row.usageMetrics = nil
        row.dialogueOutput = body("slow-body-real-gap-epoch")
        let (model, view) = fixture([row], now: 30_000)
        model.settings.interval = 5
        view.update(model, now: 30_000)
        let key = row.orbitKey
        var time: TimeInterval = 30_000
        func tick(_ gap: Double) { time += gap; view.update(model, now: time) }
        func close(_ expected: Double) -> Bool { abs(observedBodyAmount(view) - expected) < 0.000_001 }
        nextBody(&model.rows[0], amount: 17); tick(5.1)
        check(close(17) && abs(bodyAmount(view, key: key) - 17) < 0.000_001,
              "A five-second observation interval with real scheduling jitter reaches the waveform")
        nextBody(&model.rows[0], amount: 100_000); tick(16)
        check(close(17), "A real gap beyond three configured intervals never spreads unobserved backlog into the wave")
        nextBody(&model.rows[0], amount: 13); tick(5.1)
        check(close(30), "Forward observations resume after a long real gap without catching up missing output")
        let renderedBefore = bodyAmount(view, key: key)
        model.settings.interval = 0.5; view.update(model, now: time)
        check(close(30) && abs(bodyAmount(view, key: key) - renderedBefore) < 0.000_001,
              "Shortening the sampling interval preserves previously verified long-interval output history")
        model.settings.interval = 5
        model.connected = false; tick(1)
        model.connected = true; nextBody(&model.rows[0], amount: 100_000); tick(5.1)
        check(close(0), "A reconnect in low-overhead mode establishes its baseline without replaying assistant backlog")
        nextBody(&model.rows[0], amount: 19); tick(5.1)
        check(close(19), "Low-overhead dialogue sampling resumes after the reconnect baseline")
        model.rows[0].dialogueOutput = body("new-slow-epoch", total: 100_000); tick(5.1)
        check(close(0), "Low-overhead mode retains epoch-change history protection")
        nextBody(&model.rows[0], amount: 23); tick(5.1)
        check(close(23), "A new low-overhead epoch records only subsequent character growth")
        nextBody(&model.rows[0], amount: 100_000); tick(-1)
        check(close(0), "A backward observation clock cannot manufacture a low-overhead output interval")
        for limit in [-1.0, Double.nan, Double.infinity] {
            check(!body("invalid-window").isFresh(maxAge: limit), "Dialogue freshness rejects invalid custom observation windows")
        }
    }

    func dialogueGroupsAndAliases() throws {
        let membership = group(id: "body-group")
        var left = try pane("body-left", group: membership)
        var alias = try pane("body-alias", group: membership)
        var idle = try pane("body-idle", state: "idle", group: membership)
        left.resources = nil; alias.resources = nil; idle.resources = nil
        left.dialogueOutput = body("shared-body-stream")
        alias.dialogueOutput = left.dialogueOutput
        idle.dialogueOutput = body("idle-body-stream")
        let (model, view) = fixture([left, alias, idle], now: 8000)
        let key = left.orbitKey
        nextBody(&model.rows[0], amount: 20); model.rows[1].dialogueOutput = model.rows[0].dialogueOutput
        nextBody(&model.rows[2], amount: 900)
        view.update(model, now: 8001)
        check(bodyAmount(view, key: key) == 20, "Grouped aliases deduplicate one body epoch, while idle member output contributes nothing")
        model.rows[2].state = "running"; nextBody(&model.rows[2], amount: 1000)
        nextBody(&model.rows[0], amount: 5); model.rows[1].dialogueOutput = model.rows[0].dialogueOutput
        view.update(model, now: 8002)
        check(bodyAmount(view, key: key) == 25, "Joining group members establish their own body baseline without disturbing a running partner")
        nextBody(&model.rows[2], amount: 7)
        nextBody(&model.rows[0], amount: 3); model.rows[1].dialogueOutput = model.rows[0].dialogueOutput
        view.update(model, now: 8003)
        check(bodyAmount(view, key: key) == 35, "Independent active group body streams add once per observation")
        model.rows[0].dialogueOutput?.available = false; model.rows[1].dialogueOutput?.available = false
        nextBody(&model.rows[2], amount: 11); view.update(model, now: 8004)
        check(bodyAmount(view, key: key) == 46, "A missing body measurement preserves both streams' observed history without inventing output")
        model.rows[2].state = "idle"; view.update(model, now: 8005)
        check(bodyAmount(view, key: key) == 28, "An idle member clears its own history while the running member's measurement gap remains historical")
        model.rows[0].state = "unknown"; model.rows[1].state = "unknown"; view.update(model, now: 8006)
        check(view.resourceSignals(for: key).isEmpty && bodyAmount(view, key: key) == 0,
              "An unknown task state still clears all body history immediately")
    }

    func measurementGapsKeepObservedHistory() throws {
        var row = try pane("measurement-gap")
        row.dialogueOutput = body("measurement-gap-body")
        let (model, view) = fixture([row], now: 10_000)
        let key = row.orbitKey
        advance(&model.rows[0], io: 16, network: 32, tools: 40, output: 25)
        nextBody(&model.rows[0], amount: 12)
        view.update(model, now: 10_001)
        let original = view.resourceSignals(for: key, count: 24)
        var resources = model.rows[0].resources!
        var dialogue = model.rows[0].dialogueOutput!
        model.rows[0].resources = nil; model.rows[0].dialogueOutput = nil
        view.update(model, now: 10_002)
        for role in BubbleView.dynamicResourceRoles {
            let marks = view.resourceSignals(for: key, count: 24)[role] ?? []
            check(marks.map(\.rate).reduce(0, +) == (original[role] ?? []).map(\.rate).reduce(0, +) &&
                  marks.compactMap(\.newest).max() == 10_001,
                  "A missing \(role) payload retains real history at its original observation time")
        }
        check(view.accessibilityLabel()?.contains("CPU 未观测") == true &&
              view.accessibilityLabel()?.contains("对话输出未观测") == true,
              "Historical waves never claim that missing current measurements are still covered")
        resources.sampleId! += 1; resources.gpuSampleId! += 1
        resources.cpuPercent = 80; resources.gpuUtilizationPercent = 70
        resources.diskReadBytes! += 100_000; resources.networkRxBytes! += 100_000
        dialogue.sampleId! += 1; dialogue.totalChars! += 100_000
        model.rows[0].resources = resources; model.rows[0].dialogueOutput = dialogue
        view.update(model, now: 10_003)
        check(marks(view, .cpu) == 25 && marks(view, .gpu) == 40 && marks(view, .disk) == 16 &&
              marks(view, .network) == 32 && bodyAmount(view, key: key) == 12,
              "Recovery from a missing frame baselines every channel without catch-up or a fabricated CPU/GPU interval")
        advance(&model.rows[0], io: 3, network: 4, tools: 6, output: 7)
        nextBody(&model.rows[0], amount: 5); view.update(model, now: 10_004)
        check(marks(view, .cpu) == 32 && marks(view, .gpu) == 46 && marks(view, .disk) == 19 &&
              marks(view, .network) == 36 && bodyAmount(view, key: key) == 17,
              "The next continuous CPU interval, GPU point and cumulative-counter deltas resume normally")
        model.rows[0].resources?.sampleAgeSeconds = 5
        model.rows[0].dialogueOutput?.sampleAgeSeconds = 5
        view.update(model, now: 10_005)
        check(marks(view, .cpu) == 32 && marks(view, .gpu) == 46 && marks(view, .disk) == 19 &&
              marks(view, .network) == 36 && bodyAmount(view, key: key) == 17,
              "Stale payloads preserve history independently of current coverage")
        model.rows[0].resources?.sampleAgeSeconds = 0
        model.rows[0].dialogueOutput?.sampleAgeSeconds = 0
        advance(&model.rows[0], io: 10_000, network: 10_000, tools: 80, output: 80)
        nextBody(&model.rows[0], amount: 10_000); view.update(model, now: 10_006)
        check(marks(view, .cpu) == 32 && marks(view, .gpu) == 46 && marks(view, .disk) == 19 &&
              marks(view, .network) == 36 && bodyAmount(view, key: key) == 17,
              "Stale recovery cannot replay the unobserved counter gap")
        resources = model.rows[0].resources!; dialogue = model.rows[0].dialogueOutput!
        model.rows[0].resources = nil; model.rows[0].dialogueOutput = nil
        view.update(model, now: 10_007)
        resources.epoch = "changed-epoch-after-gap"
        dialogue.epoch = "changed-body-after-gap"
        model.rows[0].resources = resources; model.rows[0].dialogueOutput = dialogue
        view.update(model, now: 10_008)
        check(marks(view, .cpu) == 0 && marks(view, .gpu) == 0 && marks(view, .disk) == 0 &&
              marks(view, .network) == 0 && bodyAmount(view, key: key) == 0,
              "Observed epoch changes clear old history even after a missing baseline")
        advance(&model.rows[0], io: 2, network: 3, tools: 4, output: 5)
        nextBody(&model.rows[0], amount: 6); view.update(model, now: 10_009)
        model.disconnectedSources.insert("remote"); view.update(model, now: 10_010)
        check(view.resourceSignals(for: key).isEmpty && marks(view, .disk) == 0 && bodyAmount(view, key: key) == 0,
              "A disconnected source still clears history immediately rather than resembling a metrics-only gap")
        model.disconnectedSources.remove("remote")
        advance(&model.rows[0], io: 10_000, network: 10_000, tools: 80, output: 80)
        nextBody(&model.rows[0], amount: 10_000); view.update(model, now: 10_011)
        check(marks(view, .disk) == 0 && bodyAmount(view, key: key) == 0,
              "Reconnection begins a baseline without replaying disconnected work")
        advance(&model.rows[0], io: 2, network: 3, tools: 4, output: 5)
        nextBody(&model.rows[0], amount: 6); view.update(model, now: 10_012)
        model.rows[0].state = "unknown"; view.update(model, now: 10_013)
        check(view.resourceSignals(for: key).isEmpty && marks(view, .disk) == 0 && bodyAmount(view, key: key) == 0,
              "An unknown task state immediately clears previously observed resource and body work")

        var quiet = try pane("gap-expiry")
        quiet.dialogueOutput = body("gap-expiry-body")
        let (quietModel, quietView) = fixture([quiet], now: 11_000)
        advance(&quietModel.rows[0], io: 9, network: 8, tools: 7, output: 6)
        nextBody(&quietModel.rows[0], amount: 5); quietView.update(quietModel, now: 11_001)
        quietModel.rows[0].resources = nil; quietModel.rows[0].dialogueOutput = nil
        quietView.update(quietModel, now: 11_002)
        check(quietView.resourceSignals(for: quiet.orbitKey, now: 11_024)[.disk]?.contains { $0.rate > 0 } == true,
              "Missing current coverage may retain a real sample until its original twenty-four-second boundary")
        check(quietView.resourceSignals(for: quiet.orbitKey, now: 11_025).isEmpty,
              "Historical samples naturally expire without a constant or invented replacement wave")
    }

    func historyOwnershipDoesNotMove() throws {
        var row = try pane("ownership-gap")
        row.dialogueOutput = body("ownership-body")
        let (model, view) = fixture([row], now: 12_000)
        let oldKey = row.orbitKey
        advance(&model.rows[0], io: 9, network: 8, tools: 7, output: 6)
        nextBody(&model.rows[0], amount: 5); view.update(model, now: 12_001)
        let resources = model.rows[0].resources
        let dialogue = model.rows[0].dialogueOutput
        model.rows[0].resources = nil; model.rows[0].dialogueOutput = nil
        model.rows[0].responseGroup = try decode(group(id: "new-gap-group"), as: SessionResponseGroup.self)
        view.update(model, now: 12_002)
        let newKey = model.rows[0].orbitKey
        check(view.resourceSignals(for: oldKey).isEmpty && view.resourceSignals(for: newKey).isEmpty,
              "Moving a running pane into a new group cannot move its old group's history during a gap")
        model.rows[0].resources = resources; model.rows[0].dialogueOutput = dialogue
        advance(&model.rows[0], io: 10_000, network: 10_000, tools: 80, output: 80)
        nextBody(&model.rows[0], amount: 10_000); view.update(model, now: 12_003)
        check(marks(view, .disk) == 0 && bodyAmount(view, key: newKey) == 0,
              "A changed group starts its own observation baseline")
        advance(&model.rows[0], io: 3, network: 4, tools: 5, output: 6)
        nextBody(&model.rows[0], amount: 7); view.update(model, now: 12_004)
        check(marks(view, .disk) == 3 && bodyAmount(view, key: newKey) == 7,
              "A moved group receives only its later continuous observations")
        model.rows[0].resources = nil; model.rows[0].dialogueOutput = nil
        model.rows[0].source = "local"; view.update(model, now: 12_005)
        check(view.resourceSignals(for: newKey).isEmpty && marks(view, .disk) == 0 && bodyAmount(view, key: newKey) == 0,
              "Matching pane and group names cannot transfer remote history into an unobserved local source")

        let membership = group(id: "gap-aliases")
        var first = try pane("gap-alias-a", group: membership)
        first.dialogueOutput = body("gap-alias-body")
        var alias = try pane("gap-alias-b", group: membership)
        alias.resources = first.resources; alias.dialogueOutput = first.dialogueOutput
        let (aliases, aliasView) = fixture([first, alias], now: 13_000)
        let sharedKey = first.orbitKey
        advance(&aliases.rows[0], io: 9, network: 8, tools: 7, output: 6)
        nextBody(&aliases.rows[0], amount: 5)
        aliases.rows[1].resources = aliases.rows[0].resources
        aliases.rows[1].dialogueOutput = aliases.rows[0].dialogueOutput
        aliasView.update(aliases, now: 13_001)
        aliases.rows[0].resources = nil; aliases.rows[0].dialogueOutput = nil
        advance(&aliases.rows[1], io: 3, network: 4, tools: 5, output: 6)
        nextBody(&aliases.rows[1], amount: 7); aliasView.update(aliases, now: 13_002)
        check(marks(aliasView, .disk) == 12 && marks(aliasView, .network) == 12 &&
              marks(aliasView, .gpu) == 12 && bodyAmount(aliasView, key: sharedKey) == 12,
              "One missing alias cannot interrupt or double-count the same still-observed physical stream")
        aliases.rows[0].resources = aliases.rows[1].resources
        aliases.rows[0].dialogueOutput = aliases.rows[1].dialogueOutput
        aliasView.update(aliases, now: 13_003)
        check(marks(aliasView, .disk) == 12 && marks(aliasView, .network) == 12 &&
              marks(aliasView, .gpu) == 12 && bodyAmount(aliasView, key: sharedKey) == 12,
              "A recovered alias cannot replay its partner's already observed work")
    }

    func realDataStyleRendering() throws {
        var row = try pane("style-body")
        row.dialogueOutput = body("style-body-stream")
        let (model, view) = fixture([row], now: 9000)
        let styles = ["bars", "plasma", "aurora", "radar", "curve", "dots"]
        func render() -> Data {
            let width = max(1, Int(ceil(view.bounds.width))), height = max(1, Int(ceil(view.bounds.height)))
            let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: width * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
            let old = NSGraphicsContext.current
            NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
            defer { NSGraphicsContext.current = old }
            view.draw(view.bounds)
            return Data(bytes: context.data!, count: width * height * 4)
        }
        var quiet: Data?
        for style in styles {
            model.settings.waveStyle = style; view.update(model, now: 9000)
            let image = render()
            if let quiet { check(image == quiet, "\(style): selecting an appearance cannot create waves from zero activity") }
            else { quiet = image }
        }
        for step in 1...3 {
            advance(&model.rows[0], io: [128, 4096, 512][step - 1], network: [40, 1024, 90][step - 1],
                    tools: [15, 80, 30][step - 1], output: [20, 120, 40][step - 1])
            nextBody(&model.rows[0], amount: [8, 60, 15][step - 1])
            view.update(model, now: 9000 + Double(step))
        }
        check(Set(view.resourceSignals(for: row.orbitKey).keys) == Set(ResourceRole.allCases),
              "The real renderer receives all five channels from independent synthetic observations")
        var images: [Data] = []
        let observed = view.resourceSignals(for: row.orbitKey, count: 24).mapValues { $0.map(\.rate) }
        for style in styles {
            model.settings.waveStyle = style; view.update(model, now: 9003)
            let image = render()
            check(!images.contains(image), "\(style): real signal appearance must be visibly distinct in actual Bubble pixels")
            images.append(image)
            check(view.resourceSignals(for: row.orbitKey, count: 24).mapValues { $0.map(\.rate) } == observed,
                  "\(style): appearance cannot alter units, sample values or coverage")
        }
        model.rows[0].state = "idle"
        var waiting: Data?
        for style in styles {
            model.settings.waveStyle = style; view.update(model, now: 9004)
            let image = render()
            if let waiting { check(image == waiting, "\(style): an idle session cannot retain decorative style waves") }
            else { waiting = image }
        }
        ThemePalette.configure(theme: "neon", statusStyle: "theme")
    }

    func sharedWaveGeometryAndChannelSkins() {
        let styles = ["bars", "plasma", "aurora", "radar", "curve", "dots"]
        let normalized: [Double] = [0, 0.125, 0.75, 0.25, 0, 0, 1, 0.375, 0.625, 0, 0.5, 0]
        func paint(_ heights: [CGFloat], style: String, color: NSColor, inner: Bool = false) -> Data {
            renderedPixels {
                BubbleView.drawWaveProfile(heights, center: NSPoint(x: 100, y: 100), radius: 58,
                                           theta: .pi / 2, span: 2, style: style, color: color, inner: inner)
            }
        }
        for inner in [false, true] {
            for style in styles {
                var neutral: Data?
                var masks: [UInt8]?
                for role in ResourceRole.allCases {
                    let reference = BubbleView.resourceReference(role)
                    let heights = normalized.map { value -> CGFloat in
                        guard value > 0 else { return 0 }
                        return (BubbleView.resourceEnergy(reference * value, recentPeak: reference,
                                                          reference: reference) ?? 0) * (inner ? 4.2 : 12)
                    }
                    let actual = paint(heights, style: style, color: .white, inner: inner)
                    if let neutral {
                        check(actual == neutral, "\(style)/\(role)/inner=\(inner): equal normalized measurements must use identical geometry for every channel")
                    } else { neutral = actual }
                    check(actual.contains { $0 != 0 }, "\(style)/\(role): the actual shared renderer must draw a positive single-channel profile")
                    var colored: [Data] = []
                    for skin in SkinDefinition.all {
                        let palette = PaletteResolver(skin: skin, statusStyle: "theme")
                        let image = paint(heights, style: style, color: palette.resourceColor(role), inner: inner)
                        check(!colored.contains(image), "\(style)/\(role)/\(skin.id): a skin must change the actual channel pixels")
                        colored.append(image)
                        let alpha = alphaPixels(image)
                        if let masks {
                            check(alpha == masks, "\(style)/\(role)/\(skin.id): channel color must not select a different geometric shape")
                        } else { masks = alpha }
                    }
                }
                let quiet = paint(Array(repeating: 0, count: normalized.count), style: style, color: .white, inner: inner)
                check(quiet.allSatisfy { $0 == 0 }, "\(style): an observed quiet profile must not draw a decorative baseline or peak")
                check(paint([.nan, -.infinity, -1, 0], style: style, color: .white, inner: inner).allSatisfy { $0 == 0 },
                      "\(style): invalid measurements cannot create a path")
                let left: [CGFloat] = [0, 8, 0, 0, 0, 0, 0]
                let right: [CGFloat] = [0, 0, 0, 0, 0, 5, 0]
                let combined = zip(left, right).map { $0.0 + $0.1 }
                let separated = renderedPixels {
                    for segment in [left, right] {
                        BubbleView.drawWaveProfile(segment, center: NSPoint(x: 100, y: 100), radius: 58,
                            theta: .pi / 2, span: 2, style: style, color: .white, inner: inner)
                    }
                }
                check(paint(combined, style: style, color: .white, inner: inner) == separated,
                      "\(style): zero or missing buckets must break paths rather than connect separate real observations")
            }
        }
        check(paint([2, 8, 3], style: "off", color: .white).allSatisfy { $0 == 0 },
              "Disabled wave appearance cannot draw any geometry")
    }

    func eachChannelUsesAllWaveAppearances() throws {
        let styles = ["bars", "plasma", "aurora", "radar", "curve", "dots"]
        for role in ResourceRole.allCases {
            ThemePalette.configure(theme: "neon", statusStyle: "theme")
            var row = try pane("one-channel-" + role.rawValue)
            row.usageMetrics = nil
            row.resources?.cpuAvailable = role == .cpu
            row.resources?.gpuAvailable = role == .gpu
            row.resources?.ioAvailable = role == .disk
            row.resources?.networkAvailable = role == .network
            if role == .output { row.resources = nil; row.dialogueOutput = body("only-output-wave") }
            let (model, view) = fixture([row], now: 9600)
            var quiet: Data?
            for style in styles {
                model.settings.waveStyle = style; view.update(model, now: 9600)
                let pixels = bubblePixels(view)
                if let quiet { check(pixels == quiet, "\(role)/\(style): a single quiet channel must not gain decoration when switching appearance") }
                else { quiet = pixels }
            }
            for (index, amount) in [12, 80, 26, 60].enumerated() {
                advance(&model.rows[0], io: role == .disk ? amount * 1024 : 0,
                        network: role == .network ? amount * 512 : 0,
                        tools: role == .gpu ? amount : 0, output: role == .cpu ? amount : 0)
                if role == .output { nextBody(&model.rows[0], amount: amount) }
                view.update(model, now: 9601 + Double(index))
            }
            let signals = view.resourceSignals(for: row.orbitKey, count: 24)
            check(Set(signals.keys) == Set([role]) && signals[role]?.contains(where: { $0.rate > 0 }) == true,
                  "\(role): the complete Bubble fixture must have only its one real activity channel")
            let rates = signals.mapValues { $0.map(\.rate) }
            var rendered: [Data] = []
            for style in styles {
                model.settings.waveStyle = style; view.update(model, now: 9604)
                let pixels = bubblePixels(view)
                check(!rendered.contains(pixels), "\(role)/\(style): every single channel must visibly respond to all six appearances")
                rendered.append(pixels)
                check(view.resourceSignals(for: row.orbitKey, count: 24).mapValues { $0.map(\.rate) } == rates,
                      "\(role)/\(style): switching appearance must preserve real sample values and units")
            }
            for state in ["idle", "unknown", "error"] {
                model.rows[0].state = state
                var stopped: Data?
                for style in styles {
                    model.settings.waveStyle = style; view.update(model, now: 9605)
                    let pixels = bubblePixels(view)
                    if let stopped { check(pixels == stopped, "\(role)/\(state)/\(style): stopped channels must clear shape-specific history") }
                    else { stopped = pixels }
                    check(view.resourceSignals(for: row.orbitKey).isEmpty, "\(role)/\(state): no nonrunning profile may animate")
                }
            }
            model.connected = false; model.rows[0].state = "running"; view.update(model, now: 9606)
            check(view.resourceSignals(for: row.orbitKey).isEmpty, "\(role): offline channels cannot retain old geometric waves")
        }
    }

    func actualBubbleTextDrawing() throws {
        let names = ["目标-会话", "e\u{301}cole-agent", "👩🏽‍💻-🛰️", "مرحبا-agent", "עברית-agent", "long-counter-session-1234567890", "control-\u{01}-test"]
        let states = ["running", "idle", "error", "paused", "unknown", "running", "idle"]
        let rows = try names.enumerated().map { try pane($0.element, state: states[$0.offset]) }
        let (model, view) = fixture(rows, now: 1000)
        model.metrics = try decode(["total_tokens": Int.max, "window_tokens": 1234567890,
                                   "usage_covered": rows.count, "usage_sessions": rows.count,
                                   "live_usage_covered": rows.count, "window_seconds": 30], as: UsageMetrics.self)
        advance(&model.rows[0], io: 2048, network: 8192, tools: 2, output: 128)
        view.update(model, now: 1001)

        func draw(_ view: BubbleView) -> [UInt8] {
            let width = max(1, Int(ceil(view.bounds.width))), height = max(1, Int(ceil(view.bounds.height)))
            let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                    bytesPerRow: width * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
            let previous = NSGraphicsContext.current
            NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
            defer { NSGraphicsContext.current = previous }
            view.draw(view.bounds)
            return Array(UnsafeBufferPointer(start: context.data!.assumingMemoryBound(to: UInt8.self), count: width * height * 4))
        }

        var priorPixels: [UInt8]?
        for skin in SkinDefinition.all {
            ThemePalette.configure(theme: skin.id, statusStyle: "theme")
            model.settings.theme = skin.id
            view.update(model, now: 1001)
            let pixels = draw(view)
            check(pixels.contains { $0 != 0 }, "The real Bubble must render Unicode labels and hub counters for skin \(skin.id)")
            if let old = priorPixels { check(old != pixels, "Changing skins must affect the actual Bubble's rendered pixels") }
            priorPixels = pixels
            guard let cache = field(view, "textLayouts") as? OrbitTextLayoutCache else {
                check(false, "The actual Bubble must own the safe font geometry cache"); continue
            }
            let creations = cache.creationCount
            let repeated = draw(view)
            check(repeated == pixels, "Fixed-clock Bubble drawing must remain deterministic for skin \(skin.id)")
            check(cache.creationCount == creations && cache.count <= cache.capacity,
                  "A repeated full Bubble draw must reuse bounded font geometry")
        }

        preferences.set(true, forKey: "orbitMiniMode")
        defer { preferences.set(false, forKey: "orbitMiniMode") }
        let mini = BubbleView(frame: NSRect(x: 0, y: 0, width: 72, height: 72), preferences: preferences)
        mini.update(model, now: 1001)
        check(draw(mini).contains { $0 != 0 }, "The actual mini Bubble must also draw through the safe text engine")
        model.connected = false; model.connectionState = "reconnecting"
        view.update(model, now: 1002)
        check(draw(view).contains { $0 != 0 }, "Disconnected text and placeholder counters must remain drawable")
        ThemePalette.configure(theme: "neon", statusStyle: "theme")
    }
}

@main @MainActor
struct OrbitWaveTests {
    static func main() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let suite = OrbitWaveTestSuite()
        defer { suite.cleanUp() }
        ThemePalette.configure(theme: "neon", statusStyle: "theme")
        try suite.waitingAndResume()
        try suite.groupedMembersAndContinuity()
        try suite.cachedResumeBaseline()
        try suite.independentStateGates()
        try suite.nameRingsAndExactTiming()
        try suite.timingFractionsAndCommonBaseline()
        try suite.manualRunMenusAndHitTesting()
        try suite.partialGroupManualMenus()
        try suite.independentSignalProfilesAndClock()
        suite.softChannelScalesAndBuckets()
        try suite.waveGuideMenuNotification()
        try suite.resourceBaselinesAndPendingTools()
        try suite.adaptivePresentationAndMonotonicGeometry()
        try suite.sameBucketEpochGeometryReset()
        suite.processCPUCostFeedback()
        try suite.observedProcessCPUAndScope()
        try suite.hardwareMetricsAndIndependentSamples()
        try suite.independentDialogueLifecycle()
        try suite.lowOverheadDialogueCadence()
        try suite.dialogueGroupsAndAliases()
        try suite.measurementGapsKeepObservedHistory()
        try suite.historyOwnershipDoesNotMove()
        try suite.realDataStyleRendering()
        suite.sharedWaveGeometryAndChannelSkins()
        try suite.eachChannelUsesAllWaveAppearances()
        try suite.actualBubbleTextDrawing()
        if !suite.failures.isEmpty {
            for failure in suite.failures { fputs("FAIL: " + failure + "\n", stderr) }
            suite.cleanUp()
            exit(1)
        }
        print("Orbit wave tests passed: \(suite.assertions) assertions using synthetic observations and isolated preferences")
    }
}
