import AppKit
import SwiftUI

/// A local explanation window. It owns no collector, connection or settings.
final class WaveGuidePresenter: NSObject, NSWindowDelegate {
    private static let shared = WaveGuidePresenter()
    private var window: NSWindow?

    static func show() {
        if !Thread.isMainThread {
            DispatchQueue.main.async { show() }
            return
        }
        shared.present()
    }

    private func present() {
        if let window {
            window.deminiaturize(nil)
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 780),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "声浪图说明 · Agent Orbit"
        window.minSize = NSSize(width: 590, height: 540)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = NSHostingView(rootView: WaveGuideView())
        window.center()
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        // Releasing the hosted view also stops its local appearance timer.
        window?.contentView = nil
        window = nil
    }
}

struct WaveGuideView: View {
    @State private var skin = ThemePalette.current
    @State private var focusedRole: ResourceRole?
    private var palette: PaletteResolver { PaletteResolver(theme: skin, statusStyle: "theme") }
    private let appearanceTimer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                diagram
                channelCards
                readingRules
                Text("声浪帮助你判断活动发生在哪里。任务是否结束，请看会话状态与等待提醒；需要处理时再进入原会话。")
                    .font(.system(size: 12)).foregroundColor(palette.ui(.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(26)
            .frame(maxWidth: 840, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(palette.ui(.base))
        .preferredColorScheme(palette.skin.light ? .light : .dark)
        .onReceive(appearanceTimer) { _ in
            if skin != ThemePalette.current { skin = ThemePalette.current }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("AGENT ORBIT  /  声浪图说明", systemImage: "circle.hexagongrid")
                    .font(.system(size: 10, weight: .semibold, design: .monospaced))
                    .tracking(1).foregroundColor(palette.ui(.accent))
                Spacer()
                Text(palette.skin.title).font(.system(size: 10, weight: .medium))
                    .foregroundColor(palette.ui(.muted))
            }
            Text("一眼分清，Agent 在忙什么。")
                .font(.system(size: 26, weight: .semibold)).foregroundColor(palette.ui(.text))
            Text("四条动态信号共用一条基线，内存单独静态显示。颜色跟随皮肤，形状帮助你分辨通道。")
                .font(.system(size: 13)).lineSpacing(4).foregroundColor(palette.ui(.muted))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var diagram: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("把外圈展开来看").font(.system(size: 14, weight: .semibold))
                    .foregroundColor(palette.ui(.text))
                Spacer()
                Text("示意图 · 非实时会话").font(.system(size: 10, weight: .medium))
                    .foregroundColor(palette.ui(.muted))
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Capsule().fill(palette.ui(.base)))
            }
            Text("最近 24 秒：过去 → 现在。点击图例，只突出一种信号。")
                .font(.system(size: 11)).foregroundColor(palette.ui(.muted))
            GuideSignalDiagram(palette: palette, focusedRole: focusedRole).frame(height: 184)
            HStack(spacing: 5) {
                ForEach(ResourceRole.allCases, id: \.self) { role in
                    Button {
                        focusedRole = focusedRole == role ? nil : role
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: role.symbolName).font(.system(size: 10))
                            Text(role.title).font(.system(size: 10, weight: .medium))
                        }
                        .foregroundColor(Color(nsColor: palette.resourceColor(role)))
                        .padding(.horizontal, 7).padding(.vertical, 7)
                        .frame(maxWidth: .infinity)
                        .background(RoundedRectangle(cornerRadius: 7).fill(
                            Color(nsColor: palette.resourceColor(role)).opacity(focusedRole == role ? 0.15 : 0.055)))
                        .overlay(RoundedRectangle(cornerRadius: 7).stroke(
                            Color(nsColor: palette.resourceColor(role)).opacity(focusedRole == role ? 0.55 : 0.12)))
                    }
                    .buttonStyle(.plain)
                    .help(role.graphicDescription)
                    .accessibilityLabel(role.title + "：" + role.graphicDescription)
                    .accessibilityAddTraits(focusedRole == role ? .isSelected : [])
                }
            }
            Text("留空仅用于示意未知：实际声浪不绘制未观测通道，悬停查看覆盖情况；已观测零活动没有波峰。这是固定示例，不展示你的会话数据。")
                .font(.system(size: 10)).foregroundColor(palette.ui(.muted))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(18)
        .background(RoundedRectangle(cornerRadius: 16).fill(palette.ui(.surface)))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(palette.ui(.accent).opacity(0.14)))
    }

    private var channelCards: some View {
        VStack(spacing: 10) {
            ForEach(ResourceRole.allCases, id: \.self) { role in
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: role.symbolName)
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(Color(nsColor: palette.resourceColor(role)))
                        .frame(width: 34, height: 34)
                        .background(RoundedRectangle(cornerRadius: 10).fill(
                            Color(nsColor: palette.resourceColor(role)).opacity(0.1)))
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(role.title).font(.system(size: 13, weight: .semibold))
                                .foregroundColor(palette.ui(.text))
                            Text(role == .memory ? "静态" : "最近 24 秒")
                                .font(.system(size: 10, weight: .medium)).foregroundColor(palette.ui(.muted))
                        }
                        Text(role.graphicDescription).font(.system(size: 11, weight: .medium))
                            .foregroundColor(Color(nsColor: palette.resourceColor(role)))
                        Text(role.description).font(.system(size: 11)).lineSpacing(3)
                            .foregroundColor(palette.ui(.muted)).fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(13).frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 12).fill(palette.ui(.surface)))
            }
        }
    }

    private var readingRules: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("这样读，不会误判").font(.system(size: 14, weight: .semibold))
                .foregroundColor(palette.ui(.text))
            rule("01", "起伏看变化，颜色看来源", "各通道有自己的单位和标尺，柱高不能跨通道比较。少量运行也按近期真实变化显示；没有新增活动就没有起伏。")
            rule("02", "未观测看悬停，零活动无波峰", "零表示已观测且没有活动；通道暂不可观测时不绘制，悬停可查看覆盖情况。断线、平台限制和共享资源都可能让数据不足。")
            rule("03", "等待就收起，完成看状态", "会话等待输入或停止后，动态声浪收起。工具结束、后台终端保留或内存仍有占用，都不能单独证明任务还在运行。")
        }
    }

    private func rule(_ number: String, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(number).font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundColor(palette.ui(.accent)).padding(.top, 2)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 12, weight: .medium)).foregroundColor(palette.ui(.text))
                Text(detail).font(.system(size: 11)).lineSpacing(3).foregroundColor(palette.ui(.muted))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// Fixed, deliberately different examples. Nil is an observation gap, not zero.
