import Foundation

/// A YouTube link shared into Lunaschal from another app.
public struct SharedLink: Codable, Equatable, Identifiable {
    public let id: String
    public let url: String
    public let createdAt: Date

    public init(id: String = ULID.make(), url: String, createdAt: Date = Date()) {
        self.id = id; self.url = url; self.createdAt = createdAt
    }
}

/// YouTube links shared from the share sheet, waiting for the app. The share
/// extension cannot reach the app's own storage, so they wait in the App Group
/// folder the two share; the app moves them into the Capture composer's draft
/// the next time it opens. Nothing here needs the server.
public final class SharedLinkInbox {
    public let root: URL
    private var file: URL { root.appendingPathComponent("shared-links.json") }

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [SharedLink] {
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        return try JSONDecoder().decode([SharedLink].self, from: Data(contentsOf: file))
    }

    /// Canonicalises `url`; sharing the same video twice before the app opens
    /// keeps it once.
    @discardableResult
    public func append(_ url: String, now: Date = Date()) throws -> SharedLink {
        let canonical = try YouTubeLink.canonical(url)
        let waiting = try list()
        if let existing = waiting.first(where: { $0.url == canonical }) { return existing }
        let link = SharedLink(url: canonical, createdAt: now)
        try write(waiting + [link])
        return link
    }

    /// Drops what the app has taken, by id, so a link shared meanwhile stays.
    public func remove(_ taken: [SharedLink]) throws {
        let ids = Set(taken.map(\.id))
        try write(try list().filter { !ids.contains($0.id) })
    }

    private func write(_ links: [SharedLink]) throws {
        try JSONEncoder().encode(links).write(to: file, options: .atomic)
    }
}

/// The Capture composer's YouTube links, as it stores them: one per line.
public enum DraftLinks {
    /// `stored` with `adding` after it, each link once, in order.
    public static func merge(_ stored: String, _ adding: [String]) -> String {
        var seen = Set<String>()
        let all = stored.split(separator: "\n").map(String.init) + adding
        return all.filter { !$0.isEmpty && seen.insert($0).inserted }.joined(separator: "\n")
    }
}
