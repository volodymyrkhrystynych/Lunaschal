import Foundation

public protocol LibraryTransport: ReplicaTransport {
    func mediaCollections() async throws -> [String]
    func mediaPage(collection: String, after: String) async throws -> MediaPage
    func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data
}

extension JournalAPI: LibraryTransport {}

/// All bulk database/file work runs on this actor, never on the UI actor.
/// Only this worker writes downloaded media; UI instances read atomic manifests.
public actor LibraryDownload {
    private let replicaURL: URL
    private let mediaURL: URL
    private var replica: ReplicaStore?
    private var media: MediaStore?
    var running = false

    public init(replicaURL: URL, mediaURL: URL) {
        self.replicaURL = replicaURL
        self.mediaURL = mediaURL
    }

    /// Records per page: a page of chapters is one write transaction, full
    /// HTML and search index included, and the UI's own writes wait behind it.
    static let pageSize = 25

    public nonisolated static func collections(knowledge: Bool) -> [String] {
        let base = ["fic_chapters", "messages",
                    "paper_pages", "paper_native_ink", "paper_page_images",
                    "newspaper_issues", "newspaper_frontpages"]
        return knowledge ? base + ["wiki_articles"] : base
    }

    func replicaStore() throws -> ReplicaStore {
        if let replica { return replica }
        let store = try ReplicaStore(url: replicaURL)
        replica = store
        return store
    }

    func mediaStore() throws -> MediaStore {
        if let media { return media }
        let store = try MediaStore(root: mediaURL)
        media = store
        return store
    }

    public func usedBytes() throws -> Int64 { try mediaStore().usedBytes() }

    /// The library's text: books, their chapters and everything else the
    /// library download replicates, as stored in the database.
    public func textBytes() throws -> Int64 {
        try replicaStore().storedBytes(collections: ["fics"] + Self.collections(knowledge: true))
    }

    /// Cellular-capable text updates never bootstrap or download binary media.
    /// Limit each pass so a large backlog doesn't monopolize ordinary sync.
    @discardableResult
    public func updateText(using transport: ReplicaTransport, collections: [String],
                           maxPages: Int = 5) async throws -> Bool {
        guard !running else { return false }
        running = true
        defer { running = false }
        let store = try replicaStore()
        guard try store.isBootstrapped(collections: collections) else { return false }
        for _ in 0..<max(0, maxPages) {
            try Task.checkCancellation()
            guard let cursor = try store.cursor(collections: collections) else { return false }
            do {
                let page = try await transport.syncPage(cursor: cursor, collections: collections, limit: Self.pageSize)
                try Task.checkCancellation()
                guard page.mode == "delta", Set(page.collections) == Set(collections),
                      page.epoch == (try store.epoch),
                      cursor == (try store.cursor(collections: collections)) else {
                    try store.resetCursor(collections: collections)
                    return false
                }
                try store.apply(page, startingBootstrap: false, expectedCursor: cursor)
                if !page.hasMore { return true }
            } catch let error as HTTPFailure where error.status == 410 {
                try store.resetCursor(collections: collections)
                return false
            }
        }
        return true
    }

    /// Call with a Wi-Fi-only transport. The caller owns cancellation independently
    /// of the Settings view, so switching tabs doesn't end the download.
    public func download(using transport: LibraryTransport, collections: [String],
                         mediaCollections: [String], budget: Int64,
                         progress: @escaping @Sendable (String) async -> Void) async throws -> [String] {
        while running {
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        try Task.checkCancellation()
        running = true
        defer { running = false }
        let store = try replicaStore()
        let files = try mediaStore()
        var resetOnce = false
        var records = 0
        while true {
            try Task.checkCancellation()
            let cursor = try store.cursor(collections: collections)
            do {
                let page = try await transport.syncPage(cursor: cursor, collections: collections, limit: Self.pageSize)
                try Task.checkCancellation()
                guard Set(page.collections) == Set(collections),
                      cursor == (try store.cursor(collections: collections)) else {
                    throw ReplicaError.needsBootstrap
                }
                try store.apply(page, startingBootstrap: cursor == nil, expectedCursor: cursor)
                records += page.changes.count
                await progress("Updating reading content: \(records) records saved")
                if !page.hasMore { break }
            } catch let error as HTTPFailure where error.status == 410 && !resetOnce {
                resetOnce = true
                try store.resetCursor(collections: collections)
            }
        }
        let supported = try await transport.mediaCollections()
        for collection in supported where mediaCollections.contains(collection) {
            var after = ""
            while true {
                try Task.checkCancellation()
                await progress("Checking \(collection.replacingOccurrences(of: "_", with: " "))…")
                let page = try await transport.mediaPage(collection: collection, after: after)
                for item in page.items {
                    try Task.checkCancellation()
                    try files.observe(item)
                    guard item.available else { continue }
                    if try files.reuse(item) { continue }
                    var offset = try files.offset(for: item, budget: budget)
                    guard let size = item.size else { throw MediaError.invalidManifest }
                    if size == 0 { try files.append(Data(), to: item, offset: 0) }
                    while offset < size {
                        try Task.checkCancellation()
                        await progress("Downloading \(collection.replacingOccurrences(of: "_", with: " ")): \(offset / 1024) / \(size / 1024) KB")
                        let chunk = try await transport.mediaChunk(item, offset: offset, count: min(1024 * 1024, size - offset))
                        try Task.checkCancellation()
                        guard !chunk.isEmpty else { throw MediaError.invalidRange }
                        try files.append(chunk, to: item, offset: offset)
                        offset += Int64(chunk.count)
                    }
                    await progress("Verifying downloaded file…")
                    try await files.finish(item)
                }
                if !page.hasMore { break }
                guard page.after > after else { throw MediaError.invalidManifest }
                after = page.after
            }
        }
        return supported
    }
}
