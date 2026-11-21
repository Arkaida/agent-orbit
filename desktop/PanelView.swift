import AppKit
import SwiftUI

struct NeonBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [ThemePalette.ui(.surface), ThemePalette.ui(.base), ThemePalette.ui(.base)], startPoint: .topLeading, endPoint: .bottomTrailing)
            Canvas { context, size in
                var grid = Path()
                for x in stride(from: CGFloat(0), through: size.width, by: 26) {
                    grid.move(to: CGPoint(x: x, y: 0)); grid.addLine(to: CGPoint(x: x, y: size.height))
                }
                for y in stride(from: CGFloat(0), through: size.height, by: 26) {
                    grid.move(to: CGPoint(x: 0, y: y)); grid.addLine(to: CGPoint(x: size.width, y: y))
                }
                context.stroke(grid, with: .color(ThemePalette.ui(.accent).opacity(0.035)), lineWidth: 0.5)
            }
            RadialGradient(colors: [ThemePalette.ui(.accent).opacity(0.06), .clear], center: .topLeading, startRadius: 0, endRadius: 360)
        }.allowsHitTesting(false)
    }
}

struct NeonRule: View {
    var body: some View {
        Rectangle().fill(LinearGradient(colors: [ThemePalette.ui(.accent).opacity(0.6), ThemePalette.ui(.secondaryAccent).opacity(0.32), ThemePalette.ui(.accent).opacity(0.05)], startPoint: .leading, endPoint: .trailing)).frame(height: 1)
    }
}

struct NeonIconStyle: ButtonStyle {
    var active = false
    var size: CGFloat = 24
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 12, weight: .medium))
            .foregroundColor(active ? ThemePalette.ui(.accent) : ThemePalette.ui(.muted))
            .frame(width: size, height: size)
            .background(RoundedRectangle(cornerRadius: 7).fill(ThemePalette.ui(.accent).opacity(configuration.isPressed ? 0.2 : active ? 0.09 : 0.035)))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(ThemePalette.ui(.accent).opacity(active ? 0.35 : 0.12), lineWidth: 0.7))
            .contentShape(Rectangle())
    }
}

struct StatusMetric: View {
    let value: String
    let label: String
    let color: Color
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(value).font(.system(size: 21, weight: .medium, design: .monospaced)).monospacedDigit()
                .foregroundColor(color).shadow(color: color.opacity(0.28), radius: 7)
            Text(label).font(.system(size: 9, weight: .medium)).foregroundColor(ThemePalette.ui(.muted))
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 8).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 9).fill(color.opacity(0.045)))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(color.opacity(0.17), lineWidth: 0.7))
    }
}

