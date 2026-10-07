import XCTest
@testable import LunaschalCore

private actor FicFixture: FicDownloadTransport {
    var pages: [String: FicDownloadPage]
    var file = Data()
    var requested: [String] = []
    var notFound = false

    init(pages: [String: FicDownloadPage], file: Data = Data(), notFound: Bool = false) {
        self.pages = pages; self.file = file; self.notFound = notFound
    }

    func ficDownloadPage(ficID: String, after: String) async throws -> FicDownloadPage {
        requested.append(after)
        if notFound { throw HTTPFailure(status: 404) }
        guard let page = pages[after] else { throw ReplicaError.invalidPage }
        return page
    }

    func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data {
        file.subdata(in: Int(offset)..<Int(offset + count))
    }
}

private final class Progress: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [FicDownloadProgress] = []
    func add(_ value: FicDownloadProgress) { lock.lock(); values.append(value); lock.unlock() }
    var all: [FicDownloadProgress] { lock.lock(); defer { lock.unlock() }; return values }
}

@MainActor
final class FicDownloadTests: XCTestCase {
    private let epoch = ULID.make()
    private let fic = ULID.make()

    private func setup() throws -> (ReplicaStore, LibraryDownload, URL) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let url = root.appendingPathComponent("replica.sqlite")
        let store = try ReplicaStore(url: url)
        // The replica already knows the server's history: the fic list synced.
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [],
                                 hasMore: false, cursor: "fics", collections: ["fics"]), startingBootstrap: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return (store, LibraryDownload(replicaURL: url, mediaURL: root.appendingPathComponent("media")), root)
    }

    private func chapter(_ id: String, revision: Int64 = 5, title: String = "Chapter") -> SyncChange {
        SyncChange(revision: revision, collection: "fic_chapters", id: id, deleted: false,
                   data: ["id": .string(id), "ficId": .string(fic), "title": .string(title)])
    }

    private func page(_ chapters: [SyncChange], after: String, more: Bool, before: Int64, bytes: Int64,
                      total: Int64 = 30, media: MediaDescriptor? = nil, epoch: String? = nil) -> FicDownloadPage {
        FicDownloadPage(epoch: epoch ?? self.epoch, fic: nil, chapters: chapters, hasMore: more, after: after,
                        totalChapters: 3, textBytes: total, bytesBefore: before, pageBytes: bytes, media: media)
    }

    func testDownloadsEveryPageIntoTheReplicaAndReportsProgress() async throws {
        let (store, worker, _) = try setup()
        let ids = [ULID.make(), ULID.make(), ULID.make()]
        let api = FicFixture(pages: [
            "": page([chapter(ids[0]), chapter(ids[1])], after: "1:b", more: true, before: 0, bytes: 20),
            "1:b": page([chapter(ids[2])], after: "2:c", more: false, before: 20, bytes: 10),
        ])
        let progress = Progress()
        try await worker.downloadFic(fic, using: api) { progress.add($0) }
        XCTAssertEqual(Set(try store.relatedRecords(collection: "fic_chapters", field: "ficId", value: fic).map(\.id)), Set(ids))
        XCTAssertEqual(progress.all.map(\.doneBytes), [20, 30])
        XCTAssertEqual(progress.all.last?.fraction, 1)
        XCTAssertEqual(progress.all.map(\.after), ["1:b", "2:c"])
    }

    func testResumesFromTheGivenPageKey() async throws {
        let (_, worker, _) = try setup()
        let api = FicFixture(pages: ["1:b": page([chapter(ULID.make())], after: "2:c", more: false, before: 20, bytes: 10)])
        try await worker.downloadFic(fic, after: "1:b", using: api) { _ in }
        let requested = await api.requested
        XCTAssertEqual(requested, ["1:b"])
    }

    func testAPageFromAnotherHistoryIsNotStored() async throws {
        let (store, worker, _) = try setup()
        let api = FicFixture(pages: ["": page([chapter(ULID.make())], after: "0:a", more: false, before: 0, bytes: 10,
                                              epoch: ULID.make())])
        do {
            try await worker.downloadFic(fic, using: api) { _ in }
            XCTFail("expected historyChanged")
        } catch FicDownloadError.historyChanged {}
        XCTAssertTrue(try store.relatedRecords(collection: "fic_chapters", field: "ficId", value: fic).isEmpty)
    }

    func testA404SaysTheServerCouldNotSendIt() async throws {
        let (_, worker, _) = try setup()
        do {
            try await worker.downloadFic(fic, using: FicFixture(pages: [:], notFound: true)) { _ in }
            XCTFail("expected notOnServer")
        } catch FicDownloadError.notOnServer {}
    }

    func testAPDFBookDownloadsAndVerifiesItsFile() async throws {
        // The worker verifies the file with MediaStore's real hasher, which
        // needs CryptoKit. Skipped rather than compiled out: Linux's generated
        // test list fails to type-check with this class's async test removed.
        #if !canImport(CryptoKit)
        throw XCTSkip("MediaStore verification needs CryptoKit")
        #endif
        let (_, worker, root) = try setup()
        let bytes = Data((0..<(3 * 1024 * 1024 / 2)).map { UInt8($0 % 251) })
        let source = root.appendingPathComponent("source.pdf")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try bytes.write(to: source)
        let item = MediaDescriptor(collection: "fics", id: fic, available: true, size: Int64(bytes.count),
                                   sha256: try MediaStore.sha256(source), mime: "application/pdf", url: nil, reason: nil)
        let api = FicFixture(pages: ["": page([], after: "", more: false, before: 0, bytes: 0, total: 0, media: item)],
                             file: bytes)
        let progress = Progress()
        try await worker.downloadFic(fic, using: api) { progress.add($0) }
        let media = try MediaStore(root: root.appendingPathComponent("media"))
        let file = try XCTUnwrap(try media.downloaded(collection: "fics", id: fic))
        XCTAssertEqual(try Data(contentsOf: file), bytes)
        XCTAssertEqual(progress.all.last?.doneBytes, Int64(bytes.count))
        XCTAssertEqual(progress.all.last?.totalBytes, Int64(bytes.count))
    }

    // MARK: Replica: a restarted library bootstrap keeps what is already readable

    func testRestartingABootstrapKeepsChaptersUntilItFinishes() throws {
        let (store, _, _) = try setup()
        let collections = ["fic_chapters"]
        let kept = ULID.make(), gone = ULID.make(), prefetched = ULID.make()
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap",
                                 changes: [chapter(kept, revision: 1), chapter(gone, revision: 2)],
                                 hasMore: false, cursor: "old", collections: collections), startingBootstrap: true)
        // The library download starts over from scratch.
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap",
                                 changes: [chapter(kept, revision: 1)],
                                 hasMore: true, cursor: "p1", collections: collections), startingBootstrap: true)
        XCTAssertNotNil(try store.record(collection: "fic_chapters", id: gone), "still readable mid-bootstrap")
        // A fic opened meanwhile is fetched ahead of the bootstrap.
        XCTAssertTrue(try store.storePrefetched([chapter(prefetched, revision: 9)], epoch: epoch))
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [],
                                 hasMore: false, cursor: "done", collections: collections),
                        startingBootstrap: false, expectedCursor: "p1")
        XCTAssertNotNil(try store.record(collection: "fic_chapters", id: kept))
        XCTAssertNotNil(try store.record(collection: "fic_chapters", id: prefetched))
        XCTAssertNil(try store.record(collection: "fic_chapters", id: gone), "the server no longer has it")
        XCTAssertTrue(try store.records(collection: "fic_chapters", query: "Chapter").allSatisfy { $0.id != gone })
    }

    func testAPrefetchedRevisionIsNotOverwrittenByAnOlderOne() throws {
        let (store, _, _) = try setup()
        let id = ULID.make()
        try store.storePrefetched([chapter(id, revision: 9, title: "New")], epoch: epoch)
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap",
                                 changes: [chapter(id, revision: 3, title: "Old")],
                                 hasMore: false, cursor: "c", collections: ["fic_chapters"]), startingBootstrap: true)
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: id)?.data?["title"]?.string, "New")
    }

    // MARK: Queue

    func testOpeningAFicMovesItToTheFrontAndKeepsItsPlace() {
        var queue = FicQueue()
        XCTAssertTrue(queue.prioritize("a", title: "A"))
        queue.record("a", after: "4:x")
        XCTAssertTrue(queue.prioritize("b", title: "B"))
        XCTAssertEqual(queue.entries.map(\.id), ["b", "a"])
        XCTAssertFalse(queue.prioritize("b", title: "B"), "already downloading: nothing to preempt")
        XCTAssertTrue(queue.prioritize("a", title: "A"))
        XCTAssertEqual(queue.entries.map(\.id), ["a", "b"])
        XCTAssertEqual(queue.head?.after, "4:x")
        queue.remove("a")
        XCTAssertEqual(queue.head?.id, "b")
    }

    // MARK: Estimate

    func testEstimateUsesOnlyBytesMovedThisSession() {
        let start = Date(timeIntervalSince1970: 0)
        var estimate = TransferEstimate(started: start)
        XCTAssertNil(estimate.secondsLeft(done: 500, total: 1500, now: start), "first sample is the baseline")
        XCTAssertNil(estimate.secondsLeft(done: 600, total: 1500, now: start.addingTimeInterval(0.5)))
        // 500 bytes resumed, then 100 bytes/s.
        XCTAssertEqual(try XCTUnwrap(estimate.secondsLeft(done: 700, total: 1500, now: start.addingTimeInterval(2))), 8, accuracy: 0.001)
        XCTAssertEqual(estimate.secondsLeft(done: 1500, total: 1500, now: start.addingTimeInterval(3)), 0)
    }

    func testEstimateWording() {
        XCTAssertEqual(TransferEstimate.describe(nil), "Estimating time left…")
        XCTAssertEqual(TransferEstimate.describe(20), "Less than a minute left")
        XCTAssertEqual(TransferEstimate.describe(150), "About 3 min left")
        XCTAssertEqual(TransferEstimate.describe(7200), "About 2 h left")
        XCTAssertEqual(TransferEstimate.describe(5400), "About 1 h 30 min left")
    }
}

extension FicDownloadTests {
    func testRelatedCountMatchesRelatedRecords() async throws {
        let (store, worker, _) = try setup()
        let api = FicFixture(pages: ["": page([chapter(ULID.make()), chapter(ULID.make())], after: "1:b", more: false, before: 0, bytes: 20)])
        try await worker.downloadFic(fic, using: api) { _ in }
        XCTAssertEqual(try store.relatedCount(collection: "fic_chapters", field: "ficId", value: fic), 2)
        XCTAssertEqual(try store.relatedCount(collection: "fic_chapters", field: "ficId", value: ULID.make()), 0)
    }
}
