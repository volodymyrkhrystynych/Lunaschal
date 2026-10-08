import Foundation

/// A chapter's sanitized HTML (`backend/fanfic/sanitize.py`'s allowlist) as
/// blocks a native reader can lay out: paragraphs with their italics and bold
/// kept, headings, quotes, list items and scene breaks.
///
/// The chapter's `contentText` can't be read as written: the server makes it
/// with BeautifulSoup's `get_text(' ')`, which puts a space around every tag
/// ("she said <i>no</i>." reads "she said no .") and collapses every line
/// break to one, so splitting it into paragraphs found almost none.
public struct ChapterBlock: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case paragraph
        case heading(Int)
        case listItem(String)
        case preformatted
        /// A line of only symbols, "* * *" or "~~~": drawn centred.
        case sceneBreak
        /// `<hr>`.
        case rule
    }

    public var kind: Kind
    public var runs: [ChapterRun]
    /// How many `<blockquote>`s this block sits inside.
    public var quoteDepth: Int

    public var text: String { runs.map(\.text).joined() }
}

public struct ChapterRun: Equatable, Sendable {
    public struct Style: OptionSet, Hashable, Sendable {
        public let rawValue: Int
        public init(rawValue: Int) { self.rawValue = rawValue }
        public static let bold = Style(rawValue: 1)
        public static let italic = Style(rawValue: 2)
        public static let underline = Style(rawValue: 4)
        public static let strikethrough = Style(rawValue: 8)
        public static let code = Style(rawValue: 16)
        public static let small = Style(rawValue: 32)
    }

    public var text: String
    public var style: Style
    public var link: String?
}

public enum ChapterText {
    /// The chapter's blocks: from its HTML when there is any, else one
    /// paragraph per line of its plain text.
    public static func blocks(html: String?, text: String?) -> [ChapterBlock] {
        if let html, !html.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            var parser = HTMLBlocks(html)
            let parsed = parser.parse()
            if !parsed.isEmpty { return parsed }
        }
        return (text ?? "").components(separatedBy: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .map { ChapterBlock(kind: isSceneBreak($0) ? .sceneBreak : .paragraph,
                                runs: [ChapterRun(text: $0, style: [], link: nil)], quoteDepth: 0) }
    }

    /// "***", "* * *", "~~~", "-x-", "oOo": a line with no words in it.
    static func isSceneBreak(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.count >= 3, trimmed.count <= 40 else { return false }
        if ["ooo", "xxx", "-x-", "xox"].contains(trimmed.lowercased().replacingOccurrences(of: " ", with: "")) { return true }
        return trimmed.unicodeScalars.allSatisfy { !CharacterSet.alphanumerics.contains($0) }
    }
}

/// A forgiving scanner, not a validating parser: the input was already
/// sanitized server-side, so it only has to find tags and text, and an
/// unclosed or stray tag must never lose the words around it.
private struct HTMLBlocks {
    private let bytes: [UInt8]
    private var blocks: [ChapterBlock] = []
    private var runs: [ChapterRun] = []
    private var kind: ChapterBlock.Kind = .paragraph
    private var quoteDepth = 0
    private var styles: [String: Int] = [:]
    private var links: [String?] = []
    private var lists: [(ordered: Bool, next: Int)] = []
    private var preDepth = 0
    private var breaks = 0
    private var cellsInRow = 0

    init(_ html: String) { bytes = Array(html.utf8) }

    mutating func parse() -> [ChapterBlock] {
        var index = 0
        var textStart = 0
        while index < bytes.count {
            guard bytes[index] == UInt8(ascii: "<"), let end = tagEnd(from: index) else { index += 1; continue }
            if textStart < index { addText(String(decoding: bytes[textStart..<index], as: UTF8.self)) }
            handleTag(String(decoding: bytes[(index + 1)..<end], as: UTF8.self))
            index = end + 1
            textStart = index
        }
        if textStart < bytes.count { addText(String(decoding: bytes[textStart...], as: UTF8.self)) }
        flush()
        return blocks
    }