struct UsageMetricCell: View {
    let label: String
    let value: String
    let unit: String
    var role: ThemeRole = .accent
    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.system(size: 10)).foregroundColor(ThemePalette.ui(.muted))
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(value).font(.system(size: 14, weight: .medium, design: .monospaced)).monospacedDigit()
                    .foregroundColor(ThemePalette.ui(value == "—" ? .muted : role))
                Text(unit).font(.system(size: 9, design: .monospaced)).foregroundColor(ThemePalette.ui(.muted))
            }.lineLimit(1).minimumScaleFactor(0.8)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct UsageOverview: View {
    let metrics: UsageMetrics?
    let connected: Bool
    @State private var showDetails = false
    @AppStorage("sessionUsageOverviewExpanded") private var expanded = false
    var body: some View {
        VStack(spacing: expanded ? 7 : 0) {
            HStack(spacing: 5) {
                Button { expanded.toggle() } label: {
                    HStack(spacing: 5) {
                        Image(systemName: expanded ? "chevron.down" : "chevron.right").font(.system(size: 7, weight: .semibold))
                            .foregroundColor(ThemePalette.ui(.muted))
                        Text("总运行率").font(.system(size: 9, weight: .medium)).foregroundColor(ThemePalette.ui(.text))
                        Text(connected ? metrics?.utilizationText ?? "—" : "—")
                            .font(.system(size: 11, weight: .medium, design: .monospaced)).monospacedDigit()
                            .foregroundColor(ThemePalette.ui(connected ? .running : .offline))
                        Text(connected && metrics != nil ? "\(metrics?.runningSessions ?? 0)/\(metrics?.knownSessions ?? 0) 位置" : "—/— 位置")
                            .font(.system(size: 8)).foregroundColor(ThemePalette.ui(.muted))
                        Spacer(minLength: 0)
                    }.contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel(expanded ? "收起用量总览" : "展开用量总览")
                    .help("运行中的关注会话 ÷ 在线且状态已确认的关注会话。点击展开或收起；不代表 CPU 或 GPU 利用率。")
                Button { showDetails.toggle() } label: { Image(systemName: "info.circle").font(.system(size: 10)) }
                    .buttonStyle(.plain).foregroundColor(ThemePalette.ui(.muted))
                    .accessibilityLabel("查看用量统计说明")
                    .popover(isPresented: $showDetails, arrowEdge: .bottom) { details }
            }
            if expanded {
                HStack(alignment: .firstTextBaseline) {
                    Text("近 \(metrics?.windowSeconds ?? 30) 秒新增" + (metrics?.liveUsageIsPartial == true ? " · 部分" : ""))
                        .font(.system(size: 10)).foregroundColor(ThemePalette.ui(.muted))
                    Spacer(minLength: 4)
                    Text(windowTokenText).font(.system(size: 13, weight: .semibold, design: .monospaced)).monospacedDigit()
                        .foregroundColor(ThemePalette.ui(connected && metrics?.windowTokens != nil ? .accent : .muted))
                }.lineLimit(1).minimumScaleFactor(0.9)
                HStack(spacing: 10) {
                    UsageMetricCell(label: "实时消耗" + (metrics?.liveUsageIsPartial == true ? " · 部分" : ""), value: metrics?.liveRate(metrics?.consumptionPerSecond, connected: connected) ?? "—", unit: "tok/s")
                    UsageMetricCell(label: "总消耗" + (metrics?.usageIsPartial == true ? " · 部分" : ""), value: UsageMetrics.exactAmount(metrics?.totalTokens), unit: "tok", role: connected ? .text : .muted)
                }
                HStack(spacing: 10) {
                    UsageMetricCell(label: "上游速度" + (metrics?.liveUsageIsPartial == true ? " · 部分" : ""), value: metrics?.liveRate(metrics?.upstreamTokensPerSecond, connected: connected) ?? "—", unit: "输出 tok/s", role: .secondaryAccent)
                    UsageMetricCell(label: "工作速度" + (metrics?.liveWorkIsPartial == true ? " · 部分" : ""), value: metrics?.liveRate(metrics?.toolsPerMinute, connected: connected) ?? "—", unit: "工具次/min", role: .waiting)
                }
                HStack(spacing: 4) {
                    Text("主会话日志")
                    Spacer(minLength: 0)
                    Text(metrics == nil ? "统计准备中" : "累计 \(metrics?.usageCoverageText ?? "0/0") · 实时 \(connected ? metrics?.liveUsageCovered ?? 0 : 0)/\(metrics?.usageSessions ?? 0)")
                }.font(.system(size: 8)).foregroundColor(ThemePalette.ui(.muted))
            }
        }.padding(.horizontal, 9).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 9).fill(ThemePalette.ui(.accent).opacity(0.035)))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(ThemePalette.ui(.accent).opacity(0.13), lineWidth: 0.7))
            .accessibilityElement(children: .contain)
    }
    var windowTokenText: String {
        let amount = metrics?.liveWindowTokens(connected: connected) ?? "—"
        return (amount == "—" ? amount : "+" + amount) + " tok"
    }
    var details: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("关注会话 · 用量总览").font(.system(size: 12, weight: .semibold))
            VStack(alignment: .leading, spacing: 4) {
                detailValue("实时消耗", metrics?.liveRate(metrics?.consumptionPerSecond, connected: connected) ?? "—", "tok/s")
                detailValue("近 \(metrics?.windowSeconds ?? 30) 秒新增", metrics?.liveWindowTokens(connected: connected) ?? "—", "tok")
                detailValue("总消耗", UsageMetrics.exactAmount(metrics?.totalTokens), "tok")
                detailValue("上游输出", metrics?.liveRate(metrics?.upstreamTokensPerSecond, connected: connected) ?? "—", "tok/s")
                detailValue("工作速度", metrics?.liveRate(metrics?.toolsPerMinute, connected: connected) ?? "—", "工具次/min")
            }.font(.system(size: 10, design: .monospaced))
            Text("累计为关注主会话日志中已核实的用量，包含开始监控前的记录，不包含独立子智能体日志。切换关注会话会改变统计范围。")
            VStack(alignment: .leading, spacing: 4) {
                detailLine("输入", metrics?.inputTokens)
                detailLine("输出", metrics?.outputTokens)
                detailLine("缓存读取", metrics?.cachedInputTokens)
                detailLine("缓存写入", metrics?.cacheWriteInputTokens)
            }.font(.system(size: 10, design: .monospaced))
            Text("输入已包含缓存读取与缓存写入；缓存是输入的细分，不再重复相加。总消耗＝输入＋输出。Token 不等于费用。")
            Text("新增量是近 \(metrics?.windowSeconds ?? 30) 秒真实采集到的 Token 整数增量；首次连接先建立基准，不把历史消耗计为新增。实时消耗与上游速度：该窗口的消耗与输出增量除以窗口时长。Claude 通常在响应结束后写入用量，因此是日志结算速度。工作速度：近 \(metrics?.workWindowSeconds ?? 60) 秒工具结果次数，不代表任务完成率。")
            Text("覆盖：用量 \(metrics?.usageCoverageText ?? "0/0")，工具 \(metrics?.workCoverageText ?? "0/0")。实时覆盖：用量 \(connected ? metrics?.liveUsageCovered ?? 0 : 0)/\(metrics?.usageSessions ?? 0)，工具 \(connected ? metrics?.liveToolsCovered ?? 0 : 0)/\(metrics?.usageSessions ?? 0)。离线保留累计，速率显示 —；部分在线只统计已覆盖的在线部分。")
            Text("运行率已确认 \(metrics?.knownSessions ?? 0)/\(metrics?.monitoredSessions ?? 0) 个监控位置；一个会话组占一个位置，离线或状态未知的位置不计入分母。")
        }.font(.system(size: 10)).foregroundColor(ThemePalette.ui(.text))
            .fixedSize(horizontal: false, vertical: true).padding(14).frame(width: 290)
            .background(ThemePalette.ui(.surface)).preferredColorScheme(ThemePalette.scheme)
    }
    func detailLine(_ label: String, _ value: Int?) -> some View {
        detailValue(label, UsageMetrics.exactAmount(value), "tok")
    }
    func detailValue(_ label: String, _ value: String, _ unit: String) -> some View {
        HStack { Text(label); Spacer(); Text(value + " " + unit) }
    }
}

