import AppKit
import SwiftUI

enum Neon {
    static var base: Color { Color(nsColor: ThemePalette.base) }
    static var surface: Color { Color(nsColor: ThemePalette.surface) }
    static var cyan: Color { Color(nsColor: ThemePalette.accent) }
    static var violet: Color { Color(nsColor: ThemePalette.violet) }
    static var green: Color { Color(nsColor: ThemePalette.green) }
    static var amber: Color { Color(nsColor: ThemePalette.amber) }
    static var red: Color { Color(nsColor: ThemePalette.red) }
    static var text: Color { Color(nsColor: ThemePalette.text) }
    static var muted: Color { Color(nsColor: ThemePalette.muted) }
}

extension NSColor {
    static var neonCyan: NSColor { ThemePalette.accent }
    static var neonGreen: NSColor { ThemePalette.green }
    static var neonAmber: NSColor { ThemePalette.amber }
    static var neonRed: NSColor { ThemePalette.red }
    static var neonMuted: NSColor { ThemePalette.muted }
}

struct NeonBackground: View {
    var body: some View {
        ZStack {
            LinearGradient(colors: [Neon.surface, Neon.base, Neon.base], startPoint: .topLeading, endPoint: .bottomTrailing)
            Canvas { context, size in
                var grid = Path()
                for x in stride(from: CGFloat(0), through: size.width, by: 26) {
                    grid.move(to: CGPoint(x: x, y: 0)); grid.addLine(to: CGPoint(x: x, y: size.height))
                }
                for y in stride(from: CGFloat(0), through: size.height, by: 26) {
                    grid.move(to: CGPoint(x: 0, y: y)); grid.addLine(to: CGPoint(x: size.width, y: y))
                }
                context.stroke(grid, with: .color(Neon.cyan.opacity(0.035)), lineWidth: 0.5)
            }
            RadialGradient(colors: [Neon.cyan.opacity(0.06), .clear], center: .topLeading, startRadius: 0, endRadius: 360)
        }.allowsHitTesting(false)
    }
}

struct NeonRule: View {
    var body: some View {
        Rectangle().fill(LinearGradient(colors: [Neon.cyan.opacity(0.6), Neon.violet.opacity(0.32), Neon.cyan.opacity(0.05)], startPoint: .leading, endPoint: .trailing)).frame(height: 1)
    }
}

struct NeonIconStyle: ButtonStyle {
    var active = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 12, weight: .medium))
            .foregroundColor(active ? Neon.cyan : Neon.muted)
            .frame(width: 24, height: 24)
            .background(RoundedRectangle(cornerRadius: 7).fill(Neon.cyan.opacity(configuration.isPressed ? 0.2 : active ? 0.09 : 0.035)))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(Neon.cyan.opacity(active ? 0.35 : 0.12), lineWidth: 0.7))
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
            Text(label).font(.system(size: 9, weight: .medium)).foregroundColor(Neon.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 8).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 9).fill(color.opacity(0.045)))
        .overlay(RoundedRectangle(cornerRadius: 9).stroke(color.opacity(0.17), lineWidth: 0.7))
    }
}

