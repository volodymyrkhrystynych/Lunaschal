import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: One fic, ahead of the library download (GET /api/mobile/fics/<id>/download)

/// One page of a single fic: its chapters in position order, each at the
/// revision a bootstrap would carry, plus the sizes progress is computed from.
public struct FicDownloadPage: Decodable {
    public let epoch: String
    public let fic: SyncChange?
    public let chapters: [SyncChange]
    public let hasMore: Bool
    public let after: String
    public let totalChapters: Int
    /// Chapter text across the whole fic, and before / within this page.
    public let textBytes: Int64
    public let bytesBefore: Int64
    public let pageBytes: Int64
    /// A PDF book's file; nil for a fic made of chapters.
    public let media: MediaDescriptor?
}

public protocol FicDownloadTransport {
    func ficDownloadPage(ficID: String, after: String) async throws -> FicDownloadPage
    func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data
}

extension JournalAPI: FicDownloadTransport {
    public func ficDownloadPage(ficID: String, after: String) async throws -> FicDownloadPage {
        guard ULID.isValid(ficID) else { throw ReplicaError.invalidPage }
        var req = request("api/mobile/fics/\(ficID)/download")
        var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = after.isEmpty ? nil : [URLQueryItem(name: "after", value: after)]
        req.url = parts.url
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        return try JSONDecoder().decode(FicDownloadPage.self, from: data)
    }
}

/// How far one fic has got. `after` is the page key to resume from, so a fic
/// that was pushed back down the queue does not start over.
public struct FicDownloadProgress: Equatable, Sendable {
    public var doneBytes: Int64
    public var totalBytes: Int64
    public var after: String

    public init(doneBytes: Int64, totalBytes: Int64, after: String) {
        self.doneBytes = doneBytes
        self.totalBytes = totalBytes
        self.after = after
    }

    public var fraction: Double {
        totalBytes > 0 ? min(1, Double(doneBytes) / Double(totalBytes)) : 0
    }
}

public enum FicDownloadError: LocalizedError {
    case notOnServer
    case historyChanged

    public var errorDescription: String? {
        switch self {
        // A deleted fic and a server without this route both answer 404.
        case .notOnServer: return "The server couldn't send this fic. It may have been deleted, or the server needs updating."
        case .historyChanged: return "The server's history changed. Sync, then open this fic again."
        }
    }
}

extension LibraryDownload {
    /// Downloads one fic's chapters (or its PDF) into the replica and media
    /// store. Shares the library worker's connections and its one-at-a-time
    /// rule, so it never writes a file the bulk download is writing.
    public func downloadFic(_ ficID: String, after start: String = "", using transport: FicDownloadTransport,
                            progress: @escaping @Sendable (FicDownloadProgress) async -> Void) async throws {
        while running {
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        try Task.checkCancellation()
        running = true
        defer { running = false }
        let store = try replicaStore()
        let files = try mediaStore()
        var after = start
        var page: FicDownloadPage
        repeat {
            try Task.checkCancellation()
            do {
                page = try await transport.ficDownloadPage(ficID: ficID, after: after)
            } catch let failure as HTTPFailure where failure.status == 404 {
                throw FicDownloadError.notOnServer
            }
            try Task.checkCancellation()
            guard try store.storePrefetched((page.fic.map { [$0] } ?? []) + page.chapters, epoch: page.epoch) else {
                throw FicDownloadError.historyChanged
            }
            if page.hasMore && page.after == after { throw ReplicaError.invalidPage }
            after = page.after
            let fileBytes = page.media?.available == true ? page.media?.size ?? 0 : 0
            await progress(FicDownloadProgress(doneBytes: page.bytesBefore + page.pageBytes,
                                               totalBytes: page.textBytes + fileBytes, after: after))
        } while page.hasMore
        guard let item = page.media, item.available else { return }
        try item.validate()
        try files.observe(item)
        if try files.reuse(item) { return }
        guard let size = item.size else { throw MediaError.invalidManifest }
        // A phone's free space is the only budget for a book someone opened.
        var offset = try files.offset(for: item, budget: .max)
        if size == 0 { try files.append(Data(), to: item, offset: 0) }
        while offset < size {
            try Task.checkCancellation()
            let chunk = try await transport.mediaChunk(item, offset: offset, count: min(1024 * 1024, size - offset))
            try Task.checkCancellation()
            guard !chunk.isEmpty else { throw MediaError.invalidRange }
            try files.append(chunk, to: item, offset: offset)
            offset += Int64(chunk.count)
            await progress(FicDownloadProgress(doneBytes: page.textBytes + offset,
                                               totalBytes: page.textBytes + size, after: after))
        }
        try await files.finish(item)
    }
}

// MARK: Queue and estimate (pure, so they are tested without a network)

/// The fics waiting to download, most recently opened first: whatever was
/// opened last is what someone is looking at.
public struct FicQueue: Equatable, Sendable {
    public struct Entry: Equatable, Sendable {
        public let id: String
        public var title: String
        public var after: String
    }

    public private(set) var entries: [Entry] = []

    public init() {}

    public var head: Entry? { entries.first }
    public var isEmpty: Bool { entries.isEmpty }
    public func contains(_ id: String) -> Bool { entries.contains { $0.id == id } }

    /// Moves `id` to the front, keeping how far it had got. Returns whether
    /// the head changed — that is when the running download must yield.
    @discardableResult
    public mutating func prioritize(_ id: String, title: String) -> Bool {
        let previous = head?.id
        var entry = entries.first { $0.id == id } ?? Entry(id: id, title: title, after: "")
        entry.title = title
        entries.removeAll { $0.id == id }
        entries.insert(entry, at: 0)
        return previous != id
    }

    public mutating func record(_ id: String, after: String) {
        guard let index = entries.firstIndex(where: { $0.id == id }) else { return }
        entries[index].after = after
    }

    public mutating func remove(_ id: String) {
        entries.removeAll { $0.id == id }
    }

    public mutating func removeAll() { entries.removeAll() }
}

/// Time left from the rate seen since this download started. Resumed bytes
/// don't count toward the rate, or a half-finished fic would look instant.
public struct TransferEstimate: Equatable, Sendable {
    public let started: Date
    public private(set) var firstDone: Int64?

    public init(started: Date) { self.started = started }

    /// Seconds left, or nil until there is a rate worth quoting: a second of
    /// data, and some bytes moved.
    public mutating func secondsLeft(done: Int64, total: Int64, now: Date) -> TimeInterval? {
        guard total > 0 else { return nil }
        if done >= total { return 0 }
        guard let first = firstDone else { firstDone = done; return nil }
        let elapsed = now.timeIntervalSince(started)
        let moved = done - first
        guard elapsed >= 1, moved > 0 else { return nil }
        return Double(total - done) / (Double(moved) / elapsed)
    }

    public static func describe(_ seconds: TimeInterval?) -> String {
        guard let seconds else { return "Estimating time left…" }
        if seconds < 60 { return "Less than a minute left" }
        let minutes = Int((seconds / 60).rounded())
        if minutes < 60 { return "About \(minutes) min left" }
        let hours = minutes / 60, rest = minutes % 60
        return rest == 0 ? "About \(hours) h left" : "About \(hours) h \(rest) min left"
    }
}
