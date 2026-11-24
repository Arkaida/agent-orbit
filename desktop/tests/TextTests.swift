import AppKit

/// Real CoreText drawing and controlled Objective-C exceptions. Every label is
/// synthetic; the suite never loads application settings or agent sessions.
@MainActor
private final class TextTestSuite {
    var assertions = 0
    var failures: [String] = []

    func check(_ condition: @autoclosure () -> Bool, _ message: String) {
        assertions += 1
        if !condition() { failures.append(message) }
    }

    private func bitmap() -> CGContext {
        CGContext(data: nil, width: 256, height: 256, bitsPerComponent: 8, bytesPerRow: 256 * 4,
                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue)!
    }

    private func channels(_ context: CGContext) -> (red: Int, green: Int, blue: Int, alpha: Int) {
        let pixels = context.data!.assumingMemoryBound(to: UInt8.self)
        var sums = (red: 0, green: 0, blue: 0, alpha: 0)
        for index in stride(from: 0, to: context.bytesPerRow * context.height, by: 4) {
            sums.red += Int(pixels[index]); sums.green += Int(pixels[index + 1])
            sums.blue += Int(pixels[index + 2]); sums.alpha += Int(pixels[index + 3])
        }
        return sums
    }

    func unicodeAndInputLimits() {
        let examples = ["orbit-agent-42", "目标会话", "e\u{301}cole", "👩🏽‍💻-🛰️", "مرحبا بالعالم", "עברית", "A\u{0001}B\u{007f}C"]
        for text in examples {
            let layout = AOTextLayout.make(text: text, fontSize: 13)
            check(layout.isUsable, "Unicode and sanitized control text must produce a usable layout")
            check(layout.width.isFinite && layout.width >= 0 && layout.arcWidth.isFinite && layout.arcWidth >= 0,
                  "Unicode geometry must remain finite")
            check(layout.height.isFinite && layout.height > 0 && layout.ascender.isFinite,
                  "Unicode vertical metrics must remain finite")
            check(layout.characterCount == text.count, "Arc layout must preserve Unicode grapheme clusters")
        }
        let empty = AOTextLayout.make(text: "", fontSize: 12)
        check(empty.isUsable && empty.width == 0 && empty.arcWidth == 0 && empty.characterCount == 0,
              "Empty labels must be harmless empty layouts")
        for fontSize in [CGFloat.nan, .infinity, -1, 0, 0.49, 257] {
            let layout = AOTextLayout.make(text: "invalid-font", fontSize: fontSize)
            check(!layout.isUsable && layout.width.isFinite && layout.height.isFinite && layout.arcWidth.isFinite,
                  "Invalid native font sizes must return finite placeholder layouts")
        }
        for text in [String(repeating: "a", count: 513), String(repeating: "a", count: 257),
                     String(repeating: "👩🏽‍💻", count: 74)] {
            let layout = AOTextLayout.make(text: text, fontSize: 12)
            check(!layout.isUsable && layout.width.isFinite, "Native length limits must fail into a placeholder")
        }
        let cache = OrbitTextLayoutCache(capacity: 3)
        for fontSize in [CGFloat.nan, .infinity, -1, 0, 0.49, 257] {
            check(cache.layout("rejected", fontSize: fontSize, mode: .flat) == nil,
                  "Swift must reject invalid font geometry before entering native code")
        }
        check(cache.layout(String(repeating: "👩🏽‍💻", count: 74), fontSize: 12, mode: .arc) == nil,
              "UTF-16 bounds must apply before bridging complex emoji strings")
        check(cache.layout(String(repeating: "a", count: 257), fontSize: 12, mode: .arc) == nil,
              "The Swift grapheme bound must match native arc limits")
        check(cache.count == 0 && cache.creationCount == 0, "Rejected input must not grow the layout cache")
        for fontSize in [CGFloat(0.5), 1, 128, 256] {
            check(cache.layout("valid-font", fontSize: fontSize, mode: .flat)?.isUsable == true,
                  "Swift and native accepted font-size bounds must agree")
        }
    }