struct PaneCard: View {
    let pane: Pane
    let alert: Bool
    let connected: Bool
    let open: () -> Void
    var showPreviews = true
    var showBackgroundCounts = true
    var snooze: (() -> Void)? = nil
    var snoozed = false
    var reminderOpened = false
    var visualState: SessionVisualState { pane.visualState(connected: connected) }
    var color: Color { ThemePalette.ui(visualState.role) }
    var sessionColor: Color { Color(nsColor: ThemePalette.session(visualState)) }
    var isAvailable: Bool { visualState.role != .offline }
    var hasFreshData: Bool { connected && pane.available != false }
    var heading: some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 5, height: 5).shadow(color: color.opacity(0.7), radius: 4)
            Text(pane.session).font(.system(size: 12, weight: .semibold, design: .monospaced)).foregroundColor(sessionColor).lineLimit(1)
            if let agent = pane.agent, !agent.isEmpty {
                Text((agent == "claude" ? "Claude" : "Codex") + (pane.source == "local" ? " · 本机" : "")).font(.system(size: 8, weight: .medium, design: .monospaced))
                    .foregroundColor(agent == "claude" ? ThemePalette.ui(.secondaryAccent) : ThemePalette.ui(.accent))
                    .padding(.horizontal, 5).padding(.vertical, 3)
                    .background(RoundedRectangle(cornerRadius: 4).fill((agent == "claude" ? ThemePalette.ui(.secondaryAccent) : ThemePalette.ui(.accent)).opacity(0.09)))
            }
            Spacer(minLength: 0)
            Text(isAvailable ? pane.status : "上次：" + pane.status).font(.system(size: 9, weight: .medium))
                .foregroundColor(color).lineLimit(1).minimumScaleFactor(0.85)
        }
    }
    var response: SessionResponse? { isAvailable && !pane.isCritical ? pane.response : nil }
    var effectiveResponse: SessionResponse? { hasFreshData && !pane.isCritical ? pane.effectiveResponse : nil }
    var responseColor: Color { ThemePalette.ui(effectiveResponse?.overdue == true ? .attention : response?.kind == "running" ? .running : .waiting) }
    var responseLabel: String {
        guard let individual = response else { return "" }
        let response = pane.responseGroup != nil && effectiveResponse?.isPending == true ? effectiveResponse! : individual
        let age = SessionResponse.duration(response.elapsedSeconds)
        if pane.responseGroup != nil && response.isPending {
            return (response.observed ? "已观察组等待 " : "全组等待 ") + age
        }
        switch response.kind {
        case "running": return "工作中 " + (response.observed ? "≥" : "") + age
        case "waiting": return (response.observed ? "已观察等待 " : "等待输入 ") + age
        case "attention": return (response.observed ? "已观察待处理 " : "待你操作 ") + age
        default: return ""
        }
    }
    var usageHelp: String {
        guard let usage = pane.usageMetrics else { return "本会话的 CLI 用量暂不可用。" }
        return "主日志：累计 " + UsageMetrics.exactAmount(usage.totalTokens) + " tok；输入 " + UsageMetrics.exactAmount(usage.inputTokens) + "、输出 " + UsageMetrics.exactAmount(usage.outputTokens) + "。近 \(usage.windowSeconds ?? 30) 秒新增 " + usage.liveWindowTokens(connected: hasFreshData) + " tok，实时消耗 " + usage.liveRate(usage.consumptionPerSecond, connected: hasFreshData) + " tok/s。用量覆盖 \(usage.usageCoverageText)，实时用量 \(hasFreshData ? usage.liveUsageCovered ?? 0 : 0)/\(usage.usageSessions ?? 0)，实时工具 \(hasFreshData ? usage.liveToolsCovered ?? 0 : 0)/\(usage.usageSessions ?? 0)；部分表示只合计已核实成员。上游输出为近 \(usage.windowSeconds ?? 30) 秒已记录 Token 均速，工具为近 \(usage.workWindowSeconds ?? 60) 秒结果次数；Claude 常在响应结束后结算，不等于持续生成速度。"
    }
    @ViewBuilder var responseLine: some View {
        if let response = response, !responseLabel.isEmpty {
            HStack(spacing: 5) {
                Image(systemName: response.kind == "running" ? "clock" : "hourglass").font(.system(size: 8))
                Text(responseLabel).monospacedDigit().lineLimit(1).minimumScaleFactor(0.85)
                if response.isPending && effectiveResponse?.overdue == true {
                    Text("待你继续").font(.system(size: 8, weight: .semibold)).foregroundColor(ThemePalette.ui(.attention))
                }
                if response.isPending, pane.responseGroup != nil, effectiveResponse?.kind == "running" {
                    Text("组内工作中").font(.system(size: 8)).foregroundColor(ThemePalette.ui(.running))
                }
                Spacer(minLength: 0)
                if response.isPending && effectiveResponse?.isPending == true, let snooze = snooze {
                    Button(action: snooze) {
                        Image(systemName: snoozed ? "bell.slash.fill" : reminderOpened ? "bell.badge" : "clock.badge.checkmark")
                            .font(.system(size: 10)).foregroundColor(ThemePalette.ui(.muted))
                    }.buttonStyle(.plain).accessibilityLabel(snoozed ? "已稍后提醒" : "稍后提醒这个会话")
                        .help(snoozed ? "已暂缓一次提醒，等待时长继续累计。" : "稍后再提醒，不会将会话标为已回答。")
                }
            }.font(.system(size: 10.5)).foregroundColor(responseColor)
                .help(response.observed ? "从本次连续观察到这个状态时开始计时；首次连接无法确认实际开始时间，因此显示观察到的下限。只有 CLI 恢复工作才结束等待。" : "从确认本轮完成并等待输入时开始计时。打开或暂缓提醒不会假定你已经回答。")
        }
    }
    var efficiencyLine: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                let amount = pane.usageMetrics?.liveWindowTokens(connected: hasFreshData) ?? "—"
                Text("\(pane.usageMetrics?.windowSeconds ?? 30)s " + (amount == "—" ? amount : "+" + amount) + " tok" + (pane.usageMetrics?.liveUsageIsPartial == true ? " · 部分" : ""))
                    .font(.system(size: 11.5, weight: .semibold, design: .monospaced)).monospacedDigit()
                    .foregroundColor(ThemePalette.ui(hasFreshData && pane.usageMetrics?.windowTokens != nil ? .accent : .muted))
                Spacer(minLength: 2)
                Button(action: open) { Image(systemName: "arrow.up.right") }
                    .buttonStyle(NeonIconStyle(size: 20)).accessibilityLabel(pane.source == "local" ? "打开 Codex 应用" : "进入远端会话")
                    .help(pane.source == "local" ? "打开 Codex 应用查看本机会话" : "进入这个远端会话").disabled(!isAvailable || pane.state == "missing")
            }.lineLimit(1).minimumScaleFactor(0.9)
            Text("累计 " + UsageMetrics.exactAmount(pane.usageMetrics?.totalTokens) + " tok" + (pane.usageMetrics?.usageIsPartial == true ? " · 部分" : ""))
                .font(.system(size: 10.5, weight: .medium, design: .monospaced)).monospacedDigit()
                .foregroundColor(ThemePalette.ui(hasFreshData ? .text : .muted)).lineLimit(1).minimumScaleFactor(0.9)
        HStack(spacing: 5) {
            Text("↑ " + (pane.usageMetrics?.liveRate(pane.usageMetrics?.upstreamTokensPerSecond, connected: hasFreshData) ?? "—") + " tok/s" + (pane.usageMetrics?.liveUsageIsPartial == true ? "*" : ""))
                .foregroundColor(ThemePalette.ui(hasFreshData ? .accent : .muted))
            Text("·").foregroundColor(ThemePalette.ui(.muted))
            Text("工具 " + (pane.usageMetrics?.liveRate(pane.usageMetrics?.toolsPerMinute, connected: hasFreshData) ?? "—") + "/min" + (pane.usageMetrics?.liveWorkIsPartial == true ? "*" : ""))
                .foregroundColor(ThemePalette.ui(hasFreshData ? .secondaryAccent : .muted))
            Spacer(minLength: 0)
        }.font(.system(size: 10, design: .monospaced)).lineLimit(1).minimumScaleFactor(0.9)
        }.help(usageHelp)
    }
    var observationSummary: String? {
        guard let response = effectiveResponse, response.idleSeconds != nil || response.runSeconds != nil else {
            return pane.responseGroup.map { $0.name + ($0.kind == "running" ? " · 组内工作中" : " · 共享响应") }
        }
        let name = pane.responseGroup.map { $0.name + " · " } ?? ""
        let idle = SessionResponse.duration(response.idleSeconds)
        let run = SessionResponse.duration(response.runSeconds)
        let efficiency: String
        if let value = response.efficiencyPercent, value.isFinite {
            efficiency = String(format: "%.0f%%", max(0, min(100, value)))
        } else { efficiency = "—" }
        return name + "闲置" + idle + "/运行" + run + " · 占比" + efficiency
    }
    var observationHelp: String {
        var parts: [String] = []
        if let group = pane.responseGroup {
            parts.append("共享组「" + group.name + "」：任一成员运行时全组不催促；全部成员明确等待后才开始共同等待计时。")
        }
        parts.append("闲/跑与运行占比仅表示本次监控已观察时间，断线不补计。运行占比＝跑/(闲+跑)，不表示任务质量或模型效率。")
        parts.append("闲/跑比例 " + UsageMetrics.rate(effectiveResponse?.idleRunRatio))
        if pane.responseGroup?.parallelConflict == true { parts.append("有多个组员同时运行，请核对共享上下文是否符合你的安排。") }
        return parts.joined(separator: "\n")
    }
    @ViewBuilder var observationLine: some View {
        if let summary = observationSummary {
            Text(summary).font(.system(size: 10.5)).foregroundColor(ThemePalette.ui(.muted))
                .lineLimit(2).help(observationHelp)
        }
    }
    @ViewBuilder var details: some View {
        if showBackgroundCounts, let counts = pane.backgroundCountsText {
            Text((isAvailable ? "" : "上次：") + counts)
                .font(.system(size: 8.5, design: .monospaced)).foregroundColor(ThemePalette.ui(.muted))
                .lineLimit(1).minimumScaleFactor(0.8)
                .help("子智能体统计正在工作的任务；保留终端可能仍存活或等待输入，数量不会把已结束的主对话判为运行中。数量待核实时不显示为 0。")
        }
        if let error = pane.errorSummary, !error.isEmpty {
            HStack(alignment: .top, spacing: 5) {
                Image(systemName: "exclamationmark.triangle")
                Text(error).lineLimit(3).textSelection(.enabled)
            }.font(.system(size: 11, weight: .medium)).foregroundColor(color)
        }
        if showPreviews, visualState.role == .running, !pane.workingIsBackgroundCount {
            let text = pane.working?.isEmpty == false ? pane.working! : pane.preview.suffix(1).joined()
            if !text.isEmpty { Text(text).font(.system(size: 9)).foregroundColor(ThemePalette.ui(.muted)).lineLimit(1) }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            heading
            responseLine
            efficiencyLine
            observationLine
            details
        }
        .padding(.vertical, 8).padding(.leading, 12).padding(.trailing, 8)
        .background(RoundedRectangle(cornerRadius: 11).fill(LinearGradient(colors: [ThemePalette.ui(.surface).opacity(0.96), ThemePalette.ui(.base).opacity(0.94)], startPoint: .topLeading, endPoint: .bottomTrailing)))
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(color.opacity(alert || [.attention, .error, .unknown].contains(visualState.role) ? 0.55 : 0.2), lineWidth: 0.8))
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 2, height: 25)
                .shadow(color: color.opacity(0.5), radius: 5).padding(.leading, 0.5)
        }
        .shadow(color: alert && visualState.role == .waiting ? color.opacity(0.09) : .clear, radius: 12)
        .contentShape(Rectangle()).onTapGesture(count: 2, perform: open)
    }
}

