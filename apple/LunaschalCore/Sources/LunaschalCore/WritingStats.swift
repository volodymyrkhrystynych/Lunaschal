import Foundation

public enum WritingStats {
    /// Whitespace-separated words, the count the server and the web agree on.
    public static func words(_ text: String) -> Int {
        text.split(whereSeparator: \.isWhitespace).count
    }

    /// Net words in chapters typed here that the server hasn't counted yet.
    public static func unsyncedWords(_ documents: [WritingDocument]) -> Int {
        documents.filter { $0.kind == .chapter }.reduce(0) { total, document in
            guard let draft = document.draft?.content else { return total }
            return total + words(draft) - words(document.server.content)
        }
    }

    /// Today's words as a number to show: a day that has only cut reads as none.
    public static func wordsToday(serverWords: Int, unsynced: Int) -> Int {
        max(0, serverWords + unsynced)
    }

    /// How far through the day's goal, 0...1, or nil with no goal.
    public static func goalProgress(words: Int, goal: Int?) -> Double? {
        guard let goal, goal > 0 else { return nil }
        return min(1, Double(max(0, words)) / Double(goal))
    }
}

/// Where a spoken clip's words will land, in UTF-16 units so it agrees with
/// UITextView. It moves with the text, because the transcript may take a while
/// and nobody has to stop writing to wait for it.
public struct DictationAnchor: Codable, Equatable, Sendable {
    public var offset: Int
    public init(offset: Int) { self.offset = offset }

    /// Text at `location` lost `removed` units and gained `inserted`.
    public mutating func shift(location: Int, removed: Int, inserted: Int) {
        if location + removed <= offset {
            offset += inserted - removed
        } else if location < offset {
            // The edit replaced the text around the anchor: land after it.
            offset = location + inserted
        }
    }

    /// `text` with `spoken` put in at this anchor, with a space on whichever
    /// side would otherwise run two words together. Returns the end of the
    /// insertion too, so a second clip follows the first.
    public func inserting(_ spoken: String, into text: String) -> WritingInsertion {
        let units = Array(text.utf16)
        let at = min(max(0, offset), units.count)
        let spoken = spoken.trimmingCharacters(in: .whitespacesAndNewlines)
        func isWord(_ unit: UInt16?) -> Bool {
            guard let unit, let scalar = Unicode.Scalar(unit) else { return unit != nil }
            return !CharacterSet.whitespacesAndNewlines.contains(scalar)
        }
        let startsPunctuated = spoken.unicodeScalars.first.map(CharacterSet.punctuationCharacters.contains) ?? false
        let before = isWord(at > 0 ? units[at - 1] : nil) && !startsPunctuated ? " " : ""
        let after = isWord(at < units.count ? units[at] : nil) ? " " : ""
        let insert = before + spoken + after
        let head = String(decoding: units[..<at], as: UTF16.self)
        let tail = String(decoding: units[at...], as: UTF16.self)
        return WritingInsertion(text: head + insert + tail, location: at, inserted: insert,
                                end: at + (before + spoken).utf16.count)
    }
}

public struct WritingInsertion: Equatable, Sendable {
    public let text: String
    /// Where `inserted` went, in UTF-16 units.
    public let location: Int
    public let inserted: String
    public let end: Int
}

/// What changed between two versions of a text, in UTF-16 units. Worked out from
/// the text rather than from the keystroke, because undo and redo change a text
/// view without ever asking it first.
public struct WritingTextEdit: Equatable, Sendable {
    public let location: Int
    public let removed: Int
    public let inserted: Int

    public init(location: Int, removed: Int, inserted: Int) {
        self.location = location; self.removed = removed; self.inserted = inserted
    }

    public static func between(_ old: String, _ new: String) -> WritingTextEdit {
        let before = Array(old.utf16), after = Array(new.utf16)
        var start = 0
        while start < before.count, start < after.count, before[start] == after[start] { start += 1 }
        var oldEnd = before.count, newEnd = after.count
        while oldEnd > start, newEnd > start, before[oldEnd - 1] == after[newEnd - 1] {
            oldEnd -= 1
            newEnd -= 1
        }
        return WritingTextEdit(location: start, removed: oldEnd - start, inserted: newEnd - start)
    }
}
