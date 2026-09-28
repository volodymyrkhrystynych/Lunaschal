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

    func testOldReplayCannotOverwriteNewerRevision() throws {
        try store.apply(page([record(3, content: "Latest")]), startingBootstrap: true)
        try store.apply(page([record(1)], cursor: "cursor-two", mode: "delta"), startingBootstrap: false)
        XCTAssertEqual(try store.record(collection: "journal_entries", id: id)?.data?["content"]?.string, "Latest")
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