    func cacheAndTruncation() {
        let cache = OrbitTextLayoutCache(capacity: 4)
        let first = cache.layout("alpha", fontSize: 12, mode: .flat)!
        check(first === cache.layout("alpha", fontSize: 12, mode: .flat), "Identical geometry must reuse one immutable native layout")
        check(cache.creationCount == 1, "Repeated measuring must not rebuild native attributes")
        check(first !== cache.layout("alpha", fontSize: 12, mode: .arc), "Arc and flat cache modes must remain independent")
        check(first !== cache.layout("alpha", fontSize: 13, mode: .flat), "Font-size changes must establish distinct geometry")
        check(first !== cache.layout("beta", fontSize: 12, mode: .flat), "Text changes must establish distinct geometry")
        check(cache.count == 4, "The configured cache bound must include all key dimensions")

        let lru = OrbitTextLayoutCache(capacity: 2)
        let alpha = lru.layout("alpha", fontSize: 12, mode: .flat)!
        _ = lru.layout("beta", fontSize: 12, mode: .flat)
        _ = lru.layout("alpha", fontSize: 12, mode: .flat)
        _ = lru.layout("gamma", fontSize: 12, mode: .flat)
        check(lru.layout("alpha", fontSize: 12, mode: .flat) === alpha, "Recently used layouts must survive LRU eviction")
        _ = lru.layout("beta", fontSize: 12, mode: .flat)
        check(lru.creationCount == 4 && lru.count == 2, "Evicted labels must rebuild without exceeding capacity")
        for index in 0..<40 { _ = lru.layout("counter-\(index)", fontSize: 12, mode: .flat) }
        check(lru.count == 2, "Changing live counters must not grow an unbounded cache")
        lru.removeAll()
        check(lru.count == 0, "Cache reset must release held layouts")
        check(OrbitTextLayoutCache(capacity: 0).capacity == 1 && OrbitTextLayoutCache(capacity: 10_000).capacity == 512,
              "Even external cache configuration must remain bounded")

        let text = "总量 12345678901234567890 tok"
        let fitted = cache.fittedText(text, fontSize: 12, maxWidth: 85)
        check(fitted.contains("…") && fitted.hasPrefix("总") && fitted.hasSuffix("k"),
              "Long counters must preserve both ends with an explicit middle ellipsis")
        check(cache.width(fitted, fontSize: 12) <= 85, "Ellipsized counters must fit their measured drawing region")
        let unicode = "👩🏽‍💻e\u{301}中👩🏽‍💻e\u{301}文👩🏽‍💻"
        let unicodeFitted = cache.fittedText(unicode, fontSize: 12, maxWidth: 60)
        check(unicodeFitted.contains("…") && !unicodeFitted.contains("�"), "Truncation must preserve composed Unicode characters")
        check(cache.fittedText("short", fontSize: 12, maxWidth: 200) == "short", "A fitting label must retain its original text")
        check(cache.fittedText(text, fontSize: 12, maxWidth: 0.1).isEmpty, "An impossibly narrow region must avoid clipped character fragments")
    }

    func drawingAndSkinColors() {
        let cache = OrbitTextLayoutCache()
        for arc in [false, true] {
            let red = bitmap(), green = bitmap()
            let first = cache.layout("目标-Orbit👩🏽‍💻", fontSize: 17, mode: arc ? .arc : .flat)!
            let creations = cache.creationCount
            func draw(_ context: CGContext, _ color: NSColor) -> Bool {
                if arc {
                    return cache.drawArc("目标-Orbit👩🏽‍💻", context: context, center: CGPoint(x: 128, y: 128),
                                         radius: 83, angle: .pi / 2, fontSize: 17, color: color)
                }
                return cache.draw("目标-Orbit👩🏽‍💻", context: context,
                                  rect: CGRect(x: 20, y: 100, width: 216, height: 40), fontSize: 17, color: color)
            }
            check(draw(red, NSColor(srgbRed: 1, green: 0, blue: 0, alpha: 1)), "Real text geometry must draw into a bitmap")
            check(draw(green, NSColor(srgbRed: 0, green: 1, blue: 0, alpha: 1)), "The same cached geometry must support a new skin color")
            let r = channels(red), g = channels(green)
            check(r.alpha > 0 && g.alpha > 0, "Both text modes must render visible pixels")
            check(r.red > r.green && g.green > g.red, "Skin changes must resolve foreground color at draw time")
            check(cache.creationCount == creations && first === cache.layout("目标-Orbit👩🏽‍💻", fontSize: 17, mode: arc ? .arc : .flat),
                  "Color changes must never enter or invalidate the font geometry cache")
        }
        for angle in [CGFloat.pi / 2, -.pi / 2, 0, .pi, .pi * 2 + 0.2] {
            check(cache.drawArc("方向测试-123", context: bitmap(), center: CGPoint(x: 128, y: 128),
                                radius: 90, angle: angle, fontSize: 12, color: .white),
                  "Arc labels must render safely above, below, and across wrapped angles")
        }
        let context = bitmap()
        for radius in [CGFloat.nan, .infinity, -1, 0] {
            check(!cache.drawArc("invalid", context: context, center: CGPoint(x: 128, y: 128),
                                 radius: radius, angle: 0, fontSize: 12, color: .white), "Invalid arc geometry must be rejected")
        }
        for rect in [CGRect.zero, CGRect(x: 0, y: 0, width: -1, height: 10),
                     CGRect(x: CGFloat.nan, y: 0, width: 10, height: 10),
                     CGRect(x: 0, y: 0, width: CGFloat.infinity, height: 10)] {
            check(!cache.draw("invalid", context: context, rect: rect, fontSize: 12, color: .white),
                  "Invalid flat geometry must be rejected")
        }
    }