struct ActiveAlertCard: View {
    let event: ErrorEvent
    var color: Color { ThemePalette.ui(event.visualState.role) }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: event.isCritical ? "exclamationmark.triangle.fill" : "hand.raised.fill")
                Text(event.session).fontWeight(.semibold)
                Spacer()
                Text(event.visualState == .unknown ? "待核实" : "待处理").font(.system(size: 9))
            }
            Text(event.summary).lineLimit(3).textSelection(.enabled)
        }.font(.system(size: 11)).foregroundColor(color).padding(11)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 9).fill(color.opacity(0.055)))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(color.opacity(0.3), lineWidth: 0.8))
    }
}

// Accessory panels have no Edit menu to route standard keyboard shortcuts.
// Handle them in this editor only, without a global event monitor.
final class FollowTextView: NSTextView {
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard window?.firstResponder === self,
              modifiers.contains(.command),
              !modifiers.contains(.control), !modifiers.contains(.option),
              let key = event.charactersIgnoringModifiers?.lowercased() else {
            return super.performKeyEquivalent(with: event)
        }
        if key == "z" {
            if modifiers.contains(.shift) { undoManager?.redo() }
            else { undoManager?.undo() }
            return true
        }
        guard !modifiers.contains(.shift) else { return super.performKeyEquivalent(with: event) }
        switch key {
        case "a": selectAll(nil)
        case "c": copy(nil)
        case "x": cut(nil)
        case "v": pasteAsPlainText(nil)
        default: return super.performKeyEquivalent(with: event)
        }
        return true
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let editorWindow = window else { return }
        DispatchQueue.main.async { [weak self, weak editorWindow] in
            guard let self = self, let editorWindow = editorWindow,
                  self.window === editorWindow else { return }
            editorWindow.makeFirstResponder(self)
        }
    }
}

