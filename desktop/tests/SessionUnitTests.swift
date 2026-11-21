import Foundation

/// Synthetic payloads exercise the shipped Decodable models and helpers.
/// No collector, real session log, app window or network connection is opened.
private final class SessionUnitTestSuite {
    var assertions = 0
    var failures: [String] = []

    func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        if !condition() { failures.append(message) }
    }

    private func decode<T: Decodable>(_ object: [String: Any], as type: T.Type) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: JSONSerialization.data(withJSONObject: object))
    }

    private func group(_ kind: String = "waiting", id: String = "synthetic-pair") -> [String: Any] {
        ["id": id, "name": "Synthetic pair", "kind": kind, "observed": true, "overdue": false,
         "count": 2, "members": ["remote:alpha", "local:beta"], "idle_seconds": 12, "run_seconds": 48]
    }

    private func usage(_ identity: String? = "synthetic-root-a", total: Int = 1000,
                       window: Int? = 30, output: Int? = 6, live: Int = 1,
                       covered: Int = 1) -> [String: Any] {
        var value: [String: Any] = ["scope": "root_session", "usage_sessions": 1,
                                  "usage_covered": covered, "tools_covered": covered,
                                  "live_usage_covered": live, "live_tools_covered": live,
                                  "total_tokens": total, "input_tokens": total - 200, "output_tokens": 200,
                                  "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                                  "window_seconds": 30, "work_window_seconds": 60]
        if let identity = identity { value["root_identity"] = identity }
        if let window = window { value["window_tokens"] = window }
        if let output = output { value["window_output_tokens"] = output }
        if live > 0 {
            value["consumption_per_second"] = Double(window ?? 0) / 30
            value["upstream_tokens_per_second"] = Double(output ?? 0) / 30
            value["tools_per_minute"] = 2.0
        }
        return value
    }

    private func pane(_ state: String = "idle", id: String = "%synthetic-a", session: String = "alpha",
                      source: String = "remote", available: Bool = true, attention: String? = nil,
                      group: [String: Any]? = nil, metrics: [String: Any]? = nil) throws -> Pane {
        var value: [String: Any] = ["pane_id": id, "session": session, "state": state, "status": state,
                                  "preview": [], "source": source, "available": available,
                                  "group_id": source + ":" + session, "agent": "codex"]
        if let attention = attention { value["attention_level"] = attention }
        if let group = group { value["response_group"] = group }
        if let metrics = metrics { value["usage_metrics"] = metrics }
        return try decode(value, as: Pane.self)
    }

    func decodingAndCollection() throws {
        let shared = group("running")
        let alpha = try pane("running", group: shared, metrics: usage())
        let beta = try pane(id: "%synthetic-b", session: "beta", source: "local", group: shared,
                            metrics: usage("synthetic-root-b"))
        let independent = try pane(id: "%synthetic-c", session: "gamma")
        let units = SessionUnit.collect([alpha, independent, beta])
        check(units.count == 2, "Two configured members and one independent session must occupy two positions")
        check(units.map(\.isGroup) == [true, false], "Collection must retain first-appearance order")
        check(units[0].rows.count == 2, "A cross-source group must retain both member panes")
        check(units[0].rows.map(\.source) == ["remote", "local"], "Grouping must not erase member sources")
        check(units[0].name == "Synthetic pair", "The shared position must use its configured group name")
        check(alpha.orbitKey == beta.orbitKey, "Configured group keys must be shared across sources")
        check(alpha.usageMetrics?.rootIdentity == "synthetic-root-a", "Snake-case root identity must decode")
        check(alpha.usageMetrics?.windowTokens == 30, "Exact window token count must survive decoding")
        check(alpha.responseGroup?.idleSeconds == 12 && alpha.responseGroup?.runSeconds == 48,
              "Observed group timing must decode without summing member durations")
        let summary = units[0].summaryPane(connected: { _ in true })
        check(summary?.state == "running", "A group's representative pane must report its combined running state")
        check(summary?.session == "Synthetic pair" && summary?.responseGroup?.id == (shared["id"] as? String),
              "The representative pane must retain shared identity and name")
        check(summary?.source == nil, "A cross-source representative must not impersonate one source")
        let other = try pane(group: group(id: "synthetic-other"))
        check(SessionUnit.collect([alpha, other]).count == 2, "Different group IDs must remain separate even with identical names")
        let separate = try pane(id: "%synthetic-d", session: "gamma")
        check(SessionUnit.collect([independent, separate]).count == 2,
              "Independent panes must not be collapsed merely because session labels match")
        let snapshot = try decode(["rows": [], "events": [], "followed": [], "connected": false,
                                   "message": "Synthetic disconnected frame", "host": "example.invalid",
                                   "received_frames": 0, "metrics": ["window_tokens": 0, "window_seconds": 30]],
                                  as: Snapshot.self)
        check(snapshot.receivedFrames == 0 && snapshot.metrics?.windowTokens == 0,
              "The real Snapshot decoder must preserve an explicit zero, independently of connection state")
    }

    func groupedStatePrecedence() throws {
        let pairs: [(String, String, String?, Bool, String, SessionVisualState)] = [
            ("running", "idle", nil, true, "running", .running),
            ("running", "unknown", nil, true, "unknown", .running),
            ("running", "approval", nil, true, "running", .attention),
            ("running", "running", "warning", true, "running", .attention),
            ("running", "error", nil, true, "running", .error),
            ("running", "missing", nil, false, "unknown", .error),
            ("approval", "error", nil, true, "attention", .error),
            ("idle", "unknown", nil, true, "waiting", .unknown),
            ("idle", "idle", nil, true, "unknown", .unknown),
            ("idle", "idle", nil, true, "offline", .unknown),
            ("idle", "idle", nil, true, "waiting", .waiting),
            ("idle", "idle", nil, false, "waiting", .unknown),
            ("running", "idle", nil, false, "offline", .running)
        ]
        for (first, second, attention, available, kind, expected) in pairs {
            let rows = [try pane(first, group: group(kind)),
                        try pane(second, id: "%synthetic-b", session: "beta", source: "local",
                                 available: available, attention: attention, group: group(kind))]
            for ordered in [rows, Array(rows.reversed())] {
                let unit = SessionUnit(id: "group:synthetic-pair", rows: ordered)
                check(unit.visualState(connected: { _ in true }) == expected,
                      "Group priority must resolve \(first)/\(second), kind=\(kind), available=\(available) to \(expected)")
            }
        }
        let rows = [try pane(group: group()),
                    try pane("error", id: "%synthetic-b", session: "beta", source: "local", group: group())]
        let unit = SessionUnit(id: "group:synthetic-pair", rows: rows)
        check(unit.visualState(connected: { _ in false }) == .offline, "All disconnected members must resolve offline")
        check(unit.visualState(connected: { $0.source == "remote" }) == .unknown,
              "An offline source's stale error must not override a live member or turn partial idle into waiting")
        let ordinary = SessionUnit(id: "ordinary", rows: [try pane("running"), try pane("unknown", id: "%synthetic-b")])
        check(ordinary.visualState(connected: { _ in true }) == .unknown,
              "Ungrouped multi-pane precedence must retain its conservative unknown priority")
    }

    func rootUsageCombination() throws {
        let first = try pane(metrics: usage(total: 1000, window: 31, output: 7))
        let second = try pane(id: "%synthetic-b", session: "beta",
                              metrics: usage("synthetic-root-b", total: 2000, window: 59, output: 11))
        let combined = UsageMetrics.combining([first, second])
        check(combined?.scope == "response_group", "Combined metrics must declare their group scope")
        check(combined?.usageSessions == 2 && combined?.usageCovered == 2, "Independent root logs must retain two coverage slots")
        check(combined?.totalTokens == 3000 && combined?.outputTokens == 400, "Distinct cumulative root counters must sum")
        check(combined?.windowTokens == 90 && combined?.windowOutputTokens == 18, "Exact window integers must sum independently of rounded rates")
        check(combined?.consumptionPerSecond == 3 && combined?.toolsPerMinute == 4, "Live rates and completed tools must sum by distinct root")
        let mirror = try pane(id: "%synthetic-mirror", session: "mirror", metrics: usage(total: 1000, window: 31, output: 7))
        let deduplicated = UsageMetrics.combining([first, mirror])
        check(deduplicated?.usageSessions == 1 && deduplicated?.totalTokens == 1000 && deduplicated?.windowTokens == 31,
              "The same root shown in several panes must be counted once")
        let newer = try pane(id: "%synthetic-newer", session: "mirror", metrics: usage(total: 1100, window: 131, output: 17))
        for ordered in [[first, newer], [newer, first]] {
            let value = UsageMetrics.combining(ordered)
            check(value?.totalTokens == 1100 && value?.windowTokens == 131,
                  "Duplicate panes must prefer the newer confirmed cumulative snapshot independent of order")
        }
        let local = try pane(id: "%synthetic-local", source: "local", metrics: usage(total: 2000, window: 59, output: 11))
        let namespaced = UsageMetrics.combining([first, local])
        check(namespaced?.usageSessions == 2 && namespaced?.totalTokens == 3000 && namespaced?.windowTokens == 90,
              "The same root identity string on different sources denotes two independent ledgers")
        let missing = try pane(id: "%synthetic-unknown", session: "unknown")
        let partial = UsageMetrics.combining([first, missing])
        check(partial?.usageSessions == 2 && partial?.usageCovered == 1 && partial?.liveUsageCovered == 1,
              "A member without telemetry must retain an uncovered root slot")
        check(partial?.totalTokens == 1000 && partial?.windowTokens == 31,
              "Partial metrics may show confirmed counters while exposing their incomplete coverage")
        check(partial?.usageIsPartial == true && partial?.liveUsageIsPartial == true,
              "Partial cumulative and live coverage must remain visible to presentation helpers")
        check(UsageMetrics.combining([missing]) == nil, "Completely absent telemetry must not manufacture metrics")
        let fallbackA = try pane(metrics: usage(nil))
        let fallbackB = try pane(id: "%synthetic-fallback", metrics: usage(nil))
        check(UsageMetrics.combining([fallbackA, fallbackB])?.usageSessions == 1,
              "Unknown root identities may deduplicate only by their source and original monitor key")
        let unknown = try decode(["usage_sessions": 1, "usage_covered": 0, "live_usage_covered": 0], as: UsageMetrics.self)
        check(unknown.totalTokens == nil && unknown.windowTokens == nil, "Missing counters must decode as unknown rather than zero")
    }

    func liveCoverageAndExactFormatting() throws {
        let offline = try pane(metrics: usage(total: 1000, window: nil, output: nil, live: 0))
        let live = try pane(id: "%synthetic-live", metrics: usage(total: 1000, window: 7, output: 2))
        for ordered in [[offline, live], [live, offline]] {
            let value = UsageMetrics.combining(ordered)
            check(value?.usageSessions == 1 && value?.liveUsageCovered == 1 && value?.windowTokens == 7,
                  "At equal cumulative counters a duplicate live pane must outrank an offline cached pane")
        }
        let toolsOnly: [String: Any] = ["root_identity": "synthetic-tools-only", "scope": "root_session",
                                      "usage_sessions": 1, "usage_covered": 0, "tools_covered": 1,
                                      "live_usage_covered": 0, "live_tools_covered": 0]
        var liveToolsOnly = toolsOnly
        liveToolsOnly["live_tools_covered"] = 1
        liveToolsOnly["tools_per_minute"] = 3.0
        let toolsOffline = try pane("missing", available: false, metrics: toolsOnly)
        let toolsLive = try pane("running", id: "%synthetic-tools-live", metrics: liveToolsOnly)
        for ordered in [[toolsOffline, toolsLive], [toolsLive, toolsOffline]] {
            let value = UsageMetrics.combining(ordered)
            check(value?.usageSessions == 1 && value?.toolsCovered == 1 && value?.liveToolsCovered == 1,
                  "A tools-only live duplicate must preserve one root's live tool coverage in either pane order")
            check(value?.toolsPerMinute == 3,
                  "An offline duplicate must not erase tools-only work speed when token usage remains incomplete")
            check(value?.totalTokens == nil && value?.windowTokens == nil && value?.liveUsageCovered == 0,
                  "Live tool coverage must not fabricate cumulative or live token coverage")
        }
        let cached = UsageMetrics.combining([offline])
        check(cached?.totalTokens == 1000 && cached?.windowTokens == nil && cached?.consumptionPerSecond == nil,
              "Offline caches may retain confirmed totals but cannot imply live window usage")
        check(cached?.liveWindowTokens(connected: false) == "—", "Offline windows must display an unknown mark")
        let amounts: [(Int?, String)] = [(nil, "—"), (-1, "—"), (0, "0"), (1, "1"), (999, "999"),
                                        (1000, "1,000"), (1001, "1,001"), (9999, "9,999"),
                                        (10_000, "10,000"), (100_000, "100,000"), (1_000_001, "1,000,001"),
                                        (1_234_567_890, "1,234,567,890"), (Int.max, "9,223,372,036,854,775,807")]
        for (value, expected) in amounts {
            check(UsageMetrics.exactAmount(value) == expected, "Exact integer formatting failed for \(String(describing: value))")
        }
        let zero = try decode(["window_tokens": 0], as: UsageMetrics.self)
        let absent = try decode([:], as: UsageMetrics.self)
        let seven = try decode(["window_tokens": 7], as: UsageMetrics.self)
        check(zero.liveWindowTokens(connected: true) == "0", "A confirmed live zero must remain 0")
        check(absent.liveWindowTokens(connected: true) == "—", "An unknown live window must remain unknown")
        check(seven.liveWindowTokens(connected: false) == "—", "Disconnecting must mask a previously nonzero live window")
        let maximum = try pane(metrics: usage(total: Int.max, window: Int.max, output: Int.max))
        let extra = try pane(id: "%synthetic-extra", session: "extra",
                             metrics: usage("synthetic-root-extra", total: 1000, window: 1, output: 1))
        let overflow = UsageMetrics.combining([maximum, extra])
        check(overflow?.totalTokens == nil && overflow?.windowTokens == nil && overflow?.windowOutputTokens == nil,
              "An unrepresentable aggregate integer must stay unknown instead of overflowing or wrapping")
    }
}

@main
private struct SessionUnitTests {
    static func main() {
        let suite = SessionUnitTestSuite()
        do {
            try suite.decodingAndCollection()
            try suite.groupedStatePrecedence()
            try suite.rootUsageCombination()
            try suite.liveCoverageAndExactFormatting()
        } catch {
            suite.failures.append("Synthetic model decoding failed: \(error)")
        }
        if !suite.failures.isEmpty {
            for message in suite.failures { fputs("FAIL: " + message + "\n", stderr) }
            fputs("Session unit tests failed: \(suite.failures.count) failures across \(suite.assertions) assertions\n", stderr)
            exit(1)
        }
        print("Session unit tests passed: \(suite.assertions) assertions across native decoding, grouped states, root coverage and exact counts")
    }
}
