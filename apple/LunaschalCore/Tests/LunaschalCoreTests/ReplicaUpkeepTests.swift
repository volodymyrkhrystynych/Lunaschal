import Foundation
import XCTest
import CSQLite
@testable import LunaschalCore

/// The replica's storage shape: a chapter's text beside its payload rather
/// than in it, and a search index that holds only what a search box reads,
/// keyed so a record's entry is found without scanning the index.
final class ReplicaUpkeepTests: XCTestCase {
    private let epoch = ULID.make()
    private var url: URL!

    override func setUpWithError() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        url = root.appendingPathComponent("replica.sqlite")
    }

    private func chapter(_ id: String = ULID.make(), fic: String, text: String = "Once upon a time.", revision: Int64 = 1) -> SyncChange {
        SyncChange(revision: revision, collection: "fic_chapters", id: id, deleted: false,
                   data: ["id": .string(id), "ficId": .string(fic), "title": .string("Chapter"), "position": .number(1),
                          "contentHtml": .string("<p>\(text)</p>"), "contentText": .string(text)])
    }

    private func journal(_ content: String, id: String = ULID.make(), revision: Int64 = 1) -> SyncChange {
        SyncChange(revision: revision, collection: "journal_entries", id: id, deleted: false,
                   data: ["id": .string(id), "content": .string(content), "createdAt": .string("2026-10-07T10:00:00Z")])
    }

    private func apply(_ changes: [SyncChange], to store: ReplicaStore, collections: [String], bootstrap: Bool = true) throws {
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: bootstrap ? "bootstrap" : "delta",
                                 changes: changes, hasMore: false, cursor: ULID.make(), collections: collections),
                        startingBootstrap: bootstrap)
    }

    /// Raw SQL against the file, beside the store's own connection.
    private func query(_ sql: String) throws -> [[String]] {
        var connection: OpaquePointer?
        guard sqlite3_open(url.path, &connection) == SQLITE_OK else { throw ReplicaError.database("open") }
        defer { sqlite3_close(connection) }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(connection, sql, -1, &statement, nil) == SQLITE_OK else {
            throw ReplicaError.database(String(cString: sqlite3_errmsg(connection)))
        }
        defer { sqlite3_finalize(statement) }
        var out: [[String]] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            out.append((0..<sqlite3_column_count(statement)).map {
                sqlite3_column_text(statement, $0).map { String(cString: $0) } ?? "NULL"
            })
        }
        return out
    }

    private func exec(_ sql: String) throws {
        var connection: OpaquePointer?
        guard sqlite3_open(url.path, &connection) == SQLITE_OK else { throw ReplicaError.database("open") }
        defer { sqlite3_close(connection) }
        var error: UnsafeMutablePointer<CChar>?
        guard sqlite3_exec(connection, sql, nil, nil, &error) == SQLITE_OK else {
            throw ReplicaError.database(error.map { String(cString: $0) } ?? "exec")
        }
    }

    func testAChaptersTextIsKeptOutOfItsPayloadAndOutOfTheSearchIndex() throws {
        let store = try ReplicaStore(url: url)
        let fic = ULID.make()
        let text = String(repeating: "Dragons over the river. ", count: 400)
        let saved = chapter(fic: fic, text: text)
        try apply([saved], to: store, collections: ["fic_chapters"])
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: saved.id), saved, "read back whole")
        let raw = try query("SELECT payload,body FROM replica_records WHERE id='\(saved.id)'")[0]
        XCTAssertFalse(raw[0].contains("Dragons"), "the payload carries no text")
        XCTAssertTrue(raw[1].contains("Dragons"))
        XCTAssertEqual(try store.chapterOutline(bookID: fic).map(\.id), [saved.id])
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_find")[0][0], "0", "chapters are not indexed")
        XCTAssertGreaterThan(try store.storedBytes(collections: ["fic_chapters"]), Int64(text.utf8.count * 2))
    }

    func testARevisedOrDeletedEntryLeavesOneSearchEntryOrNone() throws {
        let store = try ReplicaStore(url: url)
        let id = ULID.make()
        try apply([journal("A walk by the river", id: id)], to: store, collections: ["journal_entries"])
        try apply([journal("Up the mountain", id: id, revision: 2)], to: store, collections: ["journal_entries"], bootstrap: false)
        XCTAssertEqual(try store.count(collection: "journal_entries", query: "river"), 0)
        XCTAssertEqual(try store.records(collection: "journal_entries", query: "mount").map(\.id), [id])
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_find")[0][0], "1")
        try apply([SyncChange(revision: 3, collection: "journal_entries", id: id, deleted: true, data: nil)],
                  to: store, collections: ["journal_entries"], bootstrap: false)
        XCTAssertEqual(try store.count(collection: "journal_entries", query: "mount"), 0)
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_find")[0][0], "0")
    }

    func testABootstrapSweepRemovesTheSearchEntriesOfWhatTheServerDropped() throws {
        let store = try ReplicaStore(url: url)
        let kept = journal("Kept river walk"), gone = journal("Gone river walk")
        try apply([kept, gone], to: store, collections: ["journal_entries"])
        try apply([kept], to: store, collections: ["journal_entries"])
        XCTAssertEqual(try store.records(collection: "journal_entries", query: "river").map(\.id), [kept.id])
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_find")[0][0], "1")
    }

    /// The regression behind "database is locked": the old index was looked up
    /// by columns FTS5 can't index, so every record written scanned all of it.
    func testFindingARecordsSearchEntryDoesNotScanTheIndex() throws {
        _ = try ReplicaStore(url: url)
        // FTS5 names its plan "INDEX <flags>:<constraints>", "=" being the rowid.
        func plan(_ sql: String) throws -> String {
            try query("EXPLAIN QUERY PLAN " + sql).map { $0.last ?? "" }.joined(separator: "\n")
        }
        let now = try plan("DELETE FROM replica_find WHERE rowid=CAST('1' AS INTEGER)")
        XCTAssertTrue(now.hasSuffix(":="), now)
        try exec("CREATE VIRTUAL TABLE old_search USING fts5(collection UNINDEXED,id UNINDEXED,title,body)")
        let before = try plan("DELETE FROM old_search WHERE collection='journal_entries' AND id='x'")
        XCTAssertTrue(before.hasSuffix(":"), "the old lookup scans: \(before)")
    }

    // MARK: Upgrading a version 3 database

    /// The schema an older build left: no `body`, chapter text in the payload,
    /// and the old index holding everything, chapters included.
    private func makeVersion3(_ records: [SyncChange]) throws {
        var sql = """
            PRAGMA journal_mode=WAL;
            CREATE TABLE replica_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE replica_records(collection TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,deleted INTEGER NOT NULL,payload TEXT,PRIMARY KEY(collection,id));
            CREATE TABLE replica_outbox(id TEXT PRIMARY KEY,operation TEXT NOT NULL,original TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',error TEXT,conflict TEXT);
            CREATE VIRTUAL TABLE replica_search USING fts5(collection UNINDEXED,id UNINDEXED,title,body);
            CREATE TABLE replica_sweep(collection TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(collection,id));
            INSERT INTO replica_meta VALUES ('epoch','\(epoch)');
            """
        for record in records {
            let payload = String(decoding: try JSONEncoder().encode(record.data), as: UTF8.self).replacingOccurrences(of: "'", with: "''")
            let text = (record.data?["contentText"]?.string ?? record.data?["content"]?.string ?? "").replacingOccurrences(of: "'", with: "''")
            sql += """
                INSERT INTO replica_records VALUES ('\(record.collection)','\(record.id)',\(record.revision),0,'\(payload)');
                INSERT INTO replica_search VALUES ('\(record.collection)','\(record.id)','\(record.title)','\(text)');
                """
        }
        try exec(sql + "PRAGMA user_version=3;")
    }

    func testAVersion3DatabaseOpensSearchableAndIsCompactedLater() throws {
        let fic = ULID.make()
        let chapters = (0..<60).map { _ in chapter(fic: fic, text: "Dragons over the river.") }
        let walk = journal("A walk by the river")
        try makeVersion3(chapters + [walk])

        let store = try ReplicaStore(url: url)
        XCTAssertEqual(try query("PRAGMA user_version")[0][0], "4")
        XCTAssertEqual(try store.records(collection: "journal_entries", query: "river").map(\.id), [walk.id])
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_find")[0][0], "1", "only the entry is indexed")
        // Readable as it was before anything is moved.
        XCTAssertEqual(try store.record(collection: "fic_chapters", id: chapters[0].id), chapters[0])
        XCTAssertEqual(try store.chapterOutline(bookID: fic).count, 60)

        var steps = 0
        try store.maintain { steps += 1; return steps <= 2 }
        XCTAssertTrue(try query("SELECT name FROM sqlite_master WHERE name='replica_search'").isEmpty, "the old index is dropped")
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_records WHERE body IS NOT NULL")[0][0], "25",
                       "one batch moved before it was told to stop")
        try store.maintain()
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_records WHERE collection='fic_chapters' AND body IS NULL")[0][0], "0")
        XCTAssertTrue(try query("SELECT payload FROM replica_records WHERE collection='fic_chapters'").allSatisfy { !$0[0].contains("Dragons") })
        XCTAssertEqual(try store.missingIndexes(), [])
        for saved in chapters {
            XCTAssertEqual(try store.record(collection: "fic_chapters", id: saved.id), saved)
        }
        XCTAssertEqual(try store.record(collection: "journal_entries", id: walk.id), walk)
    }

    func testReopeningAnUpgradedDatabaseDoesNotIndexTwice() throws {
        try makeVersion3([journal("A walk by the river")])
        let first = try ReplicaStore(url: url), second = try ReplicaStore(url: url)
        XCTAssertEqual(try first.count(collection: "journal_entries", query: "river"), 1)
        XCTAssertEqual(try second.count(collection: "journal_entries", query: "river"), 1)
        XCTAssertEqual(try query("SELECT COUNT(*) FROM replica_find")[0][0], "1")
    }
}
