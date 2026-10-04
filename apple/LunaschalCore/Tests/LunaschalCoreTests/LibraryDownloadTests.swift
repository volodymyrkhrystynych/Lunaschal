import XCTest
@testable import LunaschalCore

private actor LibraryFixture: LibraryTransport {
    var pages: [SyncPage]
    var cursors: [String?] = []
    var expired = false
    var delay = false
    var mediaCalls = 0

    init(_ pages: [SyncPage] = [], expired: Bool = false, delay: Bool = false) {
        self.pages = pages; self.expired = expired; self.delay = delay
    }
    func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage {
        cursors.append(cursor)
        if delay { try await Task.sleep(nanoseconds: 60_000_000_000) }
        if expired { throw HTTPFailure(status: 410) }
        guard !pages.isEmpty else { throw ReplicaError.invalidPage }
        return pages.removeFirst()
    }
    func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply {
        XCTFail("Library downloads must not submit journal edits")
        throw ReplicaError.invalidPage
    }
    func mediaCollections() async throws -> [String] { mediaCalls += 1; return [] }
    func mediaPage(collection: String, after: String) async throws -> MediaPage {
        XCTFail("Text updates must not fetch media")
        throw MediaError.invalidManifest
    }
    func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data {
        XCTFail("Text updates must not fetch media")
        throw MediaError.invalidManifest
    }
}

@MainActor
final class LibraryDownloadTests: XCTestCase {
    private let collections = LibraryDownload.collections(knowledge: false)
    private let epoch = ULID.make()
    private let chapter = ULID.make()

