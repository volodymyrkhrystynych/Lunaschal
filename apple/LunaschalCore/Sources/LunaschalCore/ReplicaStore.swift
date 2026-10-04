import Foundation
import CSQLite

/// Confine each connection to its owner; a page and its cursor commit together.
/// The library worker uses a separate WAL connection so UI reads remain available.
/// The outbox is independent of the server projection, including during resets.
public final class ReplicaStore {
    private var db: OpaquePointer?
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    public init(url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard sqlite3_open_v2(url.path, &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "Could not open database"
            sqlite3_close(db); db = nil
            throw ReplicaError.database(message)
        }
        sqlite3_busy_timeout(db, 5000)
        do {
            try execute("PRAGMA journal_mode=WAL")
            try execute("PRAGMA synchronous=FULL")
            let version = Int(try rows("PRAGMA user_version").first?.first ?? "0") ?? 0
            guard version <= 3 else { throw ReplicaError.database("This database needs a newer app.") }
            try transaction {
                try execute("CREATE TABLE IF NOT EXISTS replica_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                try execute("CREATE TABLE IF NOT EXISTS replica_records(collection TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,deleted INTEGER NOT NULL,payload TEXT,PRIMARY KEY(collection,id))")
                try execute("CREATE TABLE IF NOT EXISTS replica_outbox(id TEXT PRIMARY KEY,operation TEXT NOT NULL,original TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',error TEXT,conflict TEXT)")
                try execute("CREATE VIRTUAL TABLE IF NOT EXISTS replica_search USING fts5(collection UNINDEXED,id UNINDEXED,title,body)")
                if version < 2 {
                    for record in try recordsQuery("WHERE collection='newspaper_frontpages' AND deleted=0", []) {
                        try execute("UPDATE replica_search SET title=? WHERE collection=? AND id=?",
                                    [record.title, record.collection, record.id])
                    }
                }
                if version < 3 {
                    for record in try recordsQuery("WHERE collection='journal_entries' AND deleted=0", []) {
                        try execute("UPDATE replica_search SET body=? WHERE collection=? AND id=?",
                                    [searchBody(record.data ?? [:]), record.collection, record.id])
                    }
                }
                try execute("PRAGMA user_version=3")
            }
        } catch { sqlite3_close(db); db = nil; throw error }
    }

    deinit { sqlite3_close(db) }

    public var epoch: String? { get throws { try value("epoch") } }

    public func cursor(collections: [String]) throws -> String? { try value(scope(collections)) }

    public func isBootstrapped(collections: [String]) throws -> Bool {
        try value("ready:" + scope(collections)) == "1"
    }

    public func resetCursor(collections: [String]) throws {
        try transaction {
            try execute("DELETE FROM replica_meta WHERE key IN (?,?)",
                        [scope(collections), "ready:" + scope(collections)])
        }
    }

    public func resetCursors() throws {
        // Existing records remain readable while a replacement bootstrap runs.
        // Pending edits keep their old epoch and are surfaced as conflicts.
        try execute("DELETE FROM replica_meta WHERE key LIKE 'cursor:%'")
        try execute("DELETE FROM replica_meta WHERE key LIKE 'ready:%'")
    }

    public func apply(_ page: SyncPage, startingBootstrap: Bool, expectedCursor: String? = nil) throws {
        guard page.protocolVersion == 1, ULID.isValid(page.epoch),
              ["bootstrap", "delta"].contains(page.mode), !page.cursor.isEmpty,
              !page.collections.isEmpty, Set(page.collections).count == page.collections.count,
              page.changes.allSatisfy({ page.collections.contains($0.collection) && $0.revision > 0 &&
                  !$0.id.isEmpty && ($0.deleted ? $0.data == nil : $0.data?["id"]?.string == $0.id) }) else {
            throw ReplicaError.invalidPage
        }
        try transaction {
            // Another connection can reset history while a request is in flight.
            // Validate under the same write transaction as the page commit.
            let previousEpoch = try epoch
            if previousEpoch != page.epoch && !startingBootstrap { throw ReplicaError.needsBootstrap }
            if let expectedCursor, try cursor(collections: page.collections) != expectedCursor {
                throw ReplicaError.needsBootstrap
            }
            if previousEpoch != page.epoch {
                try execute("DELETE FROM replica_meta WHERE key LIKE 'cursor:%'")
                try execute("DELETE FROM replica_meta WHERE key LIKE 'ready:%'")
                try execute("DELETE FROM replica_records")
                try execute("DELETE FROM replica_search")
                try execute("UPDATE replica_outbox SET state='conflict',error='Server history changed. Review this saved edit.' WHERE state='pending'")
                try set("epoch", page.epoch)
            } else if startingBootstrap {
                try execute("DELETE FROM replica_meta WHERE key=?", ["ready:" + scope(page.collections)])
                // A complete bootstrap is a replacement for its chosen scope;
                // no unselected records or pending edits are removed.
                for collection in page.collections {
                    try execute("DELETE FROM replica_records WHERE collection=?", [collection])
                    try execute("DELETE FROM replica_search WHERE collection=?", [collection])
                }
            }
            for change in page.changes { try put(change) }
            try set(scope(page.collections), page.cursor)
            if !page.hasMore { try set("ready:" + scope(page.collections), "1") }
        }
    }

    public func record(collection: String, id: String) throws -> SyncChange? {
        try recordsQuery("WHERE collection=? AND id=?", [collection, id]).first
    }

    public func records(collection: String, query: String = "", limit: Int = 200) throws -> [SyncChange] {
        guard limit > 0 else { throw ReplicaError.invalidPage }
        let (filter, parameters) = recordFilter(collection: collection, query: query)
        return try recordsQuery(filter + " ORDER BY revision DESC,id ASC LIMIT ?", parameters + [String(limit)])
    }

    public func count(collection: String, query: String = "") throws -> Int {
        let (filter, parameters) = recordFilter(collection: collection, query: query)
        return Int(try rows("SELECT COUNT(*) FROM replica_records " + filter, parameters).first?[0] ?? "0") ?? 0
    }

    private func recordFilter(collection: String, query: String) -> (String, [String]) {
        let filter = "WHERE collection=? AND deleted=0"
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return (filter, [collection]) }
        let terms = query.split(whereSeparator: { $0.isWhitespace }).map { "\"" + $0.replacingOccurrences(of: "\"", with: "\"\"") + "\"*" }.joined(separator: " AND ")
        return (filter + " AND id IN (SELECT id FROM replica_search WHERE replica_search MATCH ? AND collection=?)",
                [collection, terms, collection])
    }

