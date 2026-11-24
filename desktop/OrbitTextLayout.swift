import AppKit

/// The cache owns only font-based geometry. Skin and state colors are supplied
/// at drawing time so changing either never reuses a stale text color.
final class OrbitTextLayoutCache {
    enum Mode: Hashable { case flat, arc }
    private struct Key: Hashable {
        var text: String
        var fontSize: CGFloat
        var mode: Mode
    }
    let capacity: Int
    static let maximumTextLength = 512
    static let maximumCharacters = 256
    private var entries: [Key: AOTextLayout] = [:]
    private var accessOrder: [Key] = []
    private(set) var creationCount = 0
    var count: Int { entries.count }

    init(capacity: Int = 256) { self.capacity = min(512, max(1, capacity)) }

    func removeAll() { entries.removeAll(); accessOrder.removeAll() }

    func layout(_ text: String, fontSize: CGFloat, mode: Mode) -> AOTextLayout? {
        guard Self.validFontSize(fontSize), text.utf16.prefix(Self.maximumTextLength + 1).count <= Self.maximumTextLength,
              text.count <= Self.maximumCharacters else { return nil }
        let key = Key(text: text, fontSize: fontSize, mode: mode)
        if let found = entries[key] {
            touch(key)
            return found
        }
        creationCount += 1
        let result = AOTextLayout.make(text: text, fontSize: fontSize)
        while entries.count >= capacity, let oldest = accessOrder.first {
            entries.removeValue(forKey: oldest); accessOrder.removeFirst()
        }
        // Failed layouts are immutable placeholder objects too. Cache them so
        // a bad label cannot repeatedly enter the native font path each frame.
        entries[key] = result
        accessOrder.append(key)
        return result
    }

    private func touch(_ key: Key) {
        if let index = accessOrder.firstIndex(of: key) { accessOrder.remove(at: index) }
        accessOrder.append(key)
    }

    static func validFontSize(_ size: CGFloat) -> Bool { size.isFinite && size >= 0.5 && size <= 256 }
    static func validRect(_ rect: NSRect) -> Bool {
        [rect.minX, rect.minY, rect.width, rect.height].allSatisfy { $0.isFinite && abs($0) <= 1_000_000 } &&
            rect.width > 0 && rect.height > 0
    }
    static func validArc(center: NSPoint, radius: CGFloat, angle: CGFloat) -> Bool {
        [center.x, center.y, radius, angle].allSatisfy { $0.isFinite && abs($0) <= 1_000_000 } && radius > 0
    }

    func width(_ text: String, fontSize: CGFloat, mode: Mode = .flat) -> CGFloat {
        if let layout = layout(text, fontSize: fontSize, mode: mode) {
            let width = mode == .arc ? layout.arcWidth : layout.width
            if width.isFinite, width >= 0 { return width }
        }
        guard Self.validFontSize(fontSize) else { return 0 }
        let boundedLength = min(text.utf16.prefix(Self.maximumTextLength + 1).count, Self.maximumCharacters)
        return CGFloat(boundedLength) * (fontSize * 0.65 + (mode == .arc ? 0.25 : 0))
    }

    /// Preserve both ends of a long counter or label without AppKit text
    /// measurement. Grapheme boundaries keep emoji and combining marks intact.
    func fittedText(_ text: String, fontSize: CGFloat, maxWidth: CGFloat) -> String {
        guard maxWidth.isFinite, maxWidth > 0, Self.validFontSize(fontSize),
              let original = layout(text, fontSize: fontSize, mode: .flat), original.isUsable else { return text }
        guard original.width > maxWidth else { return text }
        let ellipsis = "…"
        guard width(ellipsis, fontSize: fontSize) <= maxWidth else { return "" }
        let characters = Array(text)
        var low = 0, high = max(0, characters.count - 1)
        var best = ellipsis
        while low <= high {
            let kept = (low + high) / 2
            let candidate = String(characters.prefix((kept + 1) / 2)) + ellipsis + String(characters.suffix(kept / 2))
            if width(candidate, fontSize: fontSize) <= maxWidth {
                best = candidate; low = kept + 1
            } else { high = kept - 1 }
        }
        return best
    }

    @discardableResult
    func drawArc(_ text: String, context: CGContext, center: NSPoint, radius: CGFloat,
                 angle: CGFloat, fontSize: CGFloat, color: NSColor) -> Bool {
        guard Self.validArc(center: center, radius: radius, angle: angle), Self.validFontSize(fontSize) else { return false }
        guard let layout = layout(text, fontSize: fontSize, mode: .arc) else {
            placeholder(in: context, rect: NSRect(x: center.x - 3, y: center.y + radius - 1, width: 6, height: 2))
            return false
        }
        return layout.drawArc(in: context, center: center, radius: radius, angle: angle, color: color)
    }

    @discardableResult
    func draw(_ text: String, context: CGContext, rect: NSRect, fontSize: CGFloat,
              color: NSColor, centered: Bool = true) -> Bool {
        guard Self.validRect(rect), Self.validFontSize(fontSize) else { return false }
        let displayed = fittedText(text, fontSize: fontSize, maxWidth: rect.width)
        guard let layout = layout(displayed, fontSize: fontSize, mode: .flat) else {
            placeholder(in: context, rect: rect); return false
        }
        return layout.draw(in: context, rect: rect, color: color, centered: centered)
    }

    private func placeholder(in context: CGContext, rect: NSRect) {
        guard Self.validRect(rect) else { return }
        context.saveGState(); defer { context.restoreGState() }
        context.clip(to: rect)
        context.setStrokeColor(gray: 0.5, alpha: 0.75)
        context.setLineWidth(0.7)
        let half = min(3, rect.width / 3)
        context.move(to: CGPoint(x: rect.midX - half, y: rect.midY))
        context.addLine(to: CGPoint(x: rect.midX + half, y: rect.midY))
        context.strokePath()
    }
}