    /// The `>` closing a tag that starts at `start`, or nil when `<` is prose.
    private func tagEnd(from start: Int) -> Int? {
        let next = start + 1
        guard next < bytes.count else { return nil }
        let first = bytes[next]
        let isLetter = (first | 0x20) >= UInt8(ascii: "a") && (first | 0x20) <= UInt8(ascii: "z")
        guard isLetter || first == UInt8(ascii: "/") || first == UInt8(ascii: "!") else { return nil }
        var quote: UInt8?
        var index = next
        while index < bytes.count {
            let byte = bytes[index]
            if let open = quote { if byte == open { quote = nil } }
            else if byte == UInt8(ascii: "\"") || byte == UInt8(ascii: "'") { quote = byte }
            else if byte == UInt8(ascii: ">") { return index }
            index += 1
        }
        return nil
    }

    private static let styleTags: [String: ChapterRun.Style] = [
        "b": .bold, "strong": .bold, "i": .italic, "em": .italic, "cite": .italic,
        "u": .underline, "ins": .underline, "s": .strikethrough, "del": .strikethrough, "strike": .strikethrough,
        "code": .code, "small": .small, "sub": .small, "sup": .small,
    ]
    private static let blockTags: Set<String> = [
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "dl", "table", "thead", "tbody",
        "tfoot", "tr", "section", "article", "header", "footer", "center",
    ]

    private mutating func handleTag(_ raw: String) {
        if raw.hasPrefix("!") { return }
        let closing = raw.hasPrefix("/")
        let body = closing ? String(raw.dropFirst()) : raw
        let name = String(body.prefix { !$0.isWhitespace && $0 != "/" && $0 != ">" }).lowercased()
        if Self.styleTags[name] != nil {
            styles[name] = max(0, styles[name, default: 0] + (closing ? -1 : 1))
            return
        }
        switch name {
        case "br":
            breaks += 1
        case "a":
            if closing { if !links.isEmpty { links.removeLast() } }
            else { links.append(attribute("href", in: body)) }
        case "hr":
            flush()
            blocks.append(ChapterBlock(kind: .rule, runs: [], quoteDepth: quoteDepth))
        case "img":
            if let alt = attribute("alt", in: body), !alt.trimmingCharacters(in: .whitespaces).isEmpty {
                append("[\(Self.decode(alt))]", extra: .italic)
            }
        case "blockquote":
            flush()
            quoteDepth = max(0, quoteDepth + (closing ? -1 : 1))
        case "pre":
            flush()
            preDepth = max(0, preDepth + (closing ? -1 : 1))
            kind = preDepth > 0 ? .preformatted : .paragraph
        case "ul", "ol":
            flush()
            if closing { if !lists.isEmpty { lists.removeLast() } }
            else { lists.append((name == "ol", Int(attribute("start", in: body) ?? "") ?? 1)) }
        case "li":
            flush()
            if !closing {
                if var list = lists.popLast() {
                    kind = .listItem(list.ordered ? "\(list.next)." : "•")
                    list.next += 1
                    lists.append(list)
                } else { kind = .listItem("•") }
            }
        case "h1", "h2", "h3", "h4", "h5", "h6":
            flush()
            if !closing { kind = .heading(Int(String(name.dropFirst())) ?? 1) }
        case "td", "th":
            if !closing {
                if cellsInRow > 0 { append("  ·  ", extra: []) }
                cellsInRow += 1
            }
        default:
            if Self.blockTags.contains(name) {
                flush()
                if name == "tr" { cellsInRow = 0 }
            }
        }
    }

    private mutating func addText(_ raw: String) {
        let decoded = Self.decode(raw)
        if preDepth > 0 {
            applyBreaks()
            append(decoded, extra: [])
            return
        }
        // HTML whitespace: any run of it is one space, and none of it is a
        // line break; only `<br>` and blocks break lines.
        var collapsed = ""
        var lastWasSpace = false
        for character in decoded {
            if character.isWhitespace && character != "\u{00A0}" {
                if !lastWasSpace { collapsed.append(" ") }
                lastWasSpace = true
            } else {
                collapsed.append(character)
                lastWasSpace = false
            }
        }
        if collapsed.trimmingCharacters(in: .whitespaces).isEmpty {
            // Whitespace between tags only counts between words.
            if !runs.isEmpty, breaks == 0, !(runs.last?.text.hasSuffix(" ") ?? true) { append(" ", extra: []) }
            return
        }
        applyBreaks()
        if runs.isEmpty || runs.last?.text.hasSuffix("\n") == true {
            collapsed = String(collapsed.drop { $0 == " " })
        } else if collapsed.hasPrefix(" "), runs.last?.text.hasSuffix(" ") == true {
            collapsed.removeFirst()
        }
        append(collapsed, extra: [])
    }