    public func relatedRecords(collection: String, field: String, value: String) throws -> [SyncChange] {
        guard ["ficId", "paperId", "pageId", "entryId", "conversationId"].contains(field) else { throw ReplicaError.invalidPage }
        return try recordsQuery("WHERE collection=? AND deleted=0 AND json_extract(payload,?)=? ORDER BY revision",
                                [collection, "$." + field, value])
    }

    public func paperPages(paperID: String) throws -> [SyncChange] {
        try relatedRecords(collection: "paper_pages", field: "paperId", value: paperID).sorted {
            let left = $0.data?["position"]?.number ?? 0
            let right = $1.data?["position"]?.number ?? 0
            return left == right ? $0.id < $1.id : left < right
        }
    }

    public func readingPosition(collection: String, id: String, version: String) throws -> Int? {
        guard let saved = try value(readingKey(collection, id)) else { return nil }
        let position = try decode(ReadingPosition.self, saved)
        return position.version == version && position.offset >= 0 ? position.offset : nil
    }

    public func saveReadingPosition(collection: String, id: String, version: String, offset: Int, bookID: String? = nil) throws {
        let key = try readingKey(collection, id)
        guard !version.isEmpty, version.count <= 256, offset >= 0,
              bookID == nil || (collection == "fic_chapters" && ULID.isValid(bookID!)) else {
            throw ReplicaError.invalidEdit
        }
        if let bookID {
            guard let chapter = try record(collection: collection, id: id), !chapter.deleted,
                  chapter.data?["ficId"]?.string == bookID else { throw ReplicaError.invalidEdit }
        }
        try transaction {
            try set(key, json(ReadingPosition(version: version, offset: offset)))
            if let bookID { try set("reading-book:" + bookID, id) }
        }
    }

    public func lastReadChapter(bookID: String) throws -> SyncChange? {
        guard ULID.isValid(bookID) else { throw ReplicaError.invalidEdit }
        guard let id = try value("reading-book:" + bookID),
              let chapter = try record(collection: "fic_chapters", id: id), !chapter.deleted,
              chapter.data?["ficId"]?.string == bookID else { return nil }
        return chapter
    }

    private func readingKey(_ collection: String, _ id: String) throws -> String {
        guard ["fic_chapters", "fics", "study_sources", "journal_attachments"].contains(collection), ULID.isValid(id) else {
            throw ReplicaError.invalidEdit
        }
        return "reading-position:" + collection + ":" + id
    }

    public func queue(record: SyncChange, data: [String: JSONValue], delete: Bool = false) throws -> ReplicaOperation {
        guard let epoch = try epoch else { throw ReplicaError.needsBootstrap }
        guard record.collection == "journal_entries", !record.deleted,
              (delete ? data.isEmpty : !data.isEmpty), Set(data.keys).isSubset(of: ["content", "title", "tags"]) else {
            throw ReplicaError.invalidEdit
        }
        guard !(try edits()).contains(where: { $0.operation.collection == record.collection && $0.operation.recordId == record.id }) else {
            throw ReplicaError.editAlreadyPending
        }
        let operation = ReplicaOperation(epoch: epoch, record: record, action: delete ? "delete" : "update", data: data)
        try execute("INSERT INTO replica_outbox(id,operation,original) VALUES (?,?,?)",
                    [operation.id, try json(operation), try json(record)])
        return operation
    }