struct PaneCard: View {
    let pane: Pane
    let alert: Bool
    let connected: Bool
    let open: () -> Void
    var showPreviews = true
    var showBackgroundCounts = true
    var color: Color {
        if !connected { return Neon.muted }
        if pane.isCritical { return Neon.red }
        if pane.needsAttention { return Neon.amber }
        if pane.state == "idle" { return Neon.green }
        if pane.state == "running" { return Neon.amber }
        return Neon.muted
    }
    var heading: some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 5, height: 5).shadow(color: color.opacity(0.7), radius: 4)
            Text(pane.session).font(.system(size: 12, weight: .semibold, design: .monospaced)).foregroundColor(Neon.text).lineLimit(1)
            if let agent = pane.agent, !agent.isEmpty {
                Text((agent == "claude" ? "Claude" : "Codex") + (pane.source == "local" ? " · 本机" : "")).font(.system(size: 8, weight: .medium, design: .monospaced))
                    .foregroundColor(agent == "claude" ? Neon.violet : Neon.cyan)
                    .padding(.horizontal, 5).padding(.vertical, 3)
                    .background(RoundedRectangle(cornerRadius: 4).fill((agent == "claude" ? Neon.violet : Neon.cyan).opacity(0.09)))
            }
            Spacer(minLength: 0)
            Text(connected ? pane.status : "上次：" + pane.status).font(.system(size: 9, weight: .medium))
                .foregroundColor(color).lineLimit(1).minimumScaleFactor(0.85)
        }
    }
    @ViewBuilder var details: some View {
        if showBackgroundCounts, let counts = pane.backgroundCountsText {
            Text((connected ? "" : "上次：") + counts)
                .font(.system(size: 9, design: .monospaced)).foregroundColor(Neon.muted)
                .lineLimit(1).minimumScaleFactor(0.8)
                .help("子智能体统计正在工作的任务；有分母时表示在线总数。保留终端可能仍存活或等待输入，数量不会把已结束的主对话判为运行中。数量待核实时不显示为 0。")
        }
        if alert, let completed = pane.completedAt, pane.state == "idle" {
            HStack(spacing: 5) {
                Image(systemName: "checkmark.circle.fill")
                Text("刚完成")
                Text(Date(timeIntervalSince1970: completed), style: .time)
                Spacer()
            }.font(.system(size: 10, weight: .medium)).foregroundColor(Neon.green)
        }
        if let error = pane.errorSummary, !error.isEmpty {
            HStack(alignment: .top, spacing: 5) {
                Image(systemName: "exclamationmark.triangle")
                Text(error).lineLimit(3).textSelection(.enabled)
            }.font(.system(size: 11, weight: .medium)).foregroundColor(color)
        }
        if showPreviews, pane.state == "running", !pane.workingIsBackgroundCount, let working = pane.working, !working.isEmpty {
            Text(working).font(.system(size: 9, design: .monospaced)).foregroundColor(Neon.muted.opacity(0.85)).lineLimit(1)
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            heading
            details
            HStack(alignment: .bottom, spacing: 8) {
                if showPreviews { Text(pane.preview.suffix(2).joined(separator: "\n"))
                    .font(.system(size: 10.5)).foregroundColor(Neon.muted).lineSpacing(2).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading) } else { Spacer(minLength: 0) }
                Button(action: open) { Image(systemName: "arrow.up.right") }
                    .buttonStyle(NeonIconStyle()).accessibilityLabel(pane.source == "local" ? "打开 Codex 应用" : "进入远端会话")
                    .help(pane.source == "local" ? "打开 Codex 应用查看本机会话" : "进入这个远端会话").disabled(!connected || pane.state == "missing")
            }
        }
        .padding(.vertical, 9).padding(.leading, 12).padding(.trailing, 8)
        .background(RoundedRectangle(cornerRadius: 11).fill(LinearGradient(colors: [Neon.surface.opacity(0.96), Neon.base.opacity(0.94)], startPoint: .topLeading, endPoint: .bottomTrailing)))
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(color.opacity(alert || pane.needsAttention ? 0.55 : 0.2), lineWidth: 0.8))
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 2, height: 25)
                .shadow(color: color.opacity(0.5), radius: 5).padding(.leading, 0.5)
        }
        .shadow(color: alert ? Neon.green.opacity(0.09) : .clear, radius: 12)
        .contentShape(Rectangle()).onTapGesture(count: 2, perform: open)
    }
}

struct ActiveAlertCard: View {
    let event: ErrorEvent
    var color: Color { event.isCritical ? Neon.red : Neon.amber }
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: event.isCritical ? "exclamationmark.triangle.fill" : "hand.raised.fill")
                Text(event.session).fontWeight(.semibold)
                Spacer()
                Text("待处理").font(.system(size: 9))
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
        editor.textColor = ThemePalette.text
        editor.insertionPointColor = .neonCyan
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
        editor.textColor = ThemePalette.text; editor.insertionPointColor = .neonCyan
        guard editor.string != text else { return }
        // Frequent monitoring updates must not reset the caret or undo history.
        editor.string = text
    }
}

