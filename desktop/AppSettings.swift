import AppKit
import SwiftUI

struct SessionGroup: Codable, Identifiable {
    var id: String
    var name: String
    var members: [String]
}

struct AppSettings: Codable {
    var version = 1
    var remoteEnabled = false
    var host = ""
    var tmuxSocket = ""
    var localEnabled = true
    var localCodexHome = ProcessInfo.processInfo.environment["CODEX_HOME"] ?? NSHomeDirectory() + "/.codex"
    var localLimit = 16
    var localFollowed: [String] = []
    var interval = 0.5
    var theme = "neon"
    var orbitLabelColor = "theme"
    var waveStyle = "theme"
    var wavePalette = "theme"
    var waveHeight = 1.3
    var waveDensity = 1.0
    var waveResources = true
    var waveSensitivity = 1.0
    var pinned = true
    var sound = true
    var completionSound = true
    var errorSound = true
    var waitReminderEnabled = true
    var waitReminderMinutes = 5.0
    var waitReminderSound = true
    var showPreviews = true
    var showBackgroundCounts = true
    var bubbleScale = 0.85
    var opacity = 1.0
    var reduceMotion = false
    var launchCompact = false
    var sessionGroups: [SessionGroup] = []

    enum CodingKeys: String, CodingKey {
        case version, remoteEnabled, host, tmuxSocket, localEnabled, localCodexHome, localLimit, localFollowed,
             interval, theme, orbitLabelColor, waveStyle, wavePalette, waveHeight, waveDensity, waveResources, waveSensitivity,
             pinned, sound, completionSound, errorSound, waitReminderEnabled, waitReminderMinutes, waitReminderSound, showPreviews, showBackgroundCounts,
             bubbleScale, opacity, reduceMotion, launchCompact, sessionGroups
    }
    init() {}
    init(from decoder: Decoder) throws {
        self.init()
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? version
        remoteEnabled = try c.decodeIfPresent(Bool.self, forKey: .remoteEnabled) ?? remoteEnabled
        host = try c.decodeIfPresent(String.self, forKey: .host) ?? host
        tmuxSocket = try c.decodeIfPresent(String.self, forKey: .tmuxSocket) ?? tmuxSocket
        localEnabled = try c.decodeIfPresent(Bool.self, forKey: .localEnabled) ?? localEnabled
        localCodexHome = try c.decodeIfPresent(String.self, forKey: .localCodexHome) ?? localCodexHome
        localLimit = try c.decodeIfPresent(Int.self, forKey: .localLimit) ?? localLimit
        localFollowed = try c.decodeIfPresent([String].self, forKey: .localFollowed) ?? localFollowed
        interval = try c.decodeIfPresent(Double.self, forKey: .interval) ?? interval
        theme = try c.decodeIfPresent(String.self, forKey: .theme) ?? theme
        orbitLabelColor = try c.decodeIfPresent(String.self, forKey: .orbitLabelColor) ?? orbitLabelColor
        waveStyle = try c.decodeIfPresent(String.self, forKey: .waveStyle) ?? waveStyle
        wavePalette = try c.decodeIfPresent(String.self, forKey: .wavePalette) ?? wavePalette
        waveHeight = try c.decodeIfPresent(Double.self, forKey: .waveHeight) ?? waveHeight
        waveDensity = try c.decodeIfPresent(Double.self, forKey: .waveDensity) ?? waveDensity
        waveResources = try c.decodeIfPresent(Bool.self, forKey: .waveResources) ?? waveResources
        waveSensitivity = try c.decodeIfPresent(Double.self, forKey: .waveSensitivity) ?? waveSensitivity
        pinned = try c.decodeIfPresent(Bool.self, forKey: .pinned) ?? pinned
        sound = try c.decodeIfPresent(Bool.self, forKey: .sound) ?? sound
        completionSound = try c.decodeIfPresent(Bool.self, forKey: .completionSound) ?? completionSound
        errorSound = try c.decodeIfPresent(Bool.self, forKey: .errorSound) ?? errorSound
        waitReminderEnabled = try c.decodeIfPresent(Bool.self, forKey: .waitReminderEnabled) ?? waitReminderEnabled
        waitReminderMinutes = try c.decodeIfPresent(Double.self, forKey: .waitReminderMinutes) ?? waitReminderMinutes
        waitReminderSound = try c.decodeIfPresent(Bool.self, forKey: .waitReminderSound) ?? waitReminderSound
        showPreviews = try c.decodeIfPresent(Bool.self, forKey: .showPreviews) ?? showPreviews
        showBackgroundCounts = try c.decodeIfPresent(Bool.self, forKey: .showBackgroundCounts) ?? showBackgroundCounts
        bubbleScale = try c.decodeIfPresent(Double.self, forKey: .bubbleScale) ?? bubbleScale
        opacity = try c.decodeIfPresent(Double.self, forKey: .opacity) ?? opacity
        reduceMotion = try c.decodeIfPresent(Bool.self, forKey: .reduceMotion) ?? reduceMotion
        launchCompact = try c.decodeIfPresent(Bool.self, forKey: .launchCompact) ?? launchCompact
        sessionGroups = try c.decodeIfPresent([SessionGroup].self, forKey: .sessionGroups) ?? sessionGroups
    }
    func validate() throws {
        func invalid(_ text: String) -> NSError { NSError(domain: "Settings", code: 1, userInfo: [NSLocalizedDescriptionKey: text]) }
        guard localEnabled || remoteEnabled else { throw invalid("请至少启用一种监控来源。") }
        guard !remoteEnabled || !host.isEmpty else { throw invalid("请填写 SSH 主机别名或 user@host。") }
        if !host.isEmpty && host.range(of: "^[A-Za-z0-9_\\[][A-Za-z0-9_.@:\\[\\]-]*$", options: .regularExpression) == nil {
            throw invalid("SSH 主机不能包含空格、命令或选项。")
        }
        guard !localCodexHome.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw invalid("请填写 Codex 数据目录。") }
        guard (0.25...5).contains(interval), (1...64).contains(localLimit), (0.7...1.5).contains(bubbleScale), (0.65...1).contains(opacity), ThemePalette.names.contains(theme), version == 1 else { throw invalid("设置值超出了支持范围。") }
        guard ["theme", "status"].contains(orbitLabelColor),
              ["theme", "bars", "curve", "dots", "plasma", "aurora", "radar", "off"].contains(waveStyle),
              ["theme", "ice", "aurora", "ember", "status"].contains(wavePalette) else { throw invalid("请选择有效的状态配色、声浪样式与配色。") }
        guard (0.5...1.5).contains(waveHeight), (0.6...1.6).contains(waveDensity) else { throw invalid("声浪高度需要在 50–150% 之间，密度需要在 60–160% 之间。") }
        guard (0.6...1.8).contains(waveSensitivity) else { throw invalid("声浪灵敏度需要在 60–180% 之间。") }
        guard (1...120).contains(waitReminderMinutes) else { throw invalid("久等提醒需要在 1–120 分钟之间。") }
        guard sessionGroups.count <= 32 else { throw invalid("最多支持 32 个会话组。") }
        var groupIDs = Set<String>(), groupedMembers = Set<String>()
        for group in sessionGroups {
            guard !group.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  group.id.unicodeScalars.count <= 128, groupIDs.insert(group.id).inserted,
                  !group.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  group.name.unicodeScalars.count <= 40, !group.id.contains("\n"), !group.id.contains("\0"),
                  !group.name.contains("\n"), !group.name.contains("\0") else {
                throw invalid("会话组需要唯一标识和 1–40 字的组名。")
            }
            guard (2...64).contains(group.members.count) else { throw invalid("每个会话组至少选择 2 个会话。") }
            for member in group.members {
                let prefix = member.hasPrefix("remote:") ? "remote:" : member.hasPrefix("local:") ? "local:" : ""
                guard !prefix.isEmpty, !member.dropFirst(prefix.count).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      member.unicodeScalars.count <= 512, !member.contains("\n"), !member.contains("\0"),
                      groupedMembers.insert(member).inserted else {
                    throw invalid("会话组成员无效，或同一会话被重复编组。")
                }
            }
        }
        guard !tmuxSocket.contains("\n"), !tmuxSocket.contains("\0"), !localCodexHome.contains("\n"), !localCodexHome.contains("\0") else { throw invalid("目录不能包含换行或空字符。") }
    }
    func encoded() throws -> Data {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(self)
    }
}