    public func edits() throws -> [PendingEdit] {
        try rows("SELECT operation,state,COALESCE(error,''),original,COALESCE(conflict,'') FROM replica_outbox ORDER BY rowid").map {
            PendingEdit(operation: try decode(ReplicaOperation.self, $0[0]), state: $0[1],
                        error: $0[2].isEmpty ? nil : $0[2], original: try decode(SyncChange.self, $0[3]),
                        conflict: $0[4].isEmpty ? nil : try decode(SyncChange.self, $0[4]))
        }
    }

    public func acknowledge(_ operation: ReplicaOperation, reply: OperationReply) throws {
        guard reply.operationId == operation.id, let change = reply.change,
              change.collection == operation.collection, change.id == operation.recordId,
              change.revision >= operation.baseRevision else { throw CaptureError.invalidResponse }
        try transaction {
            try put(change)
            try execute("DELETE FROM replica_outbox WHERE id=?", [operation.id])
        }
    }

    public func hold(_ operation: ReplicaOperation, reply: OperationReply, state: String = "conflict") throws {
        try transaction {
            if let current = reply.current { try put(current) }
            try execute("UPDATE replica_outbox SET state=?,error=?,conflict=? WHERE id=?",
                        [state, reply.error ?? "Review this edit", try reply.current.map(json), operation.id])
        }
    }

    /// Explicit user resolution only. Local text remains in the outbox until
    /// either discarded here or a replacement operation is durably queued.
    public func resolve(_ edit: PendingEdit, keepLocal: Bool) throws {
        try transaction {
            if keepLocal {
                guard let current = try record(collection: edit.operation.collection, id: edit.operation.recordId), !current.deleted else {
                    throw ReplicaError.invalidEdit // offer Save as new entry instead
                }
                try execute("DELETE FROM replica_outbox WHERE id=?", [edit.id])
                _ = try queue(record: current, data: edit.operation.data, delete: edit.operation.action == "delete")
            } else { try execute("DELETE FROM replica_outbox WHERE id=?", [edit.id]) }
        }
    }

    private func put(_ change: SyncChange) throws {
        if let current = try record(collection: change.collection, id: change.id), current.revision >= change.revision { return }
        try execute("INSERT OR REPLACE INTO replica_records(collection,id,revision,deleted,payload) VALUES (?,?,?,?,?)",
                    [change.collection, change.id, String(change.revision), change.deleted ? "1" : "0", try change.data.map(json)])
        try execute("DELETE FROM replica_search WHERE collection=? AND id=?", [change.collection, change.id])
        if let data = change.data, !change.deleted {
            let body = searchBody(data)
            try execute("INSERT INTO replica_search(collection,id,title,body) VALUES (?,?,?,?)", [change.collection, change.id, change.title, body])
        }
    }

    private func searchBody(_ data: [String: JSONValue]) -> String {
        ["content", "rawContent", "contentText", "description", "summary", "transcript"]
            .compactMap { data[$0]?.string }.joined(separator: "\n")
    }

    private func recordsQuery(_ suffix: String, _ arguments: [String?]) throws -> [SyncChange] {
        try rows("SELECT collection,id,revision,deleted,COALESCE(payload,'') FROM replica_records \(suffix)", arguments).map {
            SyncChange(revision: Int64($0[2])!, collection: $0[0], id: $0[1], deleted: $0[3] == "1",
                       data: $0[4].isEmpty ? nil : try decode([String: JSONValue].self, $0[4]))
        }
    }

    private func scope(_ collections: [String]) -> String { "cursor:" + collections.sorted().joined(separator: ",") }
    private func value(_ key: String) throws -> String? { try rows("SELECT value FROM replica_meta WHERE key=?", [key]).first?.first }
    private func set(_ key: String, _ value: String) throws { try execute("INSERT OR REPLACE INTO replica_meta(key,value) VALUES (?,?)", [key, value]) }
    private func json<T: Encodable>(_ value: T) throws -> String { String(decoding: try encoder.encode(value), as: UTF8.self) }
    private func decode<T: Decodable>(_ type: T.Type, _ text: String) throws -> T { try decoder.decode(type, from: Data(text.utf8)) }

    private func transaction(_ work: () throws -> Void) throws {
        try execute("BEGIN IMMEDIATE")
        do { try work(); try execute("COMMIT") }
        catch { try? execute("ROLLBACK"); throw error }
    }

    private func execute(_ sql: String, _ args: [String?] = []) throws { _ = try rows(sql, args) }

    private func rows(_ sql: String, _ args: [String?] = []) throws -> [[String]] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw failure() }
        defer { sqlite3_finalize(statement) }
        for (index, value) in args.enumerated() {
            let result = value.map { sqlite3_bind_text(statement, Int32(index + 1), $0, -1, transient) }
                ?? sqlite3_bind_null(statement, Int32(index + 1))
            guard result == SQLITE_OK else { throw failure() }
        }
        var result: [[String]] = []
        while true {
            let step = sqlite3_step(statement)
            if step == SQLITE_DONE { return result }
            guard step == SQLITE_ROW else { throw failure() }
            result.append((0..<sqlite3_column_count(statement)).map { column in
                sqlite3_column_text(statement, column).map { String(cString: $0) } ?? ""
            })
        }
    }

    private func failure() -> ReplicaError { .database(String(cString: sqlite3_errmsg(db))) }
}
