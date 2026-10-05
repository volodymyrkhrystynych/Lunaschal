import Foundation
import XCTest
import CSQLite
@testable import LunaschalCore

final class JournalSearchTests: XCTestCase {
    func testCaptureSearchIncludesOriginalsLinksAndTranscriptsWithoutChangingCapture() throws {
        var capture = Capture(text: "Kyiv river walk", youtubeURLs: ["https://www.youtube.com/watch?v=aircAruvnKk"])
        let payload: [String: Any] = ["id": capture.id, "content": "Polished prose", "title": "Evening",
                                    "rawContent": "Unpolished dictation", "attachments": [["id": ULID.make(), "transcript": "Київ recording"]]]
        capture.snapshot = try JSONDecoder().decode(JournalSnapshot.self, from: JSONSerialization.data(withJSONObject: payload))
        let original = capture
        for query in ["", "  ", "kyiv RIVER", "unpolished", "КИЇВ", "aircAruvnKk", "evening prose"] {
            XCTAssertTrue(capture.matchesSearch(query), query)
        }
        XCTAssertFalse(capture.matchesSearch("river missing"))
        XCTAssertEqual(capture, original)
    }

    func testOldJournalIndexGainsOriginalDictationWithoutLosingPendingEditsOrCursor() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("replica.sqlite")
        var store: ReplicaStore? = try ReplicaStore(url: url)
        let id = ULID.make(), epoch = ULID.make()
        let record = SyncChange(revision: 1, collection: "journal_entries", id: id, deleted: false,
                               data: ["id": .string(id), "content": .string("A polished entry"), "rawContent": .string("Original constellation dictation")])
        try store!.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [record],
                                 hasMore: false, cursor: "journal", collections: ["journal_entries"]), startingBootstrap: true)
        let edit = try store!.queue(record: record, data: ["content": .string("An unsynced edit")])
        store = nil
        var connection: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &connection), SQLITE_OK)
        XCTAssertEqual(sqlite3_exec(connection, "UPDATE replica_search SET body='A polished entry'; PRAGMA user_version=2", nil, nil, nil), SQLITE_OK)
        sqlite3_close(connection)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.records(collection: "journal_entries", query: "constell").map(\.id), [id])
        XCTAssertEqual(try reopened.count(collection: "journal_entries", query: "constell"), 1)
        XCTAssertEqual(try reopened.edits().first?.operation.id, edit.id)
        XCTAssertEqual(try reopened.cursor(collections: ["journal_entries"]), "journal")
        XCTAssertEqual(try reopened.record(collection: "journal_entries", id: id), record)
    }
}
