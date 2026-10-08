import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Importing a fic by its link, from the share sheet or typed into Settings.
// The server does the importing (`POST /api/fanfic/import`); the phone only
// decides whether a link is one the server can take, and keeps it until the
// server can be reached.

/// A link to a fic on one of the sites the server imports from. The hosts
/// and paths mirror `backend/fanfic/xenforo.py`'s `KNOWN_SITES` and
/// `backend/fanfic/sites.py`'s `parse_work_url`, so the share sheet can say
/// "not a fic link" at once instead of after a round trip.
public struct FicImportLink: Equatable, Codable {
    public let url: URL
    /// The site's name as the library's provider pills show it.
    public let site: String

    public static let forums = [
        "forums.spacebattles.com": "SpaceBattles",
        "forums.sufficientvelocity.com": "Sufficient Velocity",
        "forum.questionablequesting.com": "Questionable Questing",
    ]

    /// Every site, by name, for "supported sites are …".
    public static let siteNames = ["SpaceBattles", "Sufficient Velocity", "Questionable Questing",
                                   "FanFiction.net", "AO3", "Patreon"]

    /// The first supported link in `text`: a shared URL, or a page title
    /// with its link after it, as some apps share.
    public static func find(in text: String) -> FicImportLink? {
        let tokens = text.split(whereSeparator: { $0.isWhitespace || $0 == "<" || $0 == ">" || $0 == "\"" })
        for token in tokens where token.lowercased().hasPrefix("http") {
            if let url = URL(string: String(token)), let link = FicImportLink(url) { return link }
        }
        return nil
    }

    public init?(_ url: URL) {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              var host = url.host?.lowercased() else { return nil }
        if host.hasPrefix("www.") { host.removeFirst(4) }
        if host == "m.fanfiction.net" { host = "fanfiction.net" }
        let path = url.path
        func matches(_ pattern: String) -> Bool { path.range(of: pattern, options: .regularExpression) != nil }
        if let forum = Self.forums[host] {
            // A thread, or a post/goto link the server follows to its thread.
            guard matches(#"^/(threads|posts|goto)/"#) else { return nil }
            site = forum
        } else if host == "fanfiction.net", matches(#"^/s/\d+(/|$)"#) {
            site = "FanFiction.net"
        } else if host == "archiveofourown.org", matches(#"^/works/\d+(/|$)"#) {
            site = "AO3"
        } else if host == "patreon.com", matches(#"^/posts/([^/]*-)?\d+/?$"#) {
            site = "Patreon"
        } else {
            return nil
        }
        self.url = url
    }
}

/// What `POST /api/fanfic/import` answered: a new fic, one already in the
/// library, or a broken earlier import started again.
public struct FicImportReply: Decodable, Equatable {
    public let id: String
    public let alreadyExists: Bool?
    public let restarted: Bool?

    public init(id: String, alreadyExists: Bool? = nil, restarted: Bool? = nil) {
        self.id = id; self.alreadyExists = alreadyExists; self.restarted = restarted
    }

    public func summary(site: String) -> String {
        if restarted == true { return "The earlier \(site) import didn’t finish, so the server is trying it again." }
        if alreadyExists == true { return "That \(site) fic is already in your library." }
        return "Importing from \(site) on the server. It appears in the library with the next sync."
    }
}

public protocol FicImportTransport {
    func importFic(_ url: URL) async throws -> FicImportReply
}

extension JournalAPI: FicImportTransport {
    public func importFic(_ url: URL) async throws -> FicImportReply {
        try JSONDecoder().decode(FicImportReply.self,
                                 from: await postFanfic("api/fanfic/import", ["url": url.absoluteString]))
    }
}

/// A link shared while the server couldn't be reached.
public struct PendingFicImport: Codable, Equatable, Identifiable {
    public let id: String
    public let link: FicImportLink
    public let createdAt: Date

    public init(id: String = ULID.make(), link: FicImportLink, createdAt: Date = Date()) {
        self.id = id; self.link = link; self.createdAt = createdAt
    }
}

/// Links waiting for the server. The share extension writes here and the
/// app sends them on its next sync, so the folder lives in the App Group
/// they share. A second share of the same link while the first still waits
/// is the same request, and is kept once.
public final class FicImportOutbox {
    public let root: URL
    private var file: URL { root.appendingPathComponent("fic-imports.json") }

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [PendingFicImport] {
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        return try JSONDecoder().decode([PendingFicImport].self, from: Data(contentsOf: file))
    }

    @discardableResult
    public func append(_ link: FicImportLink, now: Date = Date()) throws -> PendingFicImport {
        let pending = try list()
        if let existing = pending.first(where: { $0.link.url == link.url }) { return existing }
        let item = PendingFicImport(link: link, createdAt: now)
        try write(pending + [item])
        return item
    }

    public func remove(_ item: PendingFicImport) throws {
        try write(try list().filter { $0.id != item.id })
    }

    private func write(_ items: [PendingFicImport]) throws {
        try JSONEncoder().encode(items).write(to: file, options: .atomic)
    }
}

/// Sends what the outbox holds, oldest first. A refusal (an unsupported or
/// vanished thread) is reported and dropped, since sending it again would get
/// the same answer; anything else — offline, a server error — stops the pass
/// and keeps the rest for the next one.
public struct FicImportSync {
    public let outbox: FicImportOutbox

    public init(outbox: FicImportOutbox) { self.outbox = outbox }

    public struct Outcome: Equatable {
        public var imported: [String] = []
        public var refused: [String] = []
    }

    public func run(using transport: FicImportTransport) async throws -> Outcome {
        var outcome = Outcome()
        for item in try outbox.list() {
            try Task.checkCancellation()
            do {
                let reply = try await transport.importFic(item.link.url)
                outcome.imported.append(reply.summary(site: item.link.site))
            } catch let failure as FicServerFailure where (400..<500).contains(failure.status)
                        && ![401, 403, 408, 429].contains(failure.status) {
                outcome.refused.append("\(item.link.url.absoluteString): \(failure.localizedDescription)")
            }
            try outbox.remove(item)
        }
        return outcome
    }
}

extension URLError {
    /// The server couldn't be reached at all, as opposed to refusing: what a
    /// shared link should wait out rather than report.
    public var isUnreachable: Bool {
        [.notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost, .cannotFindHost,
         .timedOut, .dnsLookupFailed, .internationalRoamingOff, .dataNotAllowed,
         .secureConnectionFailed].contains(code)
    }
}
