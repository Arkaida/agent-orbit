import AppKit
import Combine

/// Real PanelModel, isolated files, no app window, subprocess or network.
@MainActor
private final class PanelModelTestSuite {
    var assertions = 0
    var failures: [String] = []

    func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        if !condition() { failures.append(message) }
    }

    private func snapshot(_ state: String = "idle", elapsed: Double = 1) throws -> Snapshot {
        let row: [String: Any] = [
            "pane_id": "remote:%synthetic", "session": "alpha", "source": "remote",
            "group_id": "remote:alpha", "state": state, "status": state, "preview": [],
            "available": true, "activity_epoch": 1, "output_total": 12,
            "response": ["kind": state == "idle" ? "waiting" : "running",
                         "episode_id": "synthetic-wait", "since": 1000,
                         "elapsed_seconds": elapsed, "observed": true, "overdue": false]
        ]
        let value: [String: Any] = [
            "rows": [row], "events": [], "error_events": [], "active_error_events": [],
            "followed": [], "host": "example.invalid", "connected": true,
            "connection_state": "live", "message": "Synthetic live frame", "received_frames": 1,
            "connections": [["id": "remote", "label": "远端", "connected": true,
                             "connection_state": "live", "message": "Synthetic live frame"]],
            "local_sessions": [], "metrics": ["total_tokens": 123, "tool_completions": 4]
        ]
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Snapshot.self, from: JSONSerialization.data(withJSONObject: value))
    }

    func run() throws {
        let data = URL(fileURLWithPath: argument("--data-dir", fallback: ""))
        let project = URL(fileURLWithPath: argument("--project", fallback: ""))
        guard data.path.hasPrefix(FileManager.default.temporaryDirectory.path),
              project.path.hasPrefix(FileManager.default.temporaryDirectory.path) else {
            throw NSError(domain: "PanelModelTests", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Tests require isolated temporary paths"])
        }
        try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: project, withIntermediateDirectories: true)
        var settings = AppSettings(); settings.sound = false
        try settings.encoded().write(to: data.appendingPathComponent("settings.json"))
        let model = PanelModel()
        defer { model.stop() }

        let initial = try snapshot()
        model.apply(initial)
        var publications = 0, updates = 0, errors = 0, completions = 0
        let subscription = model.objectWillChange.sink { publications += 1 }
        defer { subscription.cancel() }
        model.onUpdate = { updates += 1 }
        model.onError = { errors += 1 }
        model.onCompletion = { completions += 1 }

        for sequence in 2...12 {
            var heartbeat = initial; heartbeat.receivedFrames = sequence
            model.apply(heartbeat)
        }
        check(publications == 0, "Identical display heartbeats must not republish SwiftUI properties")
        check(updates == 0, "Identical heartbeats must not run the full native layout path")
        check(errors == 0 && completions == 0, "Heartbeats must never create event notifications")

        model.apply(try snapshot(elapsed: 2))
        check(publications == 1 && updates == 1, "A genuine wait-clock change must publish immediately")
        check(model.rows[0].response?.elapsedSeconds == 2, "Lower display cadence must retain elapsed wait time")

        // The error queue can contain a brief incident even when the latest
        // authoritative state has already recovered. Drawing FPS cannot erase it.
        var recovered = try snapshot("running")
        let incident = ErrorEvent(id: "synthetic-error", paneId: "remote:%synthetic", session: "alpha",
                                  summary: "Synthetic brief error", occurredAt: 1001, kind: "fatal", severity: "error")
        recovered.errorEvents = [incident]
        model.apply(recovered)
        check(errors == 1, "A brief fatal event must notify even when its latest row has recovered")
        check(model.unreadErrors.isEmpty, "A resolved incident must not become a history card")
        let publishedAfterError = publications, updatesAfterError = updates
        model.apply(recovered)
        check(errors == 1 && publications == publishedAfterError && updates == updatesAfterError,
              "Repeated delivery queues must not replay errors or republish identical state")

        var completed = try snapshot()
        completed.rows[0].completionId = "synthetic-completion"
        completed.rows[0].completedAt = 1002
        completed.events = [Completion(id: "synthetic-completion", paneId: "remote:%synthetic", session: "alpha", completedAt: 1002)]
        model.apply(completed)
        check(completions == 1 && model.visibleCompletions.count == 1,
              "A real completion must remain immediately visible and notify once")
        let publishedAfterCompletion = publications
        model.apply(completed)
        check(completions == 1 && publications == publishedAfterCompletion,
              "A completion heartbeat must not replay a notification or churn publisher subscriptions")

        var offline = completed
        offline.connected = false; offline.connectionState = "reconnecting"; offline.message = "Synthetic disconnect"
        offline.connections?[0].connected = false
        offline.connections?[0].connectionState = "reconnecting"
        let beforeOffline = updates
        model.apply(offline)
        check(!model.connected && model.connectionState == "reconnecting" && updates > beforeOffline,
              "Connection-state edges must update immediately, independently of event freshness")
        check(model.visibleCompletions.isEmpty, "An old completion must not stay fresh after disconnect")

        var settingsPublications = 0
        let settingsSubscription = model.$settings.dropFirst().sink { _ in settingsPublications += 1 }
        defer { settingsSubscription.cancel() }
        try model.saveSettings(model.settings, follow: "")
        let settingsURL = data.appendingPathComponent("settings.json")
        let followURL = data.appendingPathComponent("follow.json")
        let settingsInode = try FileManager.default.attributesOfItem(atPath: settingsURL.path)[.systemFileNumber] as? NSNumber
        let followInode = try FileManager.default.attributesOfItem(atPath: followURL.path)[.systemFileNumber] as? NSNumber
        try model.saveSettings(model.settings, follow: "")
        check(settingsPublications == 0, "Saving unchanged preferences must not republish configuration")
        let newSettingsInode = try FileManager.default.attributesOfItem(atPath: settingsURL.path)[.systemFileNumber] as? NSNumber
        let newFollowInode = try FileManager.default.attributesOfItem(atPath: followURL.path)[.systemFileNumber] as? NSNumber
        check(newSettingsInode == settingsInode,
              "Saving unchanged preferences must not atomically replace the settings file")
        check(newFollowInode == followInode,
              "Saving unchanged follow entries must not atomically replace the follow file")
    }
}

@main
private struct PanelModelTests {
    @MainActor static func main() {
        let suite = PanelModelTestSuite()
        do { try suite.run() }
        catch { suite.failures.append("Panel model fixture failed: \(error)") }
        if !suite.failures.isEmpty {
            for message in suite.failures { fputs("FAIL: " + message + "\n", stderr) }
            exit(1)
        }
        print("Panel model tests passed: \(suite.assertions) assertions across publisher deduplication, real timing/state edges, brief incidents, completions and unchanged settings")
    }
}
