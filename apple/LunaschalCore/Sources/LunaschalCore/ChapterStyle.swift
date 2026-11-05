import Foundation

/// What a forum post's BBCode leaves in a chapter's inline `style`, as kept by
/// the server's sanitizer (`backend/fanfic/sanitize.py`'s `_STYLE_PROPERTIES`):
/// [COLOR], [FONT], [SIZE], [CENTER] and highlights. Stories lean on them —
/// a red voice, a monospaced system message — so they are part of the text.
public struct ChapterColor: Equatable, Hashable, Sendable {
    public var red: Double, green: Double, blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red; self.green = green; self.blue = blue
    }

    /// `#rgb`, `#rrggbb`, `rgb()`/`rgba()` and the common names; nil for
    /// anything else, and for a fully transparent `rgba`.
    public static func parse(_ raw: String) -> ChapterColor? {
        let value = raw.trimmingCharacters(in: .whitespaces).lowercased()
        if value.hasPrefix("#") {
            var hex = String(value.dropFirst())
            if hex.count == 3 { hex = hex.map { "\($0)\($0)" }.joined() }
            guard hex.count == 6, let number = UInt32(hex, radix: 16) else { return nil }
            return ChapterColor(red: Double((number >> 16) & 0xFF) / 255, green: Double((number >> 8) & 0xFF) / 255,
                                blue: Double(number & 0xFF) / 255)
        }
        if value.hasPrefix("rgb"), let open = value.firstIndex(of: "("), let close = value.lastIndex(of: ")") {
            let parts = value[value.index(after: open)..<close].split(whereSeparator: { $0 == "," || $0 == " " || $0 == "/" })
                .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            guard parts.count >= 3 else { return nil }
            func channel(_ text: String) -> Double? {
                if text.hasSuffix("%") { return Double(text.dropLast()).map { $0 / 100 } }
                return Double(text).map { $0 / 255 }
            }
            if parts.count >= 4, let alpha = Double(parts[3].replacingOccurrences(of: "%", with: "")), alpha == 0 { return nil }
            guard let r = channel(parts[0]), let g = channel(parts[1]), let b = channel(parts[2]) else { return nil }
            return ChapterColor(red: min(1, max(0, r)), green: min(1, max(0, g)), blue: min(1, max(0, b)))
        }
        return named[value].flatMap { parse($0) }
    }

    private static let named: [String: String] = [
        "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
        "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "gray": "#808080",
        "grey": "#808080", "silver": "#c0c0c0", "maroon": "#800000", "navy": "#000080", "teal": "#008080",
        "olive": "#808000", "lime": "#00ff00", "aqua": "#00ffff", "cyan": "#00ffff", "fuchsia": "#ff00ff",
        "magenta": "#ff00ff", "brown": "#a52a2a", "gold": "#ffd700", "crimson": "#dc143c", "indigo": "#4b0082",
        "violet": "#ee82ee", "darkred": "#8b0000", "darkblue": "#00008b", "darkgreen": "#006400",
        "orangered": "#ff4500", "royalblue": "#4169e1", "skyblue": "#87ceeb", "deepskyblue": "#00bfff",
        "darkorange": "#ff8c00", "darkviolet": "#9400d3", "turquoise": "#40e0d0", "lightblue": "#add8e6",
    ]

    /// WCAG relative luminance.
    public var luminance: Double {
        func linear(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
        return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
    }

    public func contrast(with other: ChapterColor) -> Double {
        let (a, b) = (luminance, other.luminance)
        return (max(a, b) + 0.05) / (min(a, b) + 0.05)
    }

    /// The same hue, moved toward white (on a dark page) or black (on a light
    /// one) only as far as it takes to be read: navy chosen against the forum's
    /// white page would otherwise vanish into dark mode.
    public func readable(onDark dark: Bool, minimum: Double = 3) -> ChapterColor {
        let page = dark ? ChapterColor(red: 0, green: 0, blue: 0) : ChapterColor(red: 1, green: 1, blue: 1)
        let target = dark ? 1.0 : 0.0
        var color = self
        var step = 0
        while color.contrast(with: page) < minimum, step < 20 {
            step += 1
            let t = Double(step) / 20
            color = ChapterColor(red: red + (target - red) * t, green: green + (target - green) * t,
                                 blue: blue + (target - blue) * t)
        }
        return color
    }
}

/// A `font-family`: its first family as written, and the system design that
/// stands in when the device doesn't have it.
public struct ChapterFont: Equatable, Hashable, Sendable {
    public enum Design: Equatable, Hashable, Sendable { case serif, sansSerif, monospaced, rounded }
    public var family: String?
    public var design: Design

