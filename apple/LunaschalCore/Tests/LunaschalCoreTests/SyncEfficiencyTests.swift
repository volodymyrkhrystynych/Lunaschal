import Foundation
import XCTest
import CSQLite
@testable import LunaschalCore

/// A server that counts what it is asked: pages per scope, status checks, and
/// a 410 for a scope whose history it has dropped.
private actor CountingServer: ReplicaTransport {
    var pages: [String: [SyncPage]] = [:]
    var statuses: [ScopeStatus]?
    var expired: Set<String> = []
    private(set) var pageRequests: [String] = []
    private(set) var statusRequests: [[String]] = []

    init(statuses: [ScopeStatus]? = nil) { self.statuses = statuses }

    func queue(_ page: SyncPage) { pages[page.collections.sorted().joined(separator: ","), default: []].append(page) }
    func expire(_ collections: [String]) { expired.insert(collections.sorted().joined(separator: ",")) }

    func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage {
        let scope = collections.sorted().joined(separator: ",")
        pageRequests.append(scope)
        if cursor != nil, expired.remove(scope) != nil { throw HTTPFailure(status: 410) }
        guard var queued = pages[scope], !queued.isEmpty else { throw ReplicaError.invalidPage }
        let page = queued.removeFirst()
        pages[scope] = queued
        return page
    }

    func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply {
        let data = operation.data.merging(["id": .string(operation.recordId)]) { $1 }
        return OperationReply(operationId: operation.id,
                              change: SyncChange(revision: operation.baseRevision + 10, collection: operation.collection,
                                                 id: operation.recordId, deleted: false, data: data),
                              conflict: nil, current: nil, error: nil, resetRequired: nil)
    }

    func syncStatus(cursors: [String]) async throws -> [ScopeStatus]? {
        statusRequests.append(cursors)
        return statuses
    }
}