struct FollowTextEditor: NSViewRepresentable {
    @Binding var text: String

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: FollowTextEditor
        init(_ parent: FollowTextEditor) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard let editor = notification.object as? NSTextView else { return }
            parent.text = editor.string
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        scroll.drawsBackground = false
        scroll.borderType = .noBorder

        let editor = FollowTextView(frame: NSRect(origin: .zero, size: scroll.contentSize))
        editor.isRichText = false
        editor.importsGraphics = false
        editor.isEditable = true
        editor.isSelectable = true
        editor.allowsUndo = true
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.isAutomaticTextReplacementEnabled = false
        editor.isAutomaticSpellingCorrectionEnabled = false
        editor.isContinuousSpellCheckingEnabled = false
        editor.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        editor.textColor = ThemePalette.ns(.text)
        editor.insertionPointColor = ThemePalette.ns(.accent)
        editor.drawsBackground = false
        editor.textContainerInset = NSSize(width: 7, height: 7)
        editor.minSize = .zero
        editor.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = false
        editor.autoresizingMask = [.width]
        editor.textContainer?.containerSize = NSSize(width: scroll.contentSize.width, height: CGFloat.greatestFiniteMagnitude)
        editor.textContainer?.widthTracksTextView = true
        editor.setAccessibilityLabel("关注会话名称")
        editor.string = text
        editor.delegate = context.coordinator
        scroll.documentView = editor
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let editor = scroll.documentView as? NSTextView else { return }
        editor.textColor = ThemePalette.ns(.text); editor.insertionPointColor = ThemePalette.ns(.accent)
        guard editor.string != text else { return }
        // Frequent monitoring updates must not reset the caret or undo history.
        editor.string = text
    }
}

