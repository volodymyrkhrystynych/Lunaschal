import Foundation

/// What the notebook editor did with the ink, for reading on the iPad itself:
/// page turns, where each page's ink came from (the canvas on screen, a page's
/// canvas reporting after it was turned away from, or only the read before a
/// save), and how long saves took. A Pencil-only fault can't be reproduced in
/// the simulator; this is how one is seen on the device.
///
/// Only low-frequency events are written — never one line per stroke, which
/// would be a file write per stroke. Kept to the last `limit` lines, in a file
/// beside the notebooks, so it survives the app being closed.
public final class NotebookLog {
    public static let limit = 400

    public private(set) var lines: [String]
    private let url: URL
    private let now: () -> Date
    private let format: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "MM-dd HH:mm:ss.SSS"
        return format
    }()

    public init(url: URL, now: @escaping () -> Date = Date.init) {
        self.url = url
        self.now = now
        let text = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        lines = text.split(separator: "\n").map(String.init).suffix(Self.limit).map { $0 }
    }

    public var text: String { lines.joined(separator: "\n") }

    public func record(_ event: String) {
        lines.append("\(format.string(from: now())) \(event)")
        if lines.count > Self.limit { lines.removeFirst(lines.count - Self.limit) }
        // A log that can't be written is not worth failing anything over.
        try? Data((text + "\n").utf8).write(to: url, options: .atomic)
    }

    public func clear() {
        lines = []
        try? FileManager.default.removeItem(at: url)
    }

    /// "p1×3 p4×1": how many times each page (counted from zero) did something.
    public static func pages(_ counts: [Int: Int]) -> String {
        counts.keys.sorted().map { "p\($0 + 1)×\(counts[$0]!)" }.joined(separator: " ")
    }
}