    /// XenForo writes paragraphs as lines ending in `<br>`, with an empty line
    /// (`<br><br>`) between them: one break is a new line, two a new paragraph.
    private mutating func applyBreaks() {
        defer { breaks = 0 }
        guard breaks > 0, !runs.isEmpty else { return }
        if preDepth > 0 { return append(String(repeating: "\n", count: breaks), extra: []) }
        if breaks >= 2 { return flush() }
        trimTrailingSpace()
        append("\n", extra: [])
    }

    private mutating func append(_ text: String, extra: ChapterRun.Style) {
        guard !text.isEmpty else { return }
        var style = extra
        for (tag, depth) in styles where depth > 0 { style.insert(Self.styleTags[tag]!) }
        let link = links.last ?? nil
        if var last = runs.last, last.style == style, last.link == link {
            last.text += text
            runs[runs.count - 1] = last
        } else {
            runs.append(ChapterRun(text: text, style: style, link: link))
        }
    }

    private mutating func trimTrailingSpace() {
        while var last = runs.last {
            let trimmed = String(last.text.reversed().drop { $0 == " " }.reversed())
            if trimmed.isEmpty { runs.removeLast(); continue }
            last.text = trimmed
            runs[runs.count - 1] = last
            return
        }
    }

    private mutating func flush() {
        breaks = 0
        trimTrailingSpace()
        while let last = runs.last, last.text.hasSuffix("\n") {
            runs[runs.count - 1].text.removeLast()
            if runs[runs.count - 1].text.isEmpty { runs.removeLast() }
            trimTrailingSpace()
        }
        defer {
            runs = []
            if preDepth == 0 { kind = .paragraph }
        }
        guard runs.contains(where: { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else { return }
        var block = ChapterBlock(kind: kind, runs: runs, quoteDepth: quoteDepth)
        if block.kind == .paragraph, ChapterText.isSceneBreak(block.text) { block.kind = .sceneBreak }
        blocks.append(block)
    }

    private func attribute(_ name: String, in tag: String) -> String? {
        let pattern = "\\b\(name)\\s*=\\s*(\"([^\"]*)\"|'([^']*)'|([^\\s>]+))"
        guard let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive),
              let match = regex.firstMatch(in: tag, range: NSRange(tag.startIndex..., in: tag)) else { return nil }
        for group in 2...4 {
            if let range = Range(match.range(at: group), in: tag) { return String(tag[range]) }
        }
        return nil
    }

    private static let entities: [String: String] = [
        "amp": "&", "lt": "<", "gt": ">", "quot": "\"", "apos": "'", "nbsp": "\u{00A0}",
        "mdash": "—", "ndash": "–", "hellip": "…", "lsquo": "‘", "rsquo": "’", "ldquo": "“", "rdquo": "”",
        "copy": "©", "reg": "®", "trade": "™", "deg": "°", "middot": "·", "bull": "•", "times": "×",
        "shy": "",
    ]

    static func decode(_ text: String) -> String {
        guard text.contains("&") else { return text }
        var out = ""
        var rest = text[...]
        while let amp = rest.firstIndex(of: "&") {
            out += rest[..<amp]
            let after = rest[rest.index(after: amp)...]
            if let semi = after.prefix(12).firstIndex(of: ";") {
                let name = String(after[..<semi])
                var replacement: String?
                if name.hasPrefix("#x") || name.hasPrefix("#X") {
                    replacement = UInt32(name.dropFirst(2), radix: 16).flatMap(Unicode.Scalar.init).map { String($0) }
                } else if name.hasPrefix("#") {
                    replacement = UInt32(name.dropFirst()).flatMap(Unicode.Scalar.init).map { String($0) }
                } else {
                    replacement = entities[name]
                }
                if let replacement {
                    out += replacement
                    rest = after[after.index(after: semi)...]
                    continue
                }
            }
            out += "&"
            rest = after
        }
        return out + rest
    }
}
