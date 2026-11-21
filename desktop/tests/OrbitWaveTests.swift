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
                          "sample_age_seconds": 0, "memory_bytes": 67_108_864,
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

    private func fixture(_ rows: [Pane]) -> (PanelModel, BubbleView) {
        let model = PanelModel()
        model.rows = rows
        model.settings.waveResources = true
        model.settings.waveStyle = "bars"
        model.settings.reduceMotion = true
        let view = BubbleView(frame: NSRect(x: 0, y: 0, width: 176, height: 176), preferences: preferences)
        view.update(model)
        return (model, view)
    }

    private func advance(_ pane: inout Pane, io: Int = 0, network: Int = 0, tools: Int = 0, output: Int = 0) {
        if var resources = pane.resources {
            resources.sampleId = (resources.sampleId ?? 0) + 1
            resources.ioReadBytes = (resources.ioReadBytes ?? 0) + io
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

    func waitingAndResume() throws {
        let (model, view) = fixture([try pane("waiting", state: "idle")])
        advance(&model.rows[0], io: 10, network: 11, tools: 1, output: 12)
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0,
              "A waiting process's I/O, TCP and tool counters must not create work waves")
        check(output(view) == 0, "Waiting terminal output must not become a work wave")
        model.rows[0].state = "running"
        advance(&model.rows[0], io: 1000, network: 1000, tools: 10, output: 1000)
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "Resume establishes fresh baselines without replaying idle counters")
        advance(&model.rows[0], io: 7, network: 11, tools: 1, output: 13)
        view.update(model)
        check(marks(view, .output) == 7 && marks(view, .network) == 11 && marks(view, .tools) == 1 && output(view) == 13,
              "Continuous running samples contribute only their confirmed deltas")
        view.update(model)
        check(marks(view, .output) == 7 && marks(view, .network) == 11 && marks(view, .tools) == 1 && output(view) == 13,
              "Repeated bridge refreshes must not duplicate a real sample")
        model.rows[0].state = "idle"
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "Completion immediately clears waves, rather than freezing recent work")
        model.rows[0].state = "running"
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "An immediate new run must not revive a previous turn's waves")
    }

    func groupedMembersAndContinuity() throws {
        let shared = group()
        let (model, view) = fixture([try pane("alpha", group: shared),
                                     try pane("beta", state: "idle", source: "local", group: shared)])
        advance(&model.rows[1], io: 500, network: 600, tools: 4, output: 700)
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "An idle member's heartbeat must not animate its running group")
        advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
        view.update(model)
        check(marks(view, .output) == 10 && marks(view, .network) == 20 && marks(view, .tools) == 1 && output(view) == 30,
              "A running group member contributes its own work")
        model.rows[1].state = "running"
        advance(&model.rows[1], io: 1000, network: 1000, tools: 10, output: 1000)
        view.update(model)
        advance(&model.rows[1], io: 15, network: 25, tools: 2, output: 35)
        view.update(model)
        check(marks(view, .output) == 25 && marks(view, .network) == 45 && marks(view, .tools) == 3 && output(view) == 65,
              "A resumed member joins after its own baseline without replacing its partner")
        model.rows[0].resources?.identity = "synthetic-reconnected-alpha"
        model.rows[0].resources?.epoch = "synthetic-reconnected-alpha"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.ioReadBytes = 100_000
        model.rows[0].resources?.networkRxBytes = 100_000
        view.update(model)
        check(marks(view, .output) == 15 && marks(view, .network) == 25,
              "A new resource identity clears only its old history and creates no catch-up pulse")
        advance(&model.rows[0], io: 9, network: 13)
        view.update(model)
        check(marks(view, .output) == 24 && marks(view, .network) == 38,
              "A new identity contributes work after its own continuous baseline")
        model.rows[0].resources?.epoch = "synthetic-new-epoch-same-identity"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.ioReadBytes = 200_000
        model.rows[0].resources?.networkRxBytes = 200_000
        view.update(model)
        check(marks(view, .output) == 15 && marks(view, .network) == 25,
              "An epoch reset also clears its member's history when the resource identity string stays the same")
        model.rows[0].usageMetrics?.toolsEpoch = 2
        model.rows[0].usageMetrics?.toolCompletions = 100_000
        view.update(model)
        check(marks(view, .tools) == 2, "A tool continuity reset preserves another group member's history")
        model.disconnectedSources.insert("remote")
        view.update(model)
        check(marks(view, .output) == 15 && marks(view, .network) == 25 && marks(view, .tools) == 2,
              "Disconnecting one source must preserve the live partner's observations")
        model.disconnectedSources.insert("local")
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0,
              "A wholly disconnected group cannot retain live work observations")
    }

    func cachedResumeBaseline() throws {
        let (model, view) = fixture([try pane("cached-resume", state: "idle")])
        advance(&model.rows[0], io: 100, network: 100)
        view.update(model)
        model.rows[0].state = "running"
        // The bridge can redraw the same resource sample many times after a state change.
        for _ in 0..<4 { view.update(model) }
        advance(&model.rows[0], io: 1000, network: 1000)
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0,
              "A cached idle sample cannot arm a delta spanning the first fresh resumed sample")
        advance(&model.rows[0], io: 7, network: 11)
        view.update(model)
        check(marks(view, .output) == 7 && marks(view, .network) == 11,
              "Activity resumes after two proven running resource samples")
    }

    func independentStateGates() throws {
        for state in ["idle", "approval", "paused", "unknown", "error", "interrupted", "missing"] {
            let (model, view) = fixture([try pane("state-gate", state: state)])
            advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
            view.update(model)
            check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
                  "Nonrunning state \(state) must remain quiet even when process counters grow")
        }
        let (model, view) = fixture([try pane("warning")])
        model.rows[0].attentionLevel = "warning"
        advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
        view.update(model)
        check(marks(view, .output) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
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
        if !suite.failures.isEmpty {
            for failure in suite.failures { fputs("FAIL: " + failure + "\n", stderr) }
            suite.cleanUp()
            exit(1)
        }
        print("Orbit wave tests passed: \(suite.assertions) assertions using synthetic observations and isolated preferences")
    }
}
