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
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0,
              "A waiting process's I/O, TCP and tool counters must not create work waves")
        check(output(view) == 0, "Waiting terminal output must not become a work wave")
        model.rows[0].state = "running"
        advance(&model.rows[0], io: 1000, network: 1000, tools: 10, output: 1000)
        view.update(model)
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "Resume establishes fresh baselines without replaying idle counters")
        advance(&model.rows[0], io: 7, network: 11, tools: 1, output: 13)
        view.update(model)
        check(marks(view, .io) == 7 && marks(view, .network) == 11 && marks(view, .tools) == 1 && output(view) == 13,
              "Continuous running samples contribute only their confirmed deltas")
        view.update(model)
        check(marks(view, .io) == 7 && marks(view, .network) == 11 && marks(view, .tools) == 1 && output(view) == 13,
              "Repeated bridge refreshes must not duplicate a real sample")
        model.rows[0].state = "idle"
        view.update(model)
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "Completion immediately clears waves, rather than freezing recent work")
        model.rows[0].state = "running"
        view.update(model)
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "An immediate new run must not revive a previous turn's waves")
    }

    func groupedMembersAndContinuity() throws {
        let shared = group()
        let (model, view) = fixture([try pane("alpha", group: shared),
                                     try pane("beta", state: "idle", source: "local", group: shared)])
        advance(&model.rows[1], io: 500, network: 600, tools: 4, output: 700)
        view.update(model)
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
              "An idle member's heartbeat must not animate its running group")
        advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
        view.update(model)
        check(marks(view, .io) == 10 && marks(view, .network) == 20 && marks(view, .tools) == 1 && output(view) == 30,
              "A running group member contributes its own work")
        model.rows[1].state = "running"
        advance(&model.rows[1], io: 1000, network: 1000, tools: 10, output: 1000)
        view.update(model)
        advance(&model.rows[1], io: 15, network: 25, tools: 2, output: 35)
        view.update(model)
        check(marks(view, .io) == 25 && marks(view, .network) == 45 && marks(view, .tools) == 3 && output(view) == 65,
              "A resumed member joins after its own baseline without replacing its partner")
        model.rows[0].resources?.identity = "synthetic-reconnected-alpha"
        model.rows[0].resources?.epoch = "synthetic-reconnected-alpha"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.ioReadBytes = 100_000
        model.rows[0].resources?.networkRxBytes = 100_000
        view.update(model)
        check(marks(view, .io) == 15 && marks(view, .network) == 25,
              "A new resource identity clears only its old history and creates no catch-up pulse")
        advance(&model.rows[0], io: 9, network: 13)
        view.update(model)
        check(marks(view, .io) == 24 && marks(view, .network) == 38,
              "A new identity contributes work after its own continuous baseline")
        model.rows[0].resources?.epoch = "synthetic-new-epoch-same-identity"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.ioReadBytes = 200_000
        model.rows[0].resources?.networkRxBytes = 200_000
        view.update(model)
        check(marks(view, .io) == 15 && marks(view, .network) == 25,
              "An epoch reset also clears its member's history when the resource identity string stays the same")
        model.rows[0].usageMetrics?.toolsEpoch = 2
        model.rows[0].usageMetrics?.toolCompletions = 100_000
        view.update(model)
        check(marks(view, .tools) == 2, "A tool continuity reset preserves another group member's history")
        model.disconnectedSources.insert("remote")
        view.update(model)
        check(marks(view, .io) == 15 && marks(view, .network) == 25 && marks(view, .tools) == 2,
              "Disconnecting one source must preserve the live partner's observations")
        model.disconnectedSources.insert("local")
        view.update(model)
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0,
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
        check(marks(view, .io) == 0 && marks(view, .network) == 0,
              "A cached idle sample cannot arm a delta spanning the first fresh resumed sample")
        advance(&model.rows[0], io: 7, network: 11)
        view.update(model)
        check(marks(view, .io) == 7 && marks(view, .network) == 11,
              "Activity resumes after two proven running resource samples")
    }

    func independentStateGates() throws {
        for state in ["idle", "approval", "paused", "unknown", "error", "interrupted", "missing"] {
            let (model, view) = fixture([try pane("state-gate", state: state)])
            advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
            view.update(model)
            check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
                  "Nonrunning state \(state) must remain quiet even when process counters grow")
        }
        let (model, view) = fixture([try pane("warning")])
        model.rows[0].attentionLevel = "warning"
        advance(&model.rows[0], io: 10, network: 20, tools: 1, output: 30)
        view.update(model)
        check(marks(view, .io) == 0 && marks(view, .network) == 0 && marks(view, .tools) == 0 && output(view) == 0,
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
        check(Set(first.keys) == Set([ResourceRole.output, .io, .tools, .network]),
              "Four separately observed dynamic channels must survive into the actual renderer inputs")
        check(sums(first) == [.output: 30, .io: 400, .tools: 2, .network: 900],
              "Output activity, I/O bytes, tools and network must never mix counters or units")
        check(first.values.allSatisfy { $0.count == 24 && $0.last?.newest == 1001 },
              "Every channel must use one identical old-to-new timeline")
        advance(&model.rows[0], io: 10)
        view.update(model, now: 1002)
        let second = view.resourceSignals(for: model.rows[0].orbitKey, count: 24)
        check(sums(second) == [.output: 30, .io: 410, .tools: 2, .network: 900],
              "A new I/O-only event must alter no other channel")
        check(second[.io]?.last?.newest == 1002 && second[.output]?.last?.rate == 0,
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
        check(BubbleView.dynamicResourceRoles == [.output, .io, .tools, .network],
              "Memory must remain outside the four event-driven dynamic channels")
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
        initial.resources?.ioReadPerSecond = nil; initial.resources?.ioWritePerSecond = nil
        initial.resources?.networkRxPerSecond = nil; initial.resources?.networkTxPerSecond = nil
        initial.usageMetrics?.activeTools = 2
        let (model, view) = fixture([initial], now: 2000)
        let key = initial.orbitKey
        let baseline = view.resourceSignals(for: key, count: 24)
        check(baseline[.io] == nil && baseline[.network] == nil,
              "A first counter-only sample has no observable rate and must not be labelled known zero")
        check(view.hasPendingToolMarker(for: key), "Known unfinished tools must expose their static marker independently of completion events")
        check(marks(view, .tools) == 0 && baseline[.tools]?.allSatisfy { $0.rate == 0 } == true,
              "A pending-tool marker must never append activity or completed-tool results")
        view.update(model, now: 2001)
        check(view.hasPendingToolMarker(for: key) && marks(view, .tools) == 0,
              "A heartbeat preserves a static unfinished-tool marker without making it pulse")
        advance(&model.rows[0], io: 12, network: 18)
        model.rows[0].resources?.networkRxPerSecond = nil
        view.update(model, now: 2002)
        let confirmed = view.resourceSignals(for: key, count: 24)
        check(confirmed[.io]?.reduce(0, { $0 + $1.rate }) == 12 && confirmed[.network]?.reduce(0, { $0 + $1.rate }) == 18,
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
        check(view.presentedHeight(5, node: key, role: .io, bucket: bucket,
                                   now: ProcessInfo.processInfo.systemUptime, reducedMotion: true) == 5,
              "Reduced motion must bypass geometry interpolation entirely")
        view.update(model, now: 2000)
        check(view.recommendedFrameRate(visible: true) == 0 && view.presentedHeight(5, node: key, role: .io,
              bucket: bucket, now: 2000, reducedMotion: false) == 5,
              "An explicit export clock must produce deterministic target geometry with no scheduled animation")
        view.resumePresentation()
        advance(&model.rows[0], io: 9999, network: 9999, tools: 99, output: 9999)
        view.update(model, now: 2001)
        check(marks(view, .io) == 0 && marks(view, .tools) == 0 && marks(view, .network) == 0 && output(view) == 0,
              "Restoring a hidden view establishes new baselines instead of replaying hidden work as a fresh peak")
        advance(&model.rows[0], io: 7, network: 8, tools: 1, output: 9)
        view.update(model, now: 2002)
        check(marks(view, .io) == 7 && marks(view, .network) == 8 && marks(view, .tools) == 1 && output(view) == 9,
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
        _ = view.presentedHeight(10, node: key, role: .io, bucket: bucket, now: clock, reducedMotion: false)
        _ = view.presentedHeight(10, node: key, role: .output, bucket: bucket, now: clock, reducedMotion: false)
        check(field(view, "waveTransitions").map { Mirror(reflecting: $0).children.count } == 2,
              "The actual geometry reducer must retain independent output and I/O transitions")
        model.rows[0].resources?.epoch = "new-geometry-epoch"
        model.rows[0].resources?.sampleId = 1
        model.rows[0].resources?.ioReadBytes = 50_000
        model.rows[0].activityEpoch = 2; model.rows[0].outputTotal = 50_000
        view.update(model)
        check(field(view, "waveTransitions").map { Mirror(reflecting: $0).children.isEmpty } == true,
              "A resource or output epoch reset must immediately discard its old geometry as well as its marks")
        advance(&model.rows[0], io: 1, output: 1)
        view.update(model)
        let newHeight = view.presentedHeight(1, node: key, role: .io, bucket: bucket,
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
        if !suite.failures.isEmpty {
            for failure in suite.failures { fputs("FAIL: " + failure + "\n", stderr) }
            suite.cleanUp()
            exit(1)
        }
        print("Orbit wave tests passed: \(suite.assertions) assertions using synthetic observations and isolated preferences")
    }
}
