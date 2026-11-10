import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Canon: the original work a fanfic is set in, imported on the server from a
// Fandom wiki's XML dump (backend/writing/canon_import.py) and linked to books.
// Read over the network only — a dump is tens to hundreds of MB, and the phone
// asks the same project-scoped search the discussion's canon_search runs, so a
// book never shows a wiki it is not linked to.

public struct WritingCanonSource: Codable, Equatable, Identifiable {
    public let id: String
    public var name: String
    public var wikiHost: String?
    /// importing | ready | error
    public var status: String
    public var pages: Int
    public var error: String?
    public var progress: Progress?

    public struct Progress: Codable, Equatable {
        public var stage: String?
        public var pages: Int?
    }

    public init(id: String, name: String, wikiHost: String? = nil, status: String = "ready", pages: Int = 0,
                error: String? = nil) {
        self.id = id; self.name = name; self.wikiHost = wikiHost; self.status = status; self.pages = pages
        self.error = error; self.progress = nil
    }

    enum CodingKeys: String, CodingKey { case id, name, wikiHost, status, pages, error, progress }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        wikiHost = try c.decodeIfPresent(String.self, forKey: .wikiHost)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "error"
        pages = try c.decodeIfPresent(Int.self, forKey: .pages) ?? 0
        error = try c.decodeIfPresent(String.self, forKey: .error)
        progress = try c.decodeIfPresent(Progress.self, forKey: .progress)
    }

    /// What to call it: a fresh import has no name until its siteinfo is read.
    public var displayName: String {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? (wikiHost ?? "Importing wiki") : trimmed
    }

    /// One line under the name: progress, the failure, or how big it is.
    public var summary: String {
        switch status {
        case "importing":
            let pages = progress?.pages ?? 0
            return pages > 0 ? "Importing — \(pages.formatted()) pages so far" : "Importing…"
        case "ready": return "\(pages.formatted()) pages"
        default: return error ?? "Import failed"
        }
    }
}

public struct WritingCanonHit: Codable, Equatable, Identifiable, Hashable {
    /// `sourceId:chunkId`, what `writingCanonArticle` takes.
    public let id: String
    public let sourceId: String
    public let sourceName: String
    public let title: String
    public let snippet: String

    public init(id: String, sourceId: String, sourceName: String, title: String, snippet: String) {
        self.id = id; self.sourceId = sourceId; self.sourceName = sourceName; self.title = title; self.snippet = snippet
    }
}

public struct WritingCanonArticle: Codable, Equatable {
    public let id: String
    public let title: String
    public let text: String
    /// Fandom text is CC BY-SA; the line says where it came from.
    public let attribution: String

    public init(id: String, title: String, text: String, attribution: String) {
        self.id = id; self.title = title; self.text = text; self.attribution = attribution
    }
}

extension JournalAPI {
    /// Every imported wiki, linked to this book or not.
    public func writingCanonSources() async throws -> [WritingCanonSource] {
        try JSONDecoder().decode([WritingCanonSource].self, from: await get("api/writing/canon", [:]))
    }

    public func writingProjectCanon(projectID: String) async throws -> [String] {
        struct Reply: Decodable { let sourceIds: [String] }
        let data = try await get("api/writing/projects/\(try Self.pathID(projectID))/canon", [:])
        return try JSONDecoder().decode(Reply.self, from: data).sourceIds
    }

    /// Replaces the whole set of wikis linked to a book.
    public func setWritingProjectCanon(projectID: String, sourceIDs: [String]) async throws {
        var req = request("api/writing/projects/\(try Self.pathID(projectID))/canon", method: "PUT")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["sourceIds": sourceIDs])
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    public func searchWritingCanon(projectID: String, query: String, limit: Int = 20) async throws -> [WritingCanonHit] {
        try JSONDecoder().decode([WritingCanonHit].self, from: await get(
            "api/writing/projects/\(try Self.pathID(projectID))/canon/search", ["q": query, "limit": String(limit)]))
    }

    public func writingCanonArticle(projectID: String, id: String) async throws -> WritingCanonArticle {
        try JSONDecoder().decode(WritingCanonArticle.self, from: await get(
            "api/writing/projects/\(try Self.pathID(projectID))/canon/read", ["id": id]))
    }

    /// Starts importing a wiki from its URL (or a dump's); the server finds the
    /// dump and imports it in the background, so the reply is only the id.
    public func importWritingCanon(url: String) async throws -> String {
        struct Reply: Decodable { let id: String }
        return try JSONDecoder().decode(Reply.self, from: await chatJSON("api/writing/canon/fetch", ["url": url])).id
    }
}
