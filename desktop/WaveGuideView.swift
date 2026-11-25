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
    @State private var waveStyle = ThemePalette.currentWaveStyle
    @State private var focusedRole: ResourceRole?
    private var palette: PaletteResolver { PaletteResolver(theme: skin, statusStyle: "theme") }
    private var resolvedWaveSkin: WaveSkin? { WaveSkin(rawValue: palette.waveStyle(waveStyle)) }
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
            if waveStyle != ThemePalette.currentWaveStyle { waveStyle = ThemePalette.currentWaveStyle }
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
            Text("CPU、GPU、磁盘、网络与对话输出五层叠在同一圈。颜色区分通道，五层统一使用所选声浪皮肤的造型。")
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
            Text(waveSkinDescription).font(.system(size: 11, weight: .medium))
                .foregroundColor(palette.ui(.accent)).fixedSize(horizontal: false, vertical: true)
            GuideSignalDiagram(palette: palette, waveStyle: palette.waveStyle(waveStyle), focusedRole: focusedRole).frame(height: 184)
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
            Text("留空仅用于示意未知：缺失处不补峰；仍在运行时，已观测历史保留到原来的24秒期限。悬停查看当前覆盖；已观测零活动没有新波峰。这是固定示例，不展示你的会话数据。")
                .font(.system(size: 10)).foregroundColor(palette.ui(.muted))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(18)
        .background(RoundedRectangle(cornerRadius: 16).fill(palette.ui(.surface)))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(palette.ui(.accent).opacity(0.14)))
    }

    private var waveSkinDescription: String {
        guard let selected = resolvedWaveSkin else { return "当前声浪已关闭；选择声浪皮肤后，五层统一显示所选造型。" }
        let mode = waveStyle == "theme" ? "跟随界面皮肤" : "独立声浪皮肤"
        return mode + " · " + selected.title + "：" + selected.description
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
                            Text("最近 24 秒")
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
            rule("02", "未观测看悬停，历史按时间移出", "零表示已观测且没有活动。短暂缺指标时不增加波峰，近24秒的真实历史继续移出；恢复采样先建基线。任务进入等待、异常或离线时停止声浪。")
            rule("03", "等待就收起，完成看状态", "会话等待输入或停止后，动态声浪收起。后台终端保留、显存或内存占用不能单独证明任务仍在运行；已绑定实验的真实进展会继续显示后台运行。")
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
/// All channels use one selected skin, baseline and 24 one-second slots.
private struct GuideSignalDiagram: View {
    let palette: PaletteResolver
    let waveStyle: String
    let focusedRole: ResourceRole?
    private let cpu: [CGFloat?] = [0, 0, 0.12, 0.28, 0.52, 0.24, 0, 0.16, 0.38, nil, nil, 0, 0.12, 0.42, 0.78, 0.48, 0.18, 0, 0, 0.27, 0.6, 0.36, 0.15, 0]
    private let disk: [CGFloat?] = [0, 0.17, 0.33, 0.14, 0, 0, 0.54, 0.78, 0.32, nil, nil, 0.18, 0.46, 0.22, 0, 0, 0.3, 0.47, 0.19, 0, 0, 0.24, 0.4, 0.14]
    private let gpu: [CGFloat?] = [0, 0, 0, 0.22, 0, 0, 0, 0, 0.45, nil, nil, 0, 0, 0, 0, 0.7, 0, 0, 0, 0, 0.3, 0, 0, 0]
    private let network: [CGFloat?] = [0, 0.18, 0.62, 0.42, 0.15, 0, 0, 0, 0.2, nil, nil, 0.32, 0.72, 0.35, 0.1, 0, 0, 0.18, 0.52, 0.86, 0.36, 0.12, 0, 0]
    private let output: [CGFloat?] = [0, 0, 0.14, 0.4, 0.17, 0, 0, 0.24, 0, nil, nil, 0.15, 0, 0.33, 0.55, 0, 0, 0.19, 0.42, 0.26, 0, 0.53, 0.72, 0.35]

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
            // Build positive segments so neither zero nor unknown is bridged.
            func segments(_ values: [CGFloat?]) -> [[CGPoint]] {
                var all: [[CGPoint]] = [], current: [CGPoint] = []
                for (index, value) in values.enumerated() {
                    if let value, value > 0 {
                        current.append(CGPoint(x: x(index), y: y(value)))
                    } else if !current.isEmpty { all.append(current); current = [] }
                }
                if !current.isEmpty { all.append(current) }
                return all
            }
            func curve(_ points: [CGPoint]) -> Path {
                var path = Path()
                guard let first = points.first else { return path }
                path.move(to: first)
                for index in 1..<points.count {
                    let before = points[index - 1], after = points[index]
                    let middle = (before.x + after.x) / 2
                    path.addCurve(to: after, control1: CGPoint(x: middle, y: before.y),
                                  control2: CGPoint(x: middle, y: after.y))
                }
                return path
            }
            let profiles: [(ResourceRole, [CGFloat?])] = [(.cpu, cpu), (.gpu, gpu), (.disk, disk), (.network, network), (.output, output)]
            for (role, values) in profiles {
                let ink = color(role)
                switch WaveSkin(rawValue: waveStyle) {
                case .curve, .aurora:
                    for points in segments(values) {
                        guard let first = points.first, let last = points.last else { continue }
                        if points.count == 1 {
                            var cap = Path(); cap.move(to: CGPoint(x: first.x - 2, y: first.y))
                            cap.addLine(to: CGPoint(x: first.x + 2, y: first.y))
                            context.stroke(cap, with: .color(ink), lineWidth: 1.3)
                            continue
                        }
                        let ridge = curve(points)
                        if waveStyle == WaveSkin.aurora.rawValue {
                            var band = ridge
                            band.addLine(to: CGPoint(x: last.x, y: baseline))
                            band.addLine(to: CGPoint(x: first.x, y: baseline)); band.closeSubpath()
                            context.fill(band, with: .color(ink.opacity(0.14)))
                        }
                        context.stroke(ridge, with: .color(ink.opacity(0.88)), lineWidth: 1.3)
                    }
                case .bars, .plasma, .radar, .dots:
                    for (index, value) in values.enumerated() {
                        guard let value, value > 0 else { continue }
                        let tip = CGPoint(x: x(index), y: y(value))
                        var stem = Path(); stem.move(to: CGPoint(x: tip.x, y: baseline)); stem.addLine(to: tip)
                        switch WaveSkin(rawValue: waveStyle) {
                        case .bars:
                            let rect = CGRect(x: tip.x - 1.5, y: tip.y, width: 3, height: baseline - tip.y)
                            context.fill(Path(roundedRect: rect, cornerRadius: 0.6), with: .color(ink.opacity(0.7)))
                            var cap = Path(); cap.move(to: CGPoint(x: tip.x - 2.4, y: tip.y)); cap.addLine(to: CGPoint(x: tip.x + 2.4, y: tip.y))
                            context.stroke(cap, with: .color(ink), lineWidth: 0.8)
                        case .plasma:
                            for (width, opacity) in [(CGFloat(7), 0.1), (4, 0.22), (1.5, 0.85)] {
                                context.stroke(stem, with: .color(ink.opacity(opacity)), style: StrokeStyle(lineWidth: width, lineCap: .round))
                            }
                            context.fill(Path(ellipseIn: CGRect(x: tip.x - 1.7, y: tip.y - 1.7, width: 3.4, height: 3.4)), with: .color(ink))
                        case .radar:
                            context.stroke(stem, with: .color(ink.opacity(0.7)), lineWidth: 0.8)
                            var diamond = Path(); diamond.move(to: CGPoint(x: tip.x, y: tip.y - 2.8))
                            diamond.addLine(to: CGPoint(x: tip.x + 2.8, y: tip.y)); diamond.addLine(to: CGPoint(x: tip.x, y: tip.y + 2.8))
                            diamond.addLine(to: CGPoint(x: tip.x - 2.8, y: tip.y)); diamond.closeSubpath()
                            context.stroke(diamond, with: .color(ink), lineWidth: 0.9)
                        case .dots:
                            context.fill(Path(ellipseIn: CGRect(x: tip.x - 1.8, y: tip.y - 1.8, width: 3.6, height: 3.6)), with: .color(ink))
                        default: break
                        }
                    }
                case nil: break
                }
            }
            context.draw(Text("未知（示意）").font(.system(size: 9)).foregroundColor(palette.ui(.muted)),
                         at: CGPoint(x: (x(9) + x(10)) / 2, y: 18))
            var gap = Path(); gap.move(to: CGPoint(x: x(9), y: 27)); gap.addLine(to: CGPoint(x: x(10), y: 27))
            context.stroke(gap, with: .color(palette.ui(.muted).opacity(0.5)), lineWidth: 1)

        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("固定声浪示意：最近二十四秒从左向右；五种通道由颜色区分，共用基线和\(WaveSkin(rawValue: waveStyle)?.title ?? "关闭")声浪皮肤。对话输出单位是观测字符/秒，非Token。中间留空仅演示未知，实际数据覆盖请悬停查看。")
    }
}
