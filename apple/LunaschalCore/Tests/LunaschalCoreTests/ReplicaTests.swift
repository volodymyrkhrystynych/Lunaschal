import Foundation
import XCTest
import CSQLite
@testable import LunaschalCore

final class ReplicaTests: XCTestCase {
    private var root: URL!
    private var url: URL!
    private var store: ReplicaStore!
    private let epoch = ULID.make()
    private let id = ULID.make()

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        url = root.appendingPathComponent("replica.sqlite")
        store = try ReplicaStore(url: url)
    }
    override func tearDownWithError() throws { store = nil; try FileManager.default.removeItem(at: root) }

    private func record(_ revision: Int64 = 1, content: String = "Original", id: String? = nil, deleted: Bool = false) -> SyncChange {
        let id = id ?? self.id
        return SyncChange(revision: revision, collection: "journal_entries", id: id, deleted: deleted,
                          data: deleted ? nil : ["id": .string(id), "content": .string(content), "title": .string("Thought")])
    }

    private func page(_ changes: [SyncChange], cursor: String = "cursor-one", epoch: String? = nil, mode: String = "bootstrap") -> SyncPage {
        SyncPage(protocolVersion: 1, epoch: epoch ?? self.epoch, mode: mode, changes: changes,
                 hasMore: false, cursor: cursor, collections: ["journal_entries"])
    }

    func testPageAndCursorSurviveReopenAndSearchWorksOffline() throws {
        try store.apply(page([record(content: "Walked by the river")]), startingBootstrap: true)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.cursor(collections: ["journal_entries"]), "cursor-one")
        XCTAssertEqual(try reopened.records(collection: "journal_entries", query: "river").map(\.id), [id])
        XCTAssertEqual(try reopened.count(collection: "journal_entries"), 1)
        XCTAssertEqual(try reopened.epoch, epoch)
    }

    func testOptionalKnowledgeRemainsReadableAfterOtherCollectionsRefresh() throws {
        let article = SyncChange(revision: 1, collection: "wiki_articles", id: id, deleted: false,
            data: ["id": .string(id), "title": .string("Offline knowledge"),
                   "summary": .string("A saved reference"), "content": .string("# Astronomy\n\nA reference to constellations.")])
        let knowledgePage = SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [article],
            hasMore: false, cursor: "knowledge", collections: ["wiki_articles"])
        try store.apply(knowledgePage, startingBootstrap: true)
        // A later download without optional knowledge replaces only its own
        // collections. Existing optional content stays available offline.
        try store.apply(page([record(content: "Journal only")]), startingBootstrap: true)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.records(collection: "wiki_articles", query: "constell").first, article)
        XCTAssertEqual(try reopened.count(collection: "wiki_articles", query: "constell"), 1)
        XCTAssertEqual(try reopened.count(collection: "journal_entries", query: "constell"), 0)
        let deletion = SyncChange(revision: 2, collection: "wiki_articles", id: id, deleted: true, data: nil)
        try reopened.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "delta", changes: [deletion],
            hasMore: false, cursor: "knowledge-deleted", collections: ["wiki_articles"]), startingBootstrap: false)
        XCTAssertEqual(try reopened.count(collection: "wiki_articles"), 0)
        XCTAssertEqual(try reopened.count(collection: "journal_entries"), 1)
    }

    func testExpandedOfflineListsAndSearchReachBeyondTwoHundredRecords() throws {
        let changes = (1...210).map { index in
            record(Int64(index), content: index <= 205 ? "River walk" : "Mountain hike", id: ULID.make())
        }
        try store.apply(page(changes), startingBootstrap: true)
        let first = try store.records(collection: "journal_entries")
        XCTAssertEqual(first.count, 200)
        let expanded = try store.records(collection: "journal_entries", limit: 400)
        XCTAssertEqual(expanded.map(\.id), changes.reversed().map(\.id))
        XCTAssertEqual(Array(expanded.prefix(200)), first)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.count(collection: "journal_entries", query: "riv"), 205)
        XCTAssertEqual(try reopened.records(collection: "journal_entries", query: "riv").count, 200)
        XCTAssertEqual(try reopened.records(collection: "journal_entries", query: "riv", limit: 400).count, 205)
        XCTAssertEqual(try reopened.count(collection: "fics", query: "riv"), 0)
    }

    func testListAndSearchOrderingAgreeAndDeletionUpdatesFilteredCount() throws {
        let first = record(1, content: "River walk", id: ULID.make())
        let second = record(1, content: "River walk", id: ULID.make())
        try store.apply(page([second, first]), startingBootstrap: true)
        let expected = [first.id, second.id].sorted()
        XCTAssertEqual(try store.records(collection: "journal_entries").map(\.id), expected)
        XCTAssertEqual(try store.records(collection: "journal_entries", query: "river").map(\.id), expected)
        try store.apply(page([record(2, id: first.id, deleted: true)], mode: "delta"), startingBootstrap: false)
        XCTAssertEqual(try store.count(collection: "journal_entries", query: "river"), 1)
        XCTAssertEqual(try store.records(collection: "journal_entries", query: "river").map(\.id), [second.id])
        XCTAssertEqual(try store.count(collection: "journal_entries", query: "  "), 1)
        XCTAssertEqual(try store.count(collection: "journal_entries", query: "river OR mountain"), 0)
        XCTAssertEqual(try store.count(collection: "journal_entries", query: "\"river\""), 1)
        XCTAssertThrowsError(try store.records(collection: "journal_entries", limit: -1))
        XCTAssertThrowsError(try store.records(collection: "journal_entries", limit: 0))
    }

    func testOldReplayCannotOverwriteNewerRevision() throws {
        try store.apply(page([record(3, content: "Latest")]), startingBootstrap: true)
        try store.apply(page([record(1)], cursor: "cursor-two", mode: "delta"), startingBootstrap: false)
        XCTAssertEqual(try store.record(collection: "journal_entries", id: id)?.data?["content"]?.string, "Latest")
    }

    func testNewspaperSearchMigratesPreviouslyDownloadedCoversWithoutRedownload() throws {
        let cover = SyncChange(revision: 1, collection: "newspaper_frontpages", id: id, deleted: false,
                               data: ["id": .string(id), "paper": .string("Daily Planet"), "date": .string("2026-10-02")])
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [cover],
                                hasMore: false, cursor: "covers", collections: ["newspaper_frontpages"]), startingBootstrap: true)
        store = nil
        var connection: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &connection), SQLITE_OK)
        // Recreate the old index representation, preserving records and cursors.
        XCTAssertEqual(sqlite3_exec(connection, "UPDATE replica_search SET title='newspaper_frontpages'; PRAGMA user_version=1", nil, nil, nil), SQLITE_OK)
        sqlite3_close(connection)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.records(collection: "newspaper_frontpages", query: "Planet").map(\.id), [id])
        XCTAssertEqual(try reopened.count(collection: "newspaper_frontpages", query: "2026-10-02"), 1)
        XCTAssertEqual(try reopened.cursor(collections: ["newspaper_frontpages"]), "covers")
        XCTAssertEqual(try reopened.record(collection: "newspaper_frontpages", id: id), cover)
    }

    func testPaperPageOrderSurvivesReopenAndExcludesOtherDocumentsAndDeletedPages() throws {
        let paperID = ULID.make()
        let ids = (0..<205).map { _ in ULID.make() }
        var changes = ids.enumerated().map { index, id in
            SyncChange(revision: Int64(300 - index), collection: "paper_pages", id: id, deleted: false,
                       data: ["id": .string(id), "paperId": .string(paperID), "position": .number(Double(index))])
        }
        let otherID = ULID.make()
        changes.append(SyncChange(revision: 400, collection: "paper_pages", id: otherID, deleted: false,
                                  data: ["id": .string(otherID), "paperId": .string("another-paper"), "position": .number(0)]))
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: changes,
                                hasMore: false, cursor: "papers", collections: ["paper_pages"]), startingBootstrap: true)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.paperPages(paperID: paperID).map(\.id), ids)
        let deleted = SyncChange(revision: 500, collection: "paper_pages", id: ids[0], deleted: true, data: nil)
        try reopened.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "delta", changes: [deleted],
                                   hasMore: false, cursor: "papers-deleted", collections: ["paper_pages"]), startingBootstrap: false)
        XCTAssertEqual(try reopened.paperPages(paperID: paperID).map(\.id), Array(ids.dropFirst()))
    }

    func testTombstoneRemovesSearchResultWithoutLosingRevision() throws {
        try store.apply(page([record(content: "river")]), startingBootstrap: true)
        try store.apply(page([record(2, deleted: true)], mode: "delta"), startingBootstrap: false)
        XCTAssertEqual(try store.count(collection: "journal_entries"), 0)
        XCTAssertTrue(try store.records(collection: "journal_entries", query: "river").isEmpty)
        XCTAssertEqual(try store.record(collection: "journal_entries", id: id)?.revision, 2)
    }

    func testFailureMidPageRollsBackRecordsAndCursor() throws {
        try store.apply(page([record()]), startingBootstrap: true)
        let rejectedID = ULID.make()
        var connection: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &connection), SQLITE_OK)
        defer { sqlite3_close(connection) }
        let sql = "CREATE TRIGGER refuse_record BEFORE INSERT ON replica_records WHEN NEW.id='\(rejectedID)' BEGIN SELECT RAISE(ABORT, 'write failed'); END"
        XCTAssertEqual(sqlite3_exec(connection, sql, nil, nil, nil), SQLITE_OK)
        let update = page([record(2, content: "should roll back"), record(3, id: rejectedID)], cursor: "cursor-two", mode: "delta")
        XCTAssertThrowsError(try store.apply(update, startingBootstrap: false))
        XCTAssertEqual(try store.record(collection: "journal_entries", id: id)?.revision, 1)
        XCTAssertEqual(try store.cursor(collections: ["journal_entries"]), "cursor-one")
    }

    func testRemoteUpdatesDoNotOverwritePendingLocalEdit() throws {
        let original = record()
        try store.apply(page([original]), startingBootstrap: true)
        let operation = try store.queue(record: original, data: ["content": .string("My offline version")])
        try store.apply(page([record(2, content: "Other device")], mode: "delta"), startingBootstrap: false)
        let reopened = try ReplicaStore(url: url)
        let pending = try XCTUnwrap(reopened.edits().first)
        XCTAssertEqual(pending.operation, operation)
        XCTAssertEqual(pending.original, original)
        XCTAssertEqual(pending.operation.data["content"]?.string, "My offline version")
        XCTAssertThrowsError(try reopened.queue(record: original, data: ["content": .string("second")]))
    }

    func testEpochResetRetainsLocalEditAsConflict() throws {
        let original = record()
        try store.apply(page([original]), startingBootstrap: true)
        _ = try store.queue(record: original, data: ["content": .string("Don't lose this")])
        let newEpoch = ULID.make()
        try store.apply(page([], cursor: "new-cursor", epoch: newEpoch), startingBootstrap: true)
        XCTAssertEqual(try store.epoch, newEpoch)
        XCTAssertEqual(try store.edits().first?.state, "conflict")
        XCTAssertEqual(try store.edits().first?.operation.data["content"]?.string, "Don't lose this")
        XCTAssertNil(try store.record(collection: "journal_entries", id: id))
    }

    func testConflictResolutionUsesNewIdentityAndCurrentRevision() throws {
        let original = record()
        try store.apply(page([original]), startingBootstrap: true)
        let old = try store.queue(record: original, data: ["content": .string("Mine")])
        let current = record(5, content: "Theirs")
        let reply = OperationReply(operationId: nil, change: nil, conflict: true, current: current, error: "Conflict", resetRequired: nil)
        try store.hold(old, reply: reply)
        let pending = try XCTUnwrap(store.edits().first)
        XCTAssertEqual(pending.conflict, current)
        try store.resolve(pending, keepLocal: true)
        let replacement = try XCTUnwrap(store.edits().first)
        XCTAssertNotEqual(replacement.id, old.id)
        XCTAssertEqual(replacement.operation.baseRevision, 5)
        XCTAssertEqual(replacement.operation.data, old.data)
        XCTAssertEqual(replacement.state, "pending")
    }

    func testAcknowledgementMustMatchOperationBeforeRemovingLocalEdit() throws {
        let original = record()
        try store.apply(page([original]), startingBootstrap: true)
        let operation = try store.queue(record: original, data: ["content": .string("Mine")])
        let wrong = OperationReply(operationId: "wrong", change: record(2), conflict: nil, current: nil, error: nil, resetRequired: nil)
        XCTAssertThrowsError(try store.acknowledge(operation, reply: wrong))
        XCTAssertEqual(try store.edits().count, 1)
        let good = OperationReply(operationId: operation.id, change: record(2, content: "Mine"), conflict: nil, current: nil, error: nil, resetRequired: nil)
        try store.acknowledge(operation, reply: good)
        XCTAssertTrue(try store.edits().isEmpty)
        XCTAssertEqual(try store.record(collection: "journal_entries", id: id)?.revision, 2)
    }
}