struct SessionUnitCard: View {
    let unit: SessionUnit
    @ObservedObject var model: PanelModel
    var showDetails: Bool
    @State private var expanded = false
    var body: some View {
        VStack(spacing: 5) {
            if let pane = unit.summaryPane(connected: model.isConnected) {
                let connected = unit.rows.contains { model.isConnected($0) }
                PaneCard(pane: pane, alert: unit.rows.contains { row in model.unread.contains { $0.paneId == row.paneId } }, connected: connected,
                         open: { if unit.isGroup { expanded.toggle() } else if let member = unit.rows.first { model.open(member) } },
                         showPreviews: showDetails && model.settings.showPreviews && !unit.isGroup,
                         showBackgroundCounts: showDetails && model.settings.showBackgroundCounts && !unit.isGroup,
                         snooze: model.settings.waitReminderEnabled ? { model.snooze(pane) } : nil,
                         snoozed: model.reminderIsSnoozed(pane), reminderOpened: model.reminderWasOpened(pane))
                if unit.isGroup {
                    Button { expanded.toggle() } label: {
                        HStack(spacing: 4) {
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                            Text(expanded ? "收起成员" : "展开 \(unit.rows.count) 个成员")
                            Spacer(minLength: 0)
                            Text("共享闲置 / 运行")
                        }.font(.system(size: 9.5)).foregroundColor(ThemePalette.ui(.muted)).contentShape(Rectangle())
                    }.buttonStyle(.plain).padding(.horizontal, 10).padding(.vertical, 3)
                        .accessibilityLabel(expanded ? "收起会话组成员" : "展开会话组成员")
                }
            }
            if unit.isGroup && expanded {
                ForEach(unit.rows) { pane in
                    PaneCard(pane: pane, alert: model.unread.contains { $0.paneId == pane.paneId }, connected: model.isConnected(pane),
                             open: { model.open(pane) }, showPreviews: showDetails && model.settings.showPreviews,
                             showBackgroundCounts: showDetails && model.settings.showBackgroundCounts,
                             snooze: model.settings.waitReminderEnabled ? { model.snooze(pane) } : nil,
                             snoozed: model.reminderIsSnoozed(pane), reminderOpened: model.reminderWasOpened(pane))
                }.padding(.leading, 8)
            }
        }
    }
}