    private func setup() throws -> (ReplicaStore, LibraryDownload) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = root.appendingPathComponent("replica.sqlite")
        let store = try ReplicaStore(url: url)
        let worker = LibraryDownload(replicaURL: url, mediaURL: root.appendingPathComponent("media"))
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return (store, worker)
    }

    private func page(_ cursor: String, mode: String = "bootstrap", more: Bool = false,
                      revision: Int64 = 1, content: String = "Original") -> SyncPage {
        SyncPage(protocolVersion: 1, epoch: epoch, mode: mode, changes: [
            SyncChange(revision: revision, collection: "fic_chapters", id: chapter, deleted: false,
                       data: ["id": .string(chapter), "title": .string("Chapter"), "content": .string(content)])
        ], hasMore: more, cursor: cursor, collections: collections)
    }

    func testCellularUpdateDoesNotStartOrResumeBootstrap() async throws {
        let (store, worker) = try setup()
        let api = LibraryFixture()
        let cold = try await worker.updateText(using: api, collections: collections)
        XCTAssertFalse(cold)
        try store.apply(page("partial", more: true), startingBootstrap: true)
        let partial = try await worker.updateText(using: api, collections: collections)
        XCTAssertFalse(partial)
        let calls = await api.cursors
        XCTAssertTrue(calls.isEmpty)
        XCTAssertEqual(try store.cursor(collections: collections), "partial")
    }

    func testChapterDeltaUpdatesOfflineReaderWithoutMediaRequests() async throws {
        let (store, worker) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        let api = LibraryFixture([page("updated", mode: "delta", revision: 2, content: "New text")])
        let updated = try await worker.updateText(using: api, collections: collections)
        XCTAssertTrue(updated)
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: chapter)?.data?["content"]?.string, "New text")
        XCTAssertEqual(try store.cursor(collections: collections), "updated")
        let calls = await api.cursors
        let mediaCalls = await api.mediaCalls
        XCTAssertEqual(calls, ["ready"])
        XCTAssertEqual(mediaCalls, 0)
    }

    func testExpiredCursorRequiresWiFiAndPreservesDownloadedText() async throws {
        let (store, worker) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        let api = LibraryFixture(expired: true)
        let updated = try await worker.updateText(using: api, collections: collections)
        XCTAssertFalse(updated)
        XCTAssertNil(try store.cursor(collections: collections))
        XCTAssertFalse(try store.isBootstrapped(collections: collections))
        XCTAssertNotNil(try store.record(collection: "fic_chapters", id: chapter))
        let calls = await api.cursors
        XCTAssertEqual(calls.count, 1)
    }

    func testUnexpectedBootstrapIsNotAppliedByCellularUpdate() async throws {
        let (store, worker) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        let api = LibraryFixture([page("replacement", revision: 2, content: "Replacement")])
        let updated = try await worker.updateText(using: api, collections: collections)
        XCTAssertFalse(updated)
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: chapter)?.data?["content"]?.string, "Original")
        XCTAssertFalse(try store.isBootstrapped(collections: collections))
    }

    func testDeltaPassIsBoundedAndResumesFromCommittedCursor() async throws {
        let (store, worker) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        let api = LibraryFixture([
            page("next", mode: "delta", more: true, revision: 2),
            page("last", mode: "delta", revision: 3),
        ])
        _ = try await worker.updateText(using: api, collections: collections, maxPages: 1)
        XCTAssertEqual(try store.cursor(collections: collections), "next")
        _ = try await worker.updateText(using: api, collections: collections, maxPages: 1)
        XCTAssertEqual(try store.cursor(collections: collections), "last")
        let calls = await api.cursors
        XCTAssertEqual(calls, ["ready", "next"])
    }

    func testBulkBootstrapEnablesLaterUpdatesAndKeepsUIConnectionReadable() async throws {
        let (store, worker) = try setup()
        let api = LibraryFixture([page("partial", more: true), page("ready", revision: 2)])
        _ = try await worker.download(using: api, collections: collections, mediaCollections: [],
                                      budget: 1024) { _ in
            await MainActor.run {
                XCTAssertNotNil(try? store.record(collection: "fic_chapters", id: self.chapter))
            }
        }
        XCTAssertTrue(try store.isBootstrapped(collections: collections))
        XCTAssertEqual(try store.cursor(collections: collections), "ready")
    }

    func testCancellationKeepsCursorAndWorkerCanResume() async throws {
        let (store, worker) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        let slow = LibraryFixture(delay: true)
        let task = Task { try await worker.updateText(using: slow, collections: collections) }
        while await slow.cursors.isEmpty { await Task.yield() }
        task.cancel()
        do { _ = try await task.value; XCTFail("Cancelled update succeeded") }
        catch is CancellationError {}
        XCTAssertEqual(try store.cursor(collections: collections), "ready")
        let api = LibraryFixture([page("resumed", mode: "delta", revision: 2)])
        _ = try await worker.updateText(using: api, collections: collections)
        XCTAssertEqual(try store.cursor(collections: collections), "resumed")
    }

    func testBulkDownloadCanBeCancelledWhileUIReadsSavedContent() async throws {
        let (store, worker) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        let slow = LibraryFixture(delay: true)
        let task = Task {
            try await worker.download(using: slow, collections: collections,
                                      mediaCollections: [], budget: 1024) { _ in }
        }
        while await slow.cursors.isEmpty { await Task.yield() }
        // This test runs on MainActor: the request is still pending while the
        // UI can read its own connection and issue Pause.
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: chapter)?.title, "Chapter")
        task.cancel()
        do { _ = try await task.value; XCTFail("Cancelled bulk download succeeded") }
        catch is CancellationError {}
        XCTAssertEqual(try store.cursor(collections: collections), "ready")
        let api = LibraryFixture([page("resumed", mode: "delta", revision: 2)])
        _ = try await worker.download(using: api, collections: collections,
                                      mediaCollections: [], budget: 1024) { _ in }
        XCTAssertEqual(try store.cursor(collections: collections), "resumed")
    }

    func testPageCannotCommitAgainstAResetCursor() throws {
        let (store, _) = try setup()
        try store.apply(page("ready"), startingBootstrap: true)
        try store.resetCursor(collections: collections)
        XCTAssertThrowsError(try store.apply(
            page("stale", mode: "delta", revision: 2, content: "Stale"),
            startingBootstrap: false, expectedCursor: "ready"))
        XCTAssertNil(try store.cursor(collections: collections))
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: chapter)?.data?["content"]?.string, "Original")
    }
}