    func exceptionsRecoverAndRestoreContext() {
        let cache = OrbitTextLayoutCache()
        let diagnostics = AOTextTestDiagnosticCount()
        AOTextTestInjectFault(.layout)
        let failed = cache.layout("synthetic-failed-layout", fontSize: 12, mode: .flat)!
        check(!failed.isUsable && failed.width.isFinite && failed.height.isFinite, "A layout exception must return a finite safe object")
        check(failed === cache.layout("synthetic-failed-layout", fontSize: 12, mode: .flat),
              "Failed native layouts must be cached to avoid a per-frame exception loop")
        check(cache.creationCount == 1 && AOTextTestDiagnosticCount() == diagnostics + 1,
              "A repeated failed label must log once and reuse its placeholder")
        let placeholderStart = AOTextTestPlaceholderCount()
        check(!cache.draw("synthetic-failed-layout", context: bitmap(), rect: CGRect(x: 10, y: 10, width: 160, height: 20),
                          fontSize: 12, color: .white), "A failed layout must draw a safe placeholder and report failure")
        check(AOTextTestPlaceholderCount() == placeholderStart + 1, "Failed layout rendering must use its geometric fallback")
        check(cache.layout("synthetic-failed-layout", fontSize: 13, mode: .flat)?.isUsable == true,
              "A later independent layout must recover after a caught exception")

        for fault: AOTextTestFault in [.arcDraw, .flatDraw, .color] {
            for arc in [false, true] where fault == .color || (arc ? fault == .arcDraw : fault == .flatDraw) {
                let context = bitmap()
                context.translateBy(x: 4, y: 7); context.scaleBy(x: 0.9, y: 1.1)
                context.textMatrix = CGAffineTransform(a: 1.1, b: 0.2, c: 0.1, d: 0.8, tx: 2, ty: 3)
                context.textPosition = CGPoint(x: 29, y: 31)
                let transform = context.ctm, textMatrix = context.textMatrix, textPosition = context.textPosition
                let beforeDiagnostic = AOTextTestDiagnosticCount(), beforePlaceholder = AOTextTestPlaceholderCount()
                func draw() -> Bool {
                    if arc {
                        return cache.drawArc("synthetic-recovery", context: context, center: CGPoint(x: 128, y: 128),
                                             radius: 70, angle: -.pi / 2, fontSize: 12, color: .white)
                    }
                    return cache.draw("synthetic-recovery", context: context,
                                      rect: CGRect(x: 30, y: 80, width: 190, height: 22), fontSize: 12, color: .white)
                }
                // Build geometry before injecting a draw-stage exception.
                _ = cache.layout("synthetic-recovery", fontSize: 12, mode: arc ? .arc : .flat)
                AOTextTestInjectFault(fault)
                check(!draw(), "A controlled draw or color exception must not escape into Swift")
                check(AOTextTestDiagnosticCount() == beforeDiagnostic + 1 && AOTextTestPlaceholderCount() == beforePlaceholder + 1,
                      "Each caught draw failure must emit one diagnostic and one safe fallback")
                check(context.ctm == transform && context.textMatrix == textMatrix && context.textPosition == textPosition,
                      "Failed text drawing must restore both graphics and text state")
                check(draw(), "A normal draw immediately after an exception must succeed")
                check(context.ctm == transform && context.textMatrix == textMatrix && context.textPosition == textPosition,
                      "Successful text drawing must also restore graphics and text state")
            }
        }
    }
}

@main @MainActor
struct TextTests {
    static func main() {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let suite = TextTestSuite()
        suite.unicodeAndInputLimits()
        suite.cacheAndTruncation()
        suite.drawingAndSkinColors()
        suite.exceptionsRecoverAndRestoreContext()
        if !suite.failures.isEmpty {
            for failure in suite.failures { fputs("FAIL: " + failure + "\n", stderr) }
            exit(1)
        }
        print("Text renderer tests passed: \(suite.assertions) assertions using synthetic labels and controlled native exceptions")
    }
}