struct PanelView: View {
    @ObservedObject var model: PanelModel
    var units: [SessionUnit] { SessionUnit.collect(model.displayRows) }
    var visualStates: [SessionVisualState] {
        units.map { $0.visualState(connected: model.isConnected) }
    }
    var hasLiveSessionData: Bool {
        model.connected || visualStates.contains { $0.role != .offline }
    }
    var connectionColor: Color {
        ThemePalette.ui(ThemePalette.connection(connected: model.connected, state: model.connectionState).role)
    }
    var noticeColor: Color {
        ThemePalette.ui(model.unreadErrors.isEmpty ? .waiting : SessionVisualState.mostImportant(model.unreadErrors.map(\.visualState)).role)
    }
    var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "terminal").font(.system(size: 14, weight: .medium)).foregroundColor(ThemePalette.ui(.accent))
                .frame(width: 25, height: 25)
                .background(RoundedRectangle(cornerRadius: 8).fill(ThemePalette.ui(.accent).opacity(0.08)))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(ThemePalette.ui(.accent).opacity(0.35), lineWidth: 0.8))
                .shadow(color: ThemePalette.ui(.accent).opacity(0.18), radius: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text("Agent Orbit").font(.system(size: 12, weight: .semibold)).foregroundColor(ThemePalette.ui(.text))
                Text(model.settings.remoteEnabled && model.settings.localEnabled ? "本机 + \(model.host)" : model.host).font(.system(size: 8, design: .monospaced)).foregroundColor(ThemePalette.ui(.muted))
            }
            Spacer(minLength: 1)
            HStack(spacing: 3) {
                Button { model.onMinimize?() } label: { Image(systemName: "minus") }
                    .buttonStyle(NeonIconStyle()).accessibilityLabel("收起为悬浮球").help("收起为悬浮球")
                Button { model.sound.toggle() } label: { Image(systemName: model.sound ? "speaker.wave.2" : "speaker.slash") }
                    .buttonStyle(NeonIconStyle(active: model.sound)).help("打开或关闭提示音")
                Button { model.pinned.toggle() } label: { Image(systemName: model.pinned ? "pin.fill" : "pin") }
                    .buttonStyle(NeonIconStyle(active: model.pinned)).help("切换始终置顶")
                Button { model.onSettings?() } label: { Image(systemName: "gearshape") }
                    .buttonStyle(NeonIconStyle()).accessibilityLabel("打开设置").help("监控、皮肤与提醒设置")
            }
        }.padding(.horizontal, 11).padding(.top, 9).padding(.bottom, 8)
    }
    var summary: some View {
        VStack(spacing: 7) {
            HStack(spacing: 7) {
                StatusMetric(value: hasLiveSessionData ? "\(visualStates.filter { $0.role == .waiting }.count)" : "—", label: "等待", color: hasLiveSessionData ? ThemePalette.ui(.waiting) : ThemePalette.ui(.offline))
                StatusMetric(value: hasLiveSessionData ? "\(visualStates.filter { $0.role == .running }.count)" : "—", label: "运行", color: hasLiveSessionData ? ThemePalette.ui(.running) : ThemePalette.ui(.offline))
                StatusMetric(value: "\(visualStates.filter { [.error, .attention, .unknown].contains($0) }.count)", label: "待处理", color: ThemePalette.ui(model.attentionVisualState.role))
            }
            HStack(spacing: 5) {
                Circle().fill(connectionColor).frame(width: 4, height: 4)
                    .shadow(color: connectionColor.opacity(0.7), radius: 4)
                Text(model.pendingPanes.isEmpty ? model.message : model.pendingSummary)
                    .font(.system(size: 9, weight: model.pendingPanes.isEmpty ? .regular : .medium)).lineLimit(1)
                    .foregroundColor(model.pendingPanes.contains { $0.effectiveResponse?.overdue == true } ? ThemePalette.ui(.attention) : ThemePalette.ui(.muted))
                    .help(model.message + "\n" + model.pendingSummary)
                Spacer(minLength: 0)
                if let priority = model.priorityPane, !model.pendingPanes.isEmpty {
                    Button("优先处理") { model.open(priority) }.buttonStyle(.plain)
                        .font(.system(size: 8, weight: .medium)).foregroundColor(ThemePalette.ui(.accent))
                }
                Text(model.connectionState == "partial" ? "PARTIAL" : model.connected ? "LIVE" : model.connectionState == "config_error" ? "SETTINGS" : model.connectionNeedsAttention ? "ERROR" : model.connectionState == "delayed" ? "WAITING" : "CONNECTING").font(.system(size: 8, weight: .semibold, design: .monospaced)).tracking(1.3)
            }.foregroundColor(ThemePalette.ui(.muted))
        }.padding(.horizontal, 11).padding(.bottom, 8)
    }
    @ViewBuilder var notice: some View {
        if !model.visibleCompletions.isEmpty || !model.unreadErrors.isEmpty {
            HStack(spacing: 6) {
                Image(systemName: model.unreadErrors.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle")
                Text(model.unreadErrors.isEmpty ? "\(model.visibleCompletions.count) 个会话刚完成" : "\(model.unreadErrors.count) 条待处理提醒")
                Spacer()
                Button("清除提示") { model.clearAlerts() }.buttonStyle(.plain).foregroundColor(ThemePalette.ui(.muted))
            }.font(.system(size: 10, weight: .medium))
                .foregroundColor(noticeColor)
                .padding(.horizontal, 12).padding(.bottom, 8)
        }
    }
    var body: some View {
        GeometryReader { geometry in
        VStack(spacing: 0) {
            header
            summary
            UsageOverview(metrics: model.metrics, connected: model.connected).padding(.horizontal, 11).padding(.bottom, 8)
            notice
            NeonRule()
            ScrollView {
                LazyVStack(spacing: 7) {
                    ForEach(model.unreadErrors.prefix(8)) { ActiveAlertCard(event: $0) }
                    if model.rows.isEmpty {
                        VStack(spacing: 11) {
                            Image(systemName: "antenna.radiowaves.left.and.right").font(.system(size: 22)).foregroundColor(ThemePalette.ui(.accent).opacity(0.6))
                            Text(model.connected ? "没有找到会话" : model.connectionNeedsAttention ? "监控尚未连接" : "正在读取关注的会话…").font(.system(size: 11)).foregroundColor(ThemePalette.ui(.muted))
                            if model.connectionNeedsAttention {
                                Text(model.message).font(.system(size: 11)).foregroundColor(connectionColor)
                                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                                Button("检查设置") { model.onSettings?() }.buttonStyle(.bordered)
                            }
                        }.frame(maxWidth: .infinity).padding(.vertical, 55)
                    }
                    ForEach(units) { unit in
                        SessionUnitCard(unit: unit, model: model, showDetails: geometry.size.height >= 330)
                    }
                }.padding(9)
            }
            NeonRule().opacity(0.45)
            HStack(spacing: 5) {
                Image(systemName: "cursorarrow").font(.system(size: 8))
                Text("双击查看会话")
                Spacer()
                Text("\(units.count) 个位置 · \(model.rows.count) 个会话").monospacedDigit()
            }.font(.system(size: 9)).foregroundColor(ThemePalette.ui(.muted)).padding(.horizontal, 12).padding(.vertical, 7)
        }
        }
        .frame(minWidth: 300, minHeight: 240)
        .background(NeonBackground())
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(LinearGradient(colors: [ThemePalette.ui(.accent).opacity(0.55), ThemePalette.ui(.secondaryAccent).opacity(0.18), ThemePalette.ui(.accent).opacity(0.22)], startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1).allowsHitTesting(false))
        .clipShape(RoundedRectangle(cornerRadius: 11))
        .preferredColorScheme(ThemePalette.scheme)
        .tint(ThemePalette.ui(.accent))
        // Leaf views read the palette without observing the model. Rebuild them
        // on a skin change even when the monitored session values are unchanged.
        .id(model.settings.theme + "|" + model.settings.orbitLabelColor)
    }
}
