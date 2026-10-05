import Foundation

/// The block structure of a reply's Markdown: headings, list items, quotes,
/// code blocks and paragraphs. Inline styling (bold, links, `code`) is left in
/// the text for the view to render; this only says how the lines group.
public enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    /// `marker` is "•" for a bullet, or the number with its dot.
    case listItem(marker: String, indent: Int, text: String)
    case quote(String)
    case code(language: String?, text: String)
    case rule
    /// A GitHub table: the header row first, then the body rows.
    case table([[String]])

    public static func parse(_ text: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var fence: (language: String?, lines: [String])?

        func flush() {
            if let table = Self.table(paragraph) { blocks.append(table) }
            else if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))) }
            paragraph = []
        }

        for raw in text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if var open = fence {
                if line.hasPrefix("```") {
                    blocks.append(.code(language: open.language, text: open.lines.joined(separator: "\n")))
                    fence = nil
                } else {
                    open.lines.append(raw)
                    fence = open
                }
                continue
            }
            if line.hasPrefix("```") {
                flush()
                let language = line.dropFirst(3).trimmingCharacters(in: .whitespaces)
                fence = (language.isEmpty ? nil : language, [])
                continue
            }
            if line.isEmpty { flush(); continue }
            if let heading = Self.heading(line) { flush(); blocks.append(heading); continue }
            if line.count >= 3, Set(line.filter { $0 != " " }).count == 1, let first = line.first, "-*_".contains(first),
               line.filter({ $0 != " " }).count >= 3 {
                flush(); blocks.append(.rule); continue
            }
            if line.hasPrefix(">") {
                flush()
                let quoted = line.dropFirst().trimmingCharacters(in: .whitespaces)
                if case let .quote(previous)? = blocks.last {
                    blocks[blocks.count - 1] = .quote(previous + "\n" + quoted)
                } else { blocks.append(.quote(quoted)) }
                continue
            }
            let indent = (raw.prefix { $0 == " " || $0 == "\t" }.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }) / 2
            if let item = Self.listItem(line, indent: indent) { flush(); blocks.append(item); continue }
            // A line under a list item with no marker of its own continues it.
            if paragraph.isEmpty, indent > 0, case let .listItem(marker, level, text)? = blocks.last {
                blocks[blocks.count - 1] = .listItem(marker: marker, indent: level, text: text + "\n" + line)
                continue
            }
            paragraph.append(line)
        }
        if let open = fence { blocks.append(.code(language: open.language, text: open.lines.joined(separator: "\n"))) }
        flush()
        return blocks
    }

    private static func table(_ lines: [String]) -> MarkdownBlock? {
        guard lines.count >= 2, lines.allSatisfy({ $0.hasPrefix("|") }) else { return nil }
        func cells(_ line: String) -> [String] {
            var inner = Substring(line.trimmingCharacters(in: .whitespaces))
            if inner.hasPrefix("|") { inner = inner.dropFirst() }
            if inner.hasSuffix("|") { inner = inner.dropLast() }
            return inner.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
        }
        let divider = cells(lines[1])
        guard divider.allSatisfy({ !$0.isEmpty && $0.allSatisfy { ":- ".contains($0) } && $0.contains("-") }) else { return nil }
        return .table([cells(lines[0])] + lines.dropFirst(2).map(cells))
    }

    private static func heading(_ line: String) -> MarkdownBlock? {
        let hashes = line.prefix { $0 == "#" }.count
        guard (1...6).contains(hashes), line.dropFirst(hashes).first == " " else { return nil }
        return .heading(level: hashes, text: line.dropFirst(hashes).trimmingCharacters(in: .whitespaces))
    }

    private static func listItem(_ line: String, indent: Int) -> MarkdownBlock? {
        for bullet in ["- ", "* ", "+ "] where line.hasPrefix(bullet) {
            var text = String(line.dropFirst(2))
            // Task-list boxes, as GitHub renders them.
            if text.hasPrefix("[ ] ") { return .listItem(marker: "☐", indent: indent, text: String(text.dropFirst(4))) }
            if text.lowercased().hasPrefix("[x] ") { text = String(text.dropFirst(4)); return .listItem(marker: "☑", indent: indent, text: text) }
            return .listItem(marker: "•", indent: indent, text: text)
        }
        let digits = line.prefix { $0.isNumber }
        guard !digits.isEmpty, digits.count <= 3 else { return nil }
        let rest = line.dropFirst(digits.count)
        guard let mark = rest.first, mark == "." || mark == ")", rest.dropFirst().first == " " else { return nil }
        return .listItem(marker: "\(digits).", indent: indent, text: rest.dropFirst(2).trimmingCharacters(in: .whitespaces))
    }
}