    public static func parse(_ raw: String) -> ChapterFont? {
        let families = raw.split(separator: ",").map {
            $0.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
        }.filter { !$0.isEmpty }
        guard let first = families.first else { return nil }
        let all = families.joined(separator: " ").lowercased()
        let design: Design
        if ["mono", "courier", "consola", "menlo", "lucida console", "typewriter"].contains(where: all.contains) {
            design = .monospaced
        } else if ["comic", "cursive", "fantasy", "handwrit", "marker", "chalk"].contains(where: all.contains) {
            design = .rounded
        } else if ["sans", "arial", "helvetica", "verdana", "tahoma", "trebuchet", "segoe", "calibri", "system-ui"]
                    .contains(where: all.contains) {
            design = .sansSerif
        } else {
            design = .serif
        }
        let generic: Set<String> = ["serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "inherit", "initial"]
        return ChapterFont(family: generic.contains(first.lowercased()) ? nil : first, design: design)
    }
}

/// How a run looks beyond `ChapterRun.Style`. Every field nil is plain text.
public struct ChapterLook: Equatable, Hashable, Sendable {
    public var color: ChapterColor?
    public var background: ChapterColor?
    public var font: ChapterFont?
    /// Relative to the reader's text size.
    public var scale: Double?

    public init(color: ChapterColor? = nil, background: ChapterColor? = nil, font: ChapterFont? = nil, scale: Double? = nil) {
        self.color = color; self.background = background; self.font = font; self.scale = scale
    }

    public var isPlain: Bool { self == ChapterLook() }

    /// `inner` over `self`: what a nested element sets wins.
    func merged(with inner: ChapterLook) -> ChapterLook {
        ChapterLook(color: inner.color ?? color, background: inner.background ?? background,
                    font: inner.font ?? font, scale: inner.scale.map { $0 * (scale ?? 1) } ?? scale)
    }
}

public enum ChapterAlignment: Equatable, Sendable { case leading, center, trailing }

/// One element's inline `style`.
struct ChapterCSS: Equatable {
    var look = ChapterLook()
    var style: ChapterRun.Style = []
    var alignment: ChapterAlignment?

    init(look: ChapterLook = ChapterLook(), style: ChapterRun.Style = [], alignment: ChapterAlignment? = nil) {
        self.look = look; self.style = style; self.alignment = alignment
    }

    init(_ declarations: String) {
        for declaration in declarations.split(separator: ";") {
            let parts = declaration.split(separator: ":", maxSplits: 1)
            guard parts.count == 2 else { continue }
            let property = parts[0].trimmingCharacters(in: .whitespaces).lowercased()
            let value = parts[1].replacingOccurrences(of: "!important", with: "").trimmingCharacters(in: .whitespaces)
            let lower = value.lowercased()
            switch property {
            case "color": look.color = ChapterColor.parse(value)
            case "background-color", "background": look.background = ChapterColor.parse(value)
            case "font-family": look.font = ChapterFont.parse(value)
            case "font-size": look.scale = Self.scale(lower)
            case "font-weight":
                if lower == "bold" || lower == "bolder" || (Int(lower) ?? 0) >= 600 { style.insert(.bold) }
            case "font-style":
                if lower == "italic" || lower == "oblique" { style.insert(.italic) }
            case "text-decoration", "text-decoration-line":
                if lower.contains("underline") { style.insert(.underline) }
                if lower.contains("line-through") { style.insert(.strikethrough) }
            case "text-align":
                switch lower {
                case "center": alignment = .center
                case "right", "end": alignment = .trailing
                case "left", "start", "justify": alignment = .leading
                default: break
                }
            default: break
            }
        }
    }

    /// XenForo's [SIZE] is 9–26px against a 15px body.
    static func scale(_ value: String) -> Double? {
        let keywords: [String: Double] = [
            "xx-small": 0.6, "x-small": 0.7, "small": 0.85, "medium": 1, "large": 1.2, "x-large": 1.5,
            "xx-large": 2, "xxx-large": 2, "smaller": 0.85, "larger": 1.2,
        ]
        var result: Double?
        if let keyword = keywords[value] { result = keyword }
        else if value.hasSuffix("px") { result = Double(value.dropLast(2)).map { $0 / 15 } }
        else if value.hasSuffix("pt") { result = Double(value.dropLast(2)).map { $0 * 4 / 3 / 15 } }
        else if value.hasSuffix("rem") { result = Double(value.dropLast(3)) }
        else if value.hasSuffix("em") { result = Double(value.dropLast(2)) }
        else if value.hasSuffix("%") { result = Double(value.dropLast()).map { $0 / 100 } }
        guard let result, result > 0 else { return nil }
        let clamped = min(2, max(0.6, result))
        return abs(clamped - 1) < 0.01 ? nil : clamped
    }
}