final class SyncEfficiencyTests: XCTestCase {
    private let epoch = ULID.make()
    private let journal = ["journal_entries"]
    private let calendar = ["calendar_events"]
    private var root: URL!
    private var url: URL { root.appendingPathComponent("replica.sqlite") }

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { [root] in try? FileManager.default.removeItem(at: root!) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    private func entry(_ id: String = ULID.make(), _ content: String = "Text", revision: Int64 = 1,
                       collection: String = "journal_entries") -> SyncChange {
        SyncChange(revision: revision, collection: collection, id: id, deleted: false,
                   data: ["id": .string(id), "content": .string(content), "createdAt": .string("2026-10-0\(revision % 9 + 1)T10:00:00Z")])
    }

    private func page(_ changes: [SyncChange], _ collections: [String], cursor: String = ULID.make(),
                      mode: String = "bootstrap", more: Bool = false) -> SyncPage {
        SyncPage(protocolVersion: 1, epoch: epoch, mode: mode, changes: changes, hasMore: more,
                 cursor: cursor, collections: collections)
    }

    // MARK: What a page changed

    func testApplyReportsOnlyTheCollectionsItWrote() throws {
        let store = try ReplicaStore(url: url)
        let saved = entry()
        XCTAssertEqual(try store.apply(page([saved], journal), startingBootstrap: true), ["journal_entries"])
        XCTAssertEqual(try store.apply(page([saved], journal, mode: "delta"), startingBootstrap: false), [],
                       "a version already held changes nothing")
        XCTAssertEqual(try store.apply(page([], journal, mode: "delta"), startingBootstrap: false), [])
        // A finished bootstrap that drops a record is a change too.
        try store.apply(page([], journal, more: false), startingBootstrap: true)
        XCTAssertNil(try store.record(collection: "journal_entries", id: saved.id))
    }

    func testARestartedBootstrapThatDropsARecordReportsIt() throws {
        let store = try ReplicaStore(url: url)
        let kept = entry(), gone = entry()
        try store.apply(page([kept, gone], journal), startingBootstrap: true)
        XCTAssertEqual(try store.apply(page([kept], journal), startingBootstrap: true), ["journal_entries"])
    }

    func testARunReportsPulledChangesAndSentEdits() async throws {
        let store = try ReplicaStore(url: url)
        let saved = entry()
        try store.apply(page([saved], journal), startingBootstrap: true)
        _ = try store.queue(record: saved, data: ["content": .string("Edited")])
        let server = CountingServer()
        await server.queue(page([], journal, mode: "delta"))
        let changed = try await ReplicaSync(url: url).run(using: server, collections: journal)
        XCTAssertEqual(changed, ["journal_entries"], "the acknowledged edit rewrote the record")
        XCTAssertEqual(try store.record(collection: "journal_entries", id: saved.id)?.data?["content"]?.string, "Edited")
    }

    // MARK: Asking before pulling

    func testOneStatusRequestAnswersEveryScope() async throws {
        let store = try ReplicaStore(url: url)
        try store.apply(page([entry()], journal), startingBootstrap: true)
        try store.apply(page([entry(collection: "calendar_events")], calendar), startingBootstrap: true)
        let server = CountingServer(statuses: [ScopeStatus(changed: false, resetRequired: false),
                                               ScopeStatus(changed: true, resetRequired: false)])
        let pull = try await ReplicaSync(url: url).scopesToPull(using: server, scopes: [journal, calendar, ["fic_chapters"]])
        XCTAssertEqual(pull, [false, true, true], "no cursor yet means a bootstrap to make")
        let asked = await server.statusRequests
        XCTAssertEqual(asked.count, 1)
        XCTAssertEqual(asked.first?.count, 2, "only scopes with a cursor are asked about")
        let pages = await server.pageRequests
        XCTAssertTrue(pages.isEmpty, "asking fetches nothing")
    }

    func testAResetCursorIsPulledAndAServerWithoutTheCheckPullsEverything() async throws {
        let store = try ReplicaStore(url: url)
        try store.apply(page([entry()], journal), startingBootstrap: true)
        let reset = CountingServer(statuses: [ScopeStatus(changed: false, resetRequired: true)])
        let first = try await ReplicaSync(url: url).scopesToPull(using: reset, scopes: [journal])
        XCTAssertEqual(first, [true])
        let old = CountingServer(statuses: nil)
        let second = try await ReplicaSync(url: url).scopesToPull(using: old, scopes: [journal, calendar])
        XCTAssertEqual(second, [true, true])
    }

    func testAnExpiredScopeResetsOnlyItsOwnCursor() async throws {
        let store = try ReplicaStore(url: url)
        try store.apply(page([entry()], journal, cursor: "journal-cursor"), startingBootstrap: true)
        try store.apply(page([entry(collection: "calendar_events")], calendar, cursor: "calendar-cursor"), startingBootstrap: true)
        let server = CountingServer()
        await server.expire(journal)
        await server.queue(page([entry()], journal, cursor: "fresh"))
        try await ReplicaSync(url: url).run(using: server, collections: journal, sendEdits: false)
        XCTAssertEqual(try store.cursor(collections: journal), "fresh")
        XCTAssertEqual(try store.cursor(collections: calendar), "calendar-cursor", "another scope's cursor is untouched")
    }

    // MARK: The library

    func testALibraryWhoseCursorExpiredAsksForAWiFiDownload() async throws {
        let store = try ReplicaStore(url: url)
        let scope = ["fic_chapters"]
        let chapter = SyncChange(revision: 1, collection: "fic_chapters", id: ULID.make(), deleted: false,
                                 data: ["id": .string(ULID.make()), "ficId": .string(ULID.make()), "contentText": .string("Words")])
        let fixed = SyncChange(revision: 1, collection: "fic_chapters", id: chapter.data!["id"]!.string!, deleted: false, data: chapter.data)
        try store.apply(page([fixed], scope, cursor: "library"), startingBootstrap: true)
        let worker = LibraryDownload(replicaURL: url, mediaURL: root.appendingPathComponent("media"))
        let server = CountingServer()
        await server.queue(page([SyncChange(revision: 2, collection: "fic_chapters", id: fixed.id, deleted: false,
                                            data: fixed.data!.merging(["contentText": .string("New")]) { $1 })],
                                scope, mode: "delta"))
        let updated = try await worker.updateText(using: server, collections: scope)
        XCTAssertTrue(updated)
        let taken = await worker.takeTextChanges()
        XCTAssertEqual(taken, ["fic_chapters"])
        let again = await worker.takeTextChanges()
        XCTAssertEqual(again, [], "taken once")
        var needs = await worker.needsBootstrap
        XCTAssertFalse(needs)
        await server.expire(scope)
        let expired = try await worker.updateText(using: server, collections: scope)
        XCTAssertFalse(expired)
        needs = await worker.needsBootstrap
        XCTAssertTrue(needs)
        XCTAssertNotNil(try store.record(collection: "fic_chapters", id: fixed.id), "the text already here stays readable")
    }

    func testFicsOpenedOneAtATimeNeverTriggerALibraryDownload() async throws {
        let store = try ReplicaStore(url: url)
        try store.apply(page([entry()], journal), startingBootstrap: true)
        let fic = ULID.make(), id = ULID.make()
        XCTAssertTrue(try store.storePrefetched([SyncChange(revision: 3, collection: "fic_chapters", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(fic), "contentText": .string("Opened on its own")])], epoch: epoch))
        let worker = LibraryDownload(replicaURL: url, mediaURL: root.appendingPathComponent("media"))
        let updated = try await worker.updateText(using: CountingServer(), collections: ["fic_chapters"])
        XCTAssertFalse(updated)
        let needs = await worker.needsBootstrap
        XCTAssertFalse(needs, "never downloaded as a library, so nothing to re-download")
    }