/// All four dynamic shapes share the same baseline and 24 one-second slots.
private struct GuideSignalDiagram: View {
    let palette: PaletteResolver
    let focusedRole: ResourceRole?
    private let output: [CGFloat?] = [0, 0, 0.12, 0.28, 0.52, 0.24, 0, 0.16, 0.38, nil, nil, 0, 0.12, 0.42, 0.78, 0.48, 0.18, 0, 0, 0.27, 0.6, 0.36, 0.15, 0]
    private let io: [CGFloat?] = [0, 0.17, 0.33, 0.14, 0, 0, 0.54, 0.78, 0.32, nil, nil, 0.18, 0.46, 0.22, 0, 0, 0.3, 0.47, 0.19, 0, 0, 0.24, 0.4, 0.14]
    private let tools: [CGFloat?] = [0, 0, 0, 0.22, 0, 0, 0, 0, 0.45, nil, nil, 0, 0, 0, 0, 0.7, 0, 0, 0, 0, 0.3, 0, 0, 0]
    private let network: [CGFloat?] = [0, 0.18, 0.62, 0.42, 0.15, 0, 0, 0, 0.2, nil, nil, 0.32, 0.72, 0.35, 0.1, 0, 0, 0.18, 0.52, 0.86, 0.36, 0.12, 0, 0]

