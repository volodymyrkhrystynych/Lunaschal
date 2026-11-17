import Foundation

/// Who produced a piece of text. Typed by the user (blue), speech-to-text of
/// their audio (teal), or AI-written (the normal text colour). The split is
/// what says what a better model could regenerate: typed text never, speech
/// from its recording, AI text from its source. See `backend/authorship.py`.
public enum TextAuthor: String, Hashable, Sendable {
    case user = "u"
    case speech = "s"
    case ai = "a"
}

/// Text cut into runs by author, from the spans the server resolves in
/// `backend/authorship.py` and ships on each record as `authorship.<field>`.
///
/// Spans are `[["a", 40], ["u", 3], ["s", 9, "<recording id>"]]` measured in **Unicode scalars** (code
/// points) — the unit Python, SQLite and `Array.from` count in. Not
/// `Character`s: an emoji with a skin tone is one Character and two scalars,
/// and counting Characters would shift every colour after it.
/// `backend/tests/fixtures/text_authorship.json` pins this for all three clients.
public struct AuthoredText: Hashable, Sendable {
    public struct Run: Hashable, Sendable {
        public let author: TextAuthor
        public let text: String
        /// The recording a speech-to-text run was transcribed from, when kept.
        public let source: String?

        public init(author: TextAuthor, text: String, source: String? = nil) {
            self.author = author
            self.text = text
            self.source = source
        }
    }

    public let runs: [Run]

    public init(runs: [Run]) { self.runs = runs }

    /// One author for the whole text.
    public init(_ text: String, author: TextAuthor) {
        runs = text.isEmpty ? [] : [Run(author: author, text: text)]
    }

    /// Spans that are missing, malformed or don't cover exactly `text` give
    /// the whole text to `fallback`, never a partial colouring.
    public init(_ text: String, spans: JSONValue?, fallback: TextAuthor) {
        let scalars = Array(text.unicodeScalars)
        guard let spans = Self.parse(spans), spans.reduce(0, { $0 + $1.1 }) == scalars.count else {
            self.init(text, author: fallback)
            return
        }
        var out: [Run] = []
        var start = 0
        for (author, length, source) in spans where length > 0 {
            var view = String.UnicodeScalarView()
            view.append(contentsOf: scalars[start..<start + length])
            start += length
            if let last = out.last, last.author == author, last.source == source {
                out[out.count - 1] = Run(author: author, text: last.text + String(view), source: source)
            } else {
                out.append(Run(author: author, text: String(view), source: source))
            }
        }
        self.init(runs: out)
    }

    /// A field of an API row: `authorship[key]` read against `text`. Spans
    /// that no longer fit mean the text was edited here and not yet saved
    /// back, so it is the user's; with no spans at all (an older server)
    /// `fallback` decides. Nil when the text is empty.
    public static func field(_ text: String?, _ authorship: [String: JSONValue]?, _ key: String,
                             fallback: TextAuthor) -> AuthoredText? {
        guard let text, !text.isEmpty else { return nil }
        let spans = authorship?[key]
        return AuthoredText(text, spans: spans, fallback: spans == nil ? fallback : .user)
    }

    public var text: String { runs.map(\.text).joined() }
    public var isEmpty: Bool { runs.allSatisfy { $0.text.isEmpty } }

    /// One author for every scalar of `text`, in order — what `AuthorAligner`
    /// walks to colour text that was rendered from this one.
    public var scalarAuthors: [TextAuthor] {
        runs.flatMap { run in Array(repeating: run.author, count: run.text.unicodeScalars.count) }
    }

    /// The author of the whole text when there is only one, else nil.
    public var soleAuthor: TextAuthor? {
        let authors = Set(runs.map(\.author))
        return authors.count == 1 ? authors.first : nil
    }

    /// As the record types trim their fields for display: leading and trailing
    /// whitespace removed, the authors of what remains untouched.
    public func trimmed() -> AuthoredText {
        var out = runs
        while let first = out.first {
            let kept = String(first.text.drop(while: \.isWhitespace))
            if kept.isEmpty { out.removeFirst() } else { out[0] = Run(author: first.author, text: kept, source: first.source); break }
        }
        while let last = out.last {
            let kept = String(last.text.reversed().drop(while: \.isWhitespace).reversed())
            if kept.isEmpty { out.removeLast() } else { out[out.count - 1] = Run(author: last.author, text: kept, source: last.source); break }
        }
        return AuthoredText(runs: out)
    }

    private static func parse(_ value: JSONValue?) -> [(TextAuthor, Int, String?)]? {
        guard let items = value?.array else { return nil }
        var out: [(TextAuthor, Int, String?)] = []
        for item in items {
            guard let span = item.array, span.count == 2 || span.count == 3,
                  let author = span[0].string.flatMap(TextAuthor.init(rawValue:)),
                  let length = span[1].int, length >= 0 else { return nil }
            var source: String?
            if span.count == 3 {
                guard let named = span[2].string else { return nil }
                source = named
            }
            out.append((author, length, source))
        }
        return out
    }
}

/// Gives each character of rendered text the author of the source character
/// it came from.
///
/// Rendering Markdown only removes syntax (`**`, `#`, a link's URL) or swaps a
/// character for itself, never reorders, so what is displayed is an in-order
/// subsequence of the source. Walking the source forward and taking the next
/// matching scalar recovers where each displayed scalar came from — across
/// blocks too, as long as they are fed in source order. A displayed scalar
/// with no match ahead (a list bullet, a smart quote) keeps the last author.
public final class AuthorAligner {
    private let source: [Unicode.Scalar]
    private let authors: [TextAuthor]
    private var cursor = 0
    private var last: TextAuthor

    public init(_ text: AuthoredText) {
        source = Array(text.text.unicodeScalars)
        authors = text.scalarAuthors
        last = authors.first ?? .ai
    }

    /// One author per scalar of `rendered`, advancing through the source.
    public func authors(for rendered: String) -> [TextAuthor] {
        // A piece that is only whitespace is usually the renderer's own line
        // break between list items or rows; matching it would jump the cursor
        // to the next newline in the source, past a whole item.
        if rendered.unicodeScalars.allSatisfy(\.properties.isWhitespace) {
            return Array(repeating: last, count: rendered.unicodeScalars.count)
        }
        return rendered.unicodeScalars.map { scalar in
            var k = cursor
            while k < source.count, source[k] != scalar { k += 1 }
            if k < source.count {
                cursor = k + 1
                last = authors[k]
            }
            return last
        }
    }
}

public extension SyncChange {
    /// A replicated text field cut by author, or nil when the field is empty.
    ///
    /// The server always sends spans for the fields it tracks. When they no
    /// longer fit the text, a local edit is waiting to sync, so the whole field
    /// is the user's until the server merges it; with no spans at all (an older
    /// server) `fallback` decides.
    func authored(_ field: String, fallback: TextAuthor) -> AuthoredText? {
        AuthoredText.field(data?[field]?.string, data?["authorship"]?.object, field, fallback: fallback)
    }

    /// What a journal entry's body falls back to with no spans: polished (the
    /// model's) when there is a transcript and the text is no longer it.
    var journalContentFallback: TextAuthor {
        let raw = data?["rawContent"]?.string ?? ""
        return !raw.isEmpty && raw != data?["content"]?.string ? .ai : .user
    }
}