    // MARK: Reading off the main thread

    func testTheReaderReadsWhileAnotherConnectionHoldsTheWriteLock() async throws {
        let store = try ReplicaStore(url: url)
        let saved = entry()
        try store.apply(page([saved], journal), startingBootstrap: true)
        var writer: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &writer), SQLITE_OK)
        defer { sqlite3_close(writer) }
        XCTAssertEqual(sqlite3_exec(writer, "BEGIN IMMEDIATE; UPDATE replica_meta SET value=value", nil, nil, nil), SQLITE_OK)
        let reader = ReplicaReader(url: url)
        let started = Date()
        let read = try await reader.read { try $0.records(collection: "journal_entries").map(\.id) }
        XCTAssertEqual(read, [saved.id])
        XCTAssertLessThan(Date().timeIntervalSince(started), 1, "never waited on the writer")
        sqlite3_exec(writer, "ROLLBACK", nil, nil, nil)
    }

    func testTheJournalsTimelineOrderIsIndexed() throws {
        let store = try ReplicaStore(url: url)
        try store.apply(page((1...5).map { entry(revision: Int64($0)) }, journal), startingBootstrap: true)
        try store.buildIndexes()
        let plan = try store.newestQueryPlan(collection: "journal_entries")
        XCTAssertTrue(plan.contains("replica_by_createdAt"), plan)
        XCTAssertFalse(plan.contains("TEMP B-TREE"), "no sort of every entry: \(plan)")
        let newest = try store.newestRecords(collection: "journal_entries", limit: 2)
        XCTAssertEqual(newest.map { $0.data?["createdAt"]?.string }, ["2026-10-06T10:00:00Z", "2026-10-05T10:00:00Z"])
    }

    // MARK: Capture files

    func testTheCaptureListSeesFilesChangedBehindItsBack() throws {
        let store = try CaptureStore(root: root.appendingPathComponent("captures"))
        let first = Capture(text: "First")
        try store.save(first)
        XCTAssertEqual(try store.list().map(\.id), [first.id])
        // Another writer (the Watch receipts) removes a manifest directly.
        try FileManager.default.removeItem(at: store.root.appendingPathComponent(first.id + ".json"))
        XCTAssertEqual(try store.list().map(\.id), [])
        var edited = Capture(text: "Second")
        try store.save(edited)
        edited.lastError = "Changed"
        let data = try JSONEncoder().encode(edited)
        Thread.sleep(forTimeInterval: 0.01)
        try data.write(to: store.root.appendingPathComponent(edited.id + ".json"))
        XCTAssertEqual(try store.list().first?.lastError, "Changed")
    }

    func testTidyingKeepsMealsRecentCapturesAndUnconfirmedWatchRecordings() throws {
        let store = try CaptureStore(root: root.appendingPathComponent("captures"))
        let old = Date(timeIntervalSinceNow: -30 * 86_400)
        func synced(_ capture: Capture) throws -> Capture {
            var capture = capture
            capture.state = .synced
            try store.save(capture)
            return capture
        }
        let gone = try synced(Capture(text: "On the server", now: old))
        let recent = try synced(Capture(text: "Yesterday", now: Date(timeIntervalSinceNow: -86_400)))
        let unknown = try synced(Capture(text: "Not in the replica yet", now: old))
        let meal = try synced(Capture(text: "Ramen", kind: .food, now: old))
        let pending = Capture(text: "Not sent", now: old)
        try store.save(pending)
        var watch = Capture(mode: .transcribe, now: old)
        watch.state = .synced
        try store.save(watch)
        try Data("audio".utf8).write(to: store.audioURL(watch))
        let receipt = try WatchServerReceipt(capture: watch)
        try JSONEncoder().encode(receipt).write(to: store.root.appendingPathComponent(watch.id + ".watch-origin"))
        try JSONEncoder().encode(receipt).write(to: store.root.appendingPathComponent(watch.id + ".server-receipt"))

        let removed = try store.tidySynced(entry: { $0 == unknown.id ? .unknown : .present })
        XCTAssertEqual(removed, 1)
        XCTAssertEqual(Set(try store.list().map(\.id)), [recent.id, unknown.id, meal.id, pending.id, watch.id])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.root.appendingPathComponent(gone.id + ".json").path))

        // Once the Watch confirms, its recording goes too; its receipt stays.
        try JSONEncoder().encode(receipt).write(to: store.root.appendingPathComponent(watch.id + ".watch-server-confirmed"))
        XCTAssertEqual(try store.tidySynced(entry: { $0 == unknown.id ? .unknown : .present }), 1)
        XCTAssertFalse(try store.list().contains { $0.id == watch.id })
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.root.appendingPathComponent(watch.id + ".server-receipt").path))
    }
}