    var body: some View {
        Canvas { context, size in
            let left: CGFloat = 10, right = size.width - 10
            let baseline: CGFloat = 127, height: CGFloat = 87
            let step = (right - left) / 24
            func x(_ index: Int) -> CGFloat { left + (CGFloat(index) + 0.5) * step }
            func y(_ value: CGFloat) -> CGFloat { baseline - value * height }
            func color(_ role: ResourceRole) -> Color {
                Color(nsColor: palette.resourceColor(role)).opacity(focusedRole == nil || focusedRole == role ? 1 : 0.12)
            }
            var grid = Path()
            for fraction in [CGFloat(0), 0.25, 0.5, 0.75, 1] {
                let position = left + fraction * (right - left)
                grid.move(to: CGPoint(x: position, y: 27)); grid.addLine(to: CGPoint(x: position, y: baseline))
            }
            context.stroke(grid, with: .color(palette.ui(.muted).opacity(0.13)), style: StrokeStyle(lineWidth: 0.7, dash: [2, 4]))
            var common = Path()
            common.move(to: CGPoint(x: left, y: baseline)); common.addLine(to: CGPoint(x: right, y: baseline))
            context.stroke(common, with: .color(palette.ui(.muted).opacity(0.4)), lineWidth: 0.8)
            for (index, title) in ["−24 秒", "−18", "−12", "−6", "现在"].enumerated() {
                let position = left + CGFloat(index) * (right - left) / 4
                context.draw(Text(title).font(.system(size: 9, design: .monospaced)).foregroundColor(palette.ui(.muted)),
                             at: CGPoint(x: position, y: baseline + 16), anchor: index == 0 ? .leading : index == 4 ? .trailing : .center)
            }
            // Build separate segments so missing samples never get bridged.
            func segments(_ values: [CGFloat?]) -> [[CGPoint]] {
                var all: [[CGPoint]] = [], current: [CGPoint] = []
                for (index, value) in values.enumerated() {
                    if let value {
                        current.append(CGPoint(x: x(index), y: y(value)))
                    } else if !current.isEmpty { all.append(current); current = [] }
                }
                if !current.isEmpty { all.append(current) }
                return all
            }
            for points in segments(output) where points.count > 1 {
                var band = Path(); band.move(to: CGPoint(x: points[0].x, y: baseline))
                for point in points { band.addLine(to: point) }
                band.addLine(to: CGPoint(x: points.last!.x, y: baseline)); band.closeSubpath()
                context.fill(band, with: .color(color(.output).opacity(0.22)))
                var ridge = Path(); ridge.addLines(points)
                context.stroke(ridge, with: .color(color(.output).opacity(0.85)), lineWidth: 1.2)
            }
            for (index, value) in io.enumerated() {
                guard let value, value > 0 else { continue }
                let rect = CGRect(x: x(index) - 1.7, y: y(value), width: 3.4, height: baseline - y(value))
                context.fill(Path(roundedRect: rect, cornerRadius: 1), with: .color(color(.io).opacity(0.72)))
            }
            for points in segments(network) where points.count > 1 {
                var line = Path(); line.addLines(points)
                context.stroke(line, with: .color(color(.network)), lineWidth: 1.25)
            }
            for index in network.indices {
                guard let value = network[index], value > 0.3 else { continue }
                let previous = index > 0 ? network[index - 1] ?? 0 : 0
                let next = index < 23 ? network[index + 1] ?? 0 : 0
                if value >= previous && value > next {
                    var cap = Path(); cap.move(to: CGPoint(x: x(index) - 3.5, y: y(value)))
                    cap.addLine(to: CGPoint(x: x(index) + 3.5, y: y(value)))
                    context.stroke(cap, with: .color(color(.network)), lineWidth: 2.2)
                }
            }
            for (index, value) in tools.enumerated() {
                guard let value, value > 0 else { continue }
                let tip = CGPoint(x: x(index), y: y(value))
                var pin = Path(); pin.move(to: CGPoint(x: tip.x, y: baseline)); pin.addLine(to: tip)
                context.stroke(pin, with: .color(color(.tools)), lineWidth: 1)
                var diamond = Path(); diamond.move(to: CGPoint(x: tip.x, y: tip.y - 3.4))
                diamond.addLine(to: CGPoint(x: tip.x + 3.4, y: tip.y)); diamond.addLine(to: CGPoint(x: tip.x, y: tip.y + 3.4))
                diamond.addLine(to: CGPoint(x: tip.x - 3.4, y: tip.y)); diamond.closeSubpath()
                context.fill(diamond, with: .color(color(.tools)))
            }
            // An active tool is a static outline; it never creates a result pulse.
            let active = CGPoint(x: x(23), y: baseline - 8)
            var pending = Path(); pending.move(to: CGPoint(x: active.x, y: active.y - 3.4))
            pending.addLine(to: CGPoint(x: active.x + 3.4, y: active.y)); pending.addLine(to: CGPoint(x: active.x, y: active.y + 3.4))
            pending.addLine(to: CGPoint(x: active.x - 3.4, y: active.y)); pending.closeSubpath()
            context.stroke(pending, with: .color(color(.tools)), lineWidth: 1)
            context.draw(Text("未知（示意）").font(.system(size: 9)).foregroundColor(palette.ui(.muted)),
                         at: CGPoint(x: (x(9) + x(10)) / 2, y: 18))
            var gap = Path(); gap.move(to: CGPoint(x: x(9), y: 27)); gap.addLine(to: CGPoint(x: x(10), y: 27))
            context.stroke(gap, with: .color(palette.ui(.muted).opacity(0.5)), lineWidth: 1)
            var arc = Path(); arc.addArc(center: CGPoint(x: left + 9, y: 173), radius: 8,
                                        startAngle: .degrees(195), endAngle: .degrees(345), clockwise: false)
            context.stroke(arc, with: .color(color(.memory)), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
            context.draw(Text("内存另作静态细弧，不随工作节奏起伏").font(.system(size: 10)).foregroundColor(palette.ui(.muted)),
                         at: CGPoint(x: left + 26, y: 169), anchor: .leading)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("固定声浪示意：最近二十四秒从左向右；输出光带、进程 I/O 柱、工具事件针、网络曲线共用基线，内存另作静态细弧。空心菱形表示工具尚未返回。中间留空仅演示未知，实际数据覆盖请悬停查看。")
    }
}