struct PanelView: View {
    @ObservedObject var model: PanelModel
    var header: some View {
        HStack(spacing: 8) {
            Image(systemName: "terminal").font(.system(size: 14, weight: .medium)).foregroundColor(Neon.cyan)
                .frame(width: 25, height: 25)
                .background(RoundedRectangle(cornerRadius: 8).fill(Neon.cyan.opacity(0.08)))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Neon.cyan.opacity(0.35), lineWidth: 0.8))
                .shadow(color: Neon.cyan.opacity(0.18), radius: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text("Agent Orbit").font(.system(size: 12, weight: .semibold)).foregroundColor(Neon.text)
                Text(model.settings.remoteEnabled && model.settings.localEnabled ? "本机 + \(model.host)" : model.host).font(.system(size: 8, design: .monospaced)).foregroundColor(Neon.muted)
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
                StatusMetric(value: model.connected ? "\(model.rows.filter { $0.state == "idle" && $0.available != false }.count)" : "—", label: "等待", color: model.connected ? Neon.green : Neon.muted)
                StatusMetric(value: model.connected ? "\(model.rows.filter { $0.state == "running" && $0.available != false }.count)" : "—", label: "运行", color: model.connected ? Neon.amber : Neon.muted)
                StatusMetric(value: "\(model.criticalCount + model.warningCount)", label: "待处理", color: model.criticalCount > 0 ? Neon.red : model.warningCount > 0 ? Neon.amber : Neon.muted)
            }
            HStack(spacing: 5) {
                Circle().fill(model.connected && model.connectionState != "partial" ? Neon.cyan : Neon.amber).frame(width: 4, height: 4)
                    .shadow(color: Neon.cyan.opacity(0.7), radius: 4)
                Text(model.message).font(.system(size: 9)).lineLimit(1).help(model.message)
                Spacer()
                Text(model.connectionState == "partial" ? "PARTIAL" : model.connected ? "LIVE" : model.connectionState == "config_error" ? "SETTINGS" : model.connectionNeedsAttention ? "ERROR" : model.connectionState == "delayed" ? "WAITING" : "CONNECTING").font(.system(size: 8, weight: .semibold, design: .monospaced)).tracking(1.3)
            }.foregroundColor(Neon.muted)
        }.padding(.horizontal, 11).padding(.bottom, 8)
    }
    @ViewBuilder var notice: some View {
        if !model.unread.isEmpty || !model.unreadErrors.isEmpty {
            HStack(spacing: 6) {
                Image(systemName: model.unreadErrors.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle")
                Text(model.unreadErrors.isEmpty ? "\(model.unread.count) 个会话刚完成" : "\(model.unreadErrors.count) 条待处理提醒")
                Spacer()
                Button("清除提示") { model.clearAlerts() }.buttonStyle(.plain).foregroundColor(Neon.muted)
            }.font(.system(size: 10, weight: .medium))
                .foregroundColor(model.unreadErrors.isEmpty ? Neon.green : Neon.amber)
                .padding(.horizontal, 12).padding(.bottom, 8)
        }
    }
    var body: some View {
        VStack(spacing: 0) {
            header
            summary
            notice
            NeonRule()
            ScrollView {
                LazyVStack(spacing: 7) {
                    ForEach(model.unreadErrors.prefix(8)) { ActiveAlertCard(event: $0) }
                    if model.rows.isEmpty {
                        VStack(spacing: 11) {
                            Image(systemName: "antenna.radiowaves.left.and.right").font(.system(size: 22)).foregroundColor(Neon.cyan.opacity(0.6))
                            Text(model.connected ? "没有找到会话" : model.connectionNeedsAttention ? "监控尚未连接" : "正在读取关注的会话…").font(.system(size: 11)).foregroundColor(Neon.muted)
                            if model.connectionNeedsAttention {
                                Text(model.message).font(.system(size: 11)).foregroundColor(Neon.amber)
                                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                                Button("检查设置") { model.onSettings?() }.buttonStyle(.bordered)
                            }
                        }.frame(maxWidth: .infinity).padding(.vertical, 55)
                    }
                    ForEach(model.rows) { pane in
                        PaneCard(pane: pane, alert: model.unread.contains { $0.paneId == pane.paneId }, connected: pane.available ?? model.connected, open: { model.open(pane) }, showPreviews: model.settings.showPreviews, showBackgroundCounts: model.settings.showBackgroundCounts)
                    }
                }.padding(9)
            }
            NeonRule().opacity(0.45)
            HStack(spacing: 5) {
                Image(systemName: "cursorarrow").font(.system(size: 8))
                Text("双击查看会话")
                Spacer()
                Text("\(model.rows.count) 个会话").monospacedDigit()
            }.font(.system(size: 9)).foregroundColor(Neon.muted).padding(.horizontal, 12).padding(.vertical, 7)
        }
        .frame(minWidth: 300, minHeight: 240)
        .background(NeonBackground())
        .overlay(RoundedRectangle(cornerRadius: 11).stroke(LinearGradient(colors: [Neon.cyan.opacity(0.55), Neon.violet.opacity(0.18), Neon.cyan.opacity(0.22)], startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1).allowsHitTesting(false))
        .clipShape(RoundedRectangle(cornerRadius: 11))
        .preferredColorScheme(ThemePalette.scheme)
    }
}
