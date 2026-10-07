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
                try execute("CREATE TABLE IF NOT EXISTS replica_sweep(collection TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(collection,id))")
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
                try execute("DELETE FROM replica_sweep")
                try execute("UPDATE replica_outbox SET state='conflict',error='Server history changed. Review this saved edit.' WHERE state='pending'")
                try set("epoch", page.epoch)
            } else if startingBootstrap {
                try execute("DELETE FROM replica_meta WHERE key=?", ["ready:" + scope(page.collections)])
                // A complete bootstrap is a replacement for its chosen scope;
                // no unselected records or pending edits are removed. What it
                // replaces is swept when it finishes, not deleted as it starts:
                // a 5 GB library bootstrap used to empty every downloaded fic
                // for the hours it took to reach them again.
                for collection in page.collections {
                    try execute("INSERT OR IGNORE INTO replica_sweep(collection,id) SELECT collection,id FROM replica_records WHERE collection=?", [collection])
                }
            }
            for change in page.changes { try put(change) }
            try set(scope(page.collections), page.cursor)
            if !page.hasMore {
                try set("ready:" + scope(page.collections), "1")
                for collection in page.collections {
                    try execute("DELETE FROM replica_search WHERE collection=? AND id IN (SELECT id FROM replica_sweep WHERE collection=?)", [collection, collection])
                    try execute("DELETE FROM replica_records WHERE collection=? AND id IN (SELECT id FROM replica_sweep WHERE collection=?)", [collection, collection])
                    try execute("DELETE FROM replica_sweep WHERE collection=?", [collection])
                }
            }
        }
    }

    /// Records fetched outside any cursor — one fic, ahead of the library
    /// download. Each is the server's latest revision of that record, so the
    /// bootstrap or delta that reaches it later is a no-op (`put` keeps the
    /// newer revision). A page from another epoch is dropped: those revisions
    /// mean nothing against this replica's history.
    @discardableResult
    public func storePrefetched(_ changes: [SyncChange], epoch: String) throws -> Bool {
        guard changes.allSatisfy({ $0.revision > 0 && !$0.id.isEmpty &&
            ($0.deleted ? $0.data == nil : $0.data?["id"]?.string == $0.id) }) else { throw ReplicaError.invalidPage }
        var stored = false
        try transaction {
            guard try self.epoch == epoch else { return }
            for change in changes { try put(change) }
            stored = true
        }
        return stored
    }

    public func record(collection: String, id: String) throws -> SyncChange? {
        try recordsQuery("WHERE collection=? AND id=?", [collection, id]).first
    }

    public func records(collection: String, query: String = "", limit: Int = 200) throws -> [SyncChange] {
        guard limit > 0 else { throw ReplicaError.invalidPage }
        let (filter, parameters) = recordFilter(collection: collection, query: query)
        return try recordsQuery(filter + " ORDER BY revision DESC,id ASC LIMIT ?", parameters + [String(limit)])
    }

    /// Newest first by when each record was made, rather than by when the
    /// server last changed it: the Journal feed reads as a timeline, and an old
    /// entry edited today belongs where it was written.
    public func newestRecords(collection: String, query: String = "", limit: Int = 200) throws -> [SyncChange] {
        guard limit > 0 else { throw ReplicaError.invalidPage }
        let (filter, parameters) = recordFilter(collection: collection, query: query)
        return try recordsQuery(filter + " ORDER BY json_extract(payload,'$.createdAt') DESC,id DESC LIMIT ?",
                                parameters + [String(limit)])
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

    /// `relatedRecords(...).count` without reading every payload: a long fic's
    /// chapters are megabytes of text, and this is asked on every book opened.
    public func relatedCount(collection: String, field: String, value: String) throws -> Int {
        guard ["ficId", "paperId", "pageId", "entryId", "conversationId"].contains(field) else { throw ReplicaError.invalidPage }
        return Int(try rows("SELECT COUNT(*) FROM replica_records WHERE collection=? AND deleted=0 AND json_extract(payload,?)=?",
                            [collection, "$." + field, value]).first?[0] ?? "0") ?? 0
    }

    /// How many chapters each of these books has on the device, in one pass:
    /// the library list badges a page of books at a time.
    public func chapterCounts(bookIDs: [String]) throws -> [String: Int] {
        var counts: [String: Int] = [:]
        for start in stride(from: 0, to: bookIDs.count, by: 400) {
            let chunk = Array(bookIDs[start..<min(start + 400, bookIDs.count)])
            let marks = Array(repeating: "?", count: chunk.count).joined(separator: ",")
            // Written out literally so an expression index on ficId can serve it.
            for row in try rows("""
                SELECT json_extract(payload,'$.ficId'),COUNT(*) FROM replica_records
                WHERE collection='fic_chapters' AND deleted=0 AND json_extract(payload,'$.ficId') IN (\(marks))
                GROUP BY 1
                """, chunk) { counts[row[0]] = Int(row[1]) ?? 0 }
        }
        return counts
    }

    /// Bytes of record text held for these collections: what downloading the
    /// library put in the database, beside the media files it put on disk.
    public func storedBytes(collections: [String]) throws -> Int64 {
        guard !collections.isEmpty else { return 0 }
        let marks = Array(repeating: "?", count: collections.count).joined(separator: ",")
        return Int64(try rows("""
            SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM replica_records
            WHERE deleted=0 AND collection IN (\(marks))
            """, collections).first?[0] ?? "0") ?? 0
    }

    public func relatedRecords(collection: String, field: String, value: String) throws -> [SyncChange] {
        guard ["ficId", "paperId", "pageId", "entryId", "conversationId"].contains(field) else { throw ReplicaError.invalidPage }
        return try recordsQuery("WHERE collection=? AND deleted=0 AND json_extract(payload,?)=? ORDER BY revision",
                                [collection, "$." + field, value])
    }

    /// `relatedRecords` for many parents in one pass: the Journal feed draws
    /// every loaded entry's attachments at once.
    public func relatedRecords(collection: String, field: String, values: [String]) throws -> [SyncChange] {
        guard ["ficId", "paperId", "pageId", "entryId", "conversationId"].contains(field) else { throw ReplicaError.invalidPage }
        var out: [SyncChange] = []
        // Kept well under SQLite's bound-parameter limit.
        for start in stride(from: 0, to: values.count, by: 400) {
            let chunk = Array(values[start..<min(start + 400, values.count)])
            let marks = Array(repeating: "?", count: chunk.count).joined(separator: ",")
            out += try recordsQuery("WHERE collection=? AND deleted=0 AND json_extract(payload,?) IN (\(marks)) ORDER BY revision",
                                    [collection, "$." + field] + chunk)
        }
        return out
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
            if let bookID { try set("reading-book-opened:" + bookID, String(Date().timeIntervalSince1970)) }
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

    public func markBookOpened(_ id: String) throws {
        guard ULID.isValid(id), let book = try record(collection: "fics", id: id), !book.deleted else {
            throw ReplicaError.invalidEdit
        }
        try set("reading-book-opened:" + id, String(Date().timeIntervalSince1970))
    }

    public func books(filter: BookFilter, limit: Int = 50) throws -> (records: [SyncChange], count: Int) {
        guard limit > 0 else { throw ReplicaError.invalidPage }
        var clauses = ["collection='fics'", "deleted=0"]
        var args: [String?] = []
        for word in filter.query.split(whereSeparator: { $0.isWhitespace }) {
            let escaped = word.replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "%", with: "\\%").replacingOccurrences(of: "_", with: "\\_")
            clauses.append("(json_extract(payload,'$.title') LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM json_each(payload,'$.tags') WHERE value LIKE ? ESCAPE '\\'))")
            args += ["%\(escaped)%", "%\(escaped)%"]
        }
        if !filter.source.isEmpty {
            clauses.append("(json_extract(payload,'$.sourceType')=? OR json_extract(payload,'$.site')=?)")
            args += [filter.source, filter.source]
        }
        if filter.folder == "unsorted" {
            clauses.append("NOT EXISTS (SELECT 1 FROM json_each(payload,'$.folderIds'))")
        } else if !filter.folder.isEmpty {
            clauses.append("EXISTS (SELECT 1 FROM json_each(payload,'$.folderIds') WHERE value=?)")
            args.append(filter.folder)
        }
        if !filter.tag.isEmpty {
            clauses.append("EXISTS (SELECT 1 FROM json_each(payload,'$.tags') WHERE value=?)")
            args.append(filter.tag)
        }
        if !filter.bookmark.isEmpty {
            clauses.append("""
                (EXISTS (SELECT 1 FROM replica_records b WHERE b.collection='fic_bookmarks' AND b.deleted=0
                    AND json_extract(b.payload,'$.ficId')=replica_records.id AND json_extract(b.payload,'$.type')=?
                    AND NOT EXISTS (SELECT 1 FROM replica_outbox d WHERE d.state='pending'
                        AND json_extract(d.operation,'$.collection')='fic_bookmarks'
                        AND json_extract(d.operation,'$.action')='delete'
                        AND json_extract(d.operation,'$.recordId')=b.id))
                OR EXISTS (SELECT 1 FROM replica_outbox o WHERE json_extract(o.operation,'$.collection')='fic_bookmarks'
                    AND json_extract(o.operation,'$.action')='create' AND o.state='pending'
                    AND json_extract(o.operation,'$.data.ficId')=replica_records.id AND json_extract(o.operation,'$.data.type')=?))
                """)
            args += [filter.bookmark, filter.bookmark]
        }
        let whereSQL = "WHERE " + clauses.joined(separator: " AND ")
        let order: String
        switch filter.sort {
        case "recent":
            order = """
                MAX(COALESCE((SELECT CAST(value AS REAL) FROM replica_meta WHERE key='reading-book-opened:' || replica_records.id),0),
                    COALESCE(CAST(strftime('%s',json_extract(payload,'$.lastOpenedAt')) AS REAL),0)) DESC,
                json_extract(payload,'$.updatedAt') DESC,id
                """
        case "title": order = "json_extract(payload,'$.title') COLLATE NOCASE,id"
        default: order = "COALESCE(json_extract(payload,'$.latestActivity'),CAST(strftime('%s',json_extract(payload,'$.createdAt')) AS REAL),0) DESC,id"
        }
        let count = Int(try rows("SELECT COUNT(*) FROM replica_records \(whereSQL)", args).first?[0] ?? "0") ?? 0
        let books = try recordsQuery("\(whereSQL) ORDER BY \(order) LIMIT ?", args + [String(limit)])
        return (books, count)
    }

    /// Where opening a book lands, in the desktop reader's order
    /// (`resolveInitialChapter`): the continue bookmark — a pending one
    /// included — at its scroll position, then the chapter this device last
    /// read, then the server's last-read chapter, then the first chapter. A
    /// chapter whose text isn't downloaded is skipped. Nil means no chapter text.
    public func resumePoint(bookID: String) throws -> (chapter: SyncChange, fraction: Double?)? {
        guard ULID.isValid(bookID) else { throw ReplicaError.invalidEdit }
        let chapters = try relatedRecords(collection: "fic_chapters", field: "ficId", value: bookID)
        func chapter(_ id: String?) -> SyncChange? { id.flatMap { id in chapters.first { $0.id == id } } }
        if let mark = try bookmarks(bookID: bookID).first(where: { $0.data?["type"]?.string == "continue" }),
           let target = chapter(mark.data?["chapterId"]?.string) {
            return (target, mark.data?["scrollPosition"]?.number)
        }
        if let local = try lastReadChapter(bookID: bookID) { return (local, nil) }
        if let server = chapter(try record(collection: "fics", id: bookID)?.data?["lastReadChapterId"]?.string) {
            return (server, nil)
        }
        return chapters.min { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) }.map { ($0, nil) }
    }

    /// How many books each folder holds, keyed by folder id, with books in no
    /// folder at all under `"unsorted"`.
    public func folderCounts() throws -> [String: Int] {
        var counts: [String: Int] = [:]
        for row in try rows("""
            SELECT j.value, COUNT(*) FROM replica_records r, json_each(r.payload,'$.folderIds') j
            WHERE r.collection='fics' AND r.deleted=0 GROUP BY j.value
            """) { counts[row[0]] = Int(row[1]) ?? 0 }
        counts["unsorted"] = Int(try rows("""
            SELECT COUNT(*) FROM replica_records WHERE collection='fics' AND deleted=0
            AND NOT EXISTS (SELECT 1 FROM json_each(payload,'$.folderIds'))
            """).first?[0] ?? "0") ?? 0
        return counts
    }

    public func bookTags() throws -> [String] {
        try rows("SELECT DISTINCT j.value FROM replica_records r,json_each(r.payload,'$.tags') j WHERE r.collection='fics' AND r.deleted=0 ORDER BY j.value COLLATE NOCASE").map { $0[0] }
    }

    public func bookmarkEdits(bookID: String) throws -> [PendingEdit] {
        try edits().filter { $0.operation.collection == "fic_bookmarks" && $0.original.data?["ficId"]?.string == bookID }
    }

    public func bookmarks(bookID: String) throws -> [SyncChange] {
        var saved = try serverBookmarks(bookID: bookID)
        for edit in try bookmarkEdits(bookID: bookID) where edit.state == "pending" {
            if edit.operation.action == "delete" { saved.removeAll { $0.id == edit.operation.recordId } }
            else {
                if edit.original.data?["type"]?.string == "continue" { saved.removeAll { $0.data?["type"]?.string == "continue" } }
                saved.append(edit.original)
            }
        }
        return saved
    }

    /// The bookmarks as the server last sent them, without edits waiting here.
    private func serverBookmarks(bookID: String) throws -> [SyncChange] {
        var saved = try relatedRecords(collection: "fic_bookmarks", field: "ficId", value: bookID)
        // A continue replacement and its tombstone can arrive on separate pages.
        if let latest = saved.filter({ $0.data?["type"]?.string == "continue" }).max(by: { $0.revision < $1.revision }) {
            saved.removeAll { $0.data?["type"]?.string == "continue" && $0.id != latest.id }
        }
        return saved
    }

    /// A book has one continue point, so a new one replaces any that hasn't
    /// synced yet (or was held as a conflict) rather than waiting for it: the
    /// newest choice is the only one worth sending. It always names the
    /// server's continue point as the one it replaces, never the unsent one.
    public func queueBookmark(chapter: SyncChange, type: String, fraction: Double) throws {
        guard let epoch = try epoch, chapter.collection == "fic_chapters", !chapter.deleted,
              let bookID = chapter.data?["ficId"]?.string, ["favorite", "continue"].contains(type),
              fraction.isFinite, (0...1).contains(fraction) else { throw ReplicaError.invalidEdit }
        let waiting = try bookmarkEdits(bookID: bookID)
        guard type == "continue" || !waiting.contains(where: {
            $0.original.data?["type"]?.string == "favorite" && $0.original.data?["chapterId"]?.string == chapter.id
        }) else { throw ReplicaError.editAlreadyPending }
        let replaced = type == "continue" ? waiting.filter { $0.original.data?["type"]?.string == "continue" } : []
        let previous = type == "continue" ? try serverBookmarks(bookID: bookID).first { $0.data?["type"]?.string == "continue" } : nil
        let id = ULID.make()
        let original = SyncChange(revision: previous?.revision ?? 0, collection: "fic_bookmarks", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(bookID), "chapterId": .string(chapter.id),
                   "type": .string(type), "scrollPosition": .number(fraction)])
        let data: [String: JSONValue] = ["ficId": .string(bookID), "chapterId": .string(chapter.id),
            "type": .string(type), "scrollPosition": .number(fraction),
            "previousContinueId": previous.map { .string($0.id) } ?? .null]
        let operation = ReplicaOperation(epoch: epoch, record: original, action: "create", data: data)
        try transaction {
            for edit in replaced { try execute("DELETE FROM replica_outbox WHERE id=?", [edit.id]) }
            try execute("INSERT INTO replica_outbox(id,operation,original) VALUES (?,?,?)",
                        [operation.id, try json(operation), try json(original)])
        }
    }

    public func deleteBookmark(_ bookmark: SyncChange) throws {
        guard let epoch = try epoch, bookmark.collection == "fic_bookmarks", !bookmark.deleted,
              bookmark.revision > 0, let bookID = bookmark.data?["ficId"]?.string else { throw ReplicaError.invalidEdit }
        guard !(try bookmarkEdits(bookID: bookID)).contains(where: {
            $0.operation.recordId == bookmark.id ||
            (bookmark.data?["type"]?.string == "continue" && $0.original.data?["type"]?.string == "continue")
        }) else { throw ReplicaError.editAlreadyPending }
        let operation = ReplicaOperation(epoch: epoch, record: bookmark, action: "delete", data: [:])
        try execute("INSERT INTO replica_outbox(id,operation,original) VALUES (?,?,?)",
                    [operation.id, try json(operation), try json(bookmark)])
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
            try repointContinue(after: change)
        }
    }

    /// A continue point replaced while its predecessor was already on its way
    /// still names the server's older one, which the predecessor has just
    /// replaced, so it would come back as a conflict. Point it at the one the
    /// server now has instead.
    private func repointContinue(after change: SyncChange) throws {
        guard change.collection == "fic_bookmarks", change.data?["type"]?.string == "continue",
              let bookID = change.data?["ficId"]?.string else { return }
        for edit in try bookmarkEdits(bookID: bookID) where edit.state == "pending" && edit.operation.action == "create"
            && edit.original.data?["type"]?.string == "continue" && edit.operation.recordId != change.id
            && edit.operation.data["previousContinueId"]?.string != change.id {
            guard var operation = try JSONSerialization.jsonObject(with: Data(try json(edit.operation).utf8)) as? [String: Any],
                  var data = operation["data"] as? [String: Any],
                  var original = try JSONSerialization.jsonObject(with: Data(try json(edit.original).utf8)) as? [String: Any]
            else { continue }
            data["previousContinueId"] = change.id
            operation["data"] = data
            operation["baseRevision"] = change.revision
            original["revision"] = change.revision
            try execute("UPDATE replica_outbox SET operation=?,original=? WHERE id=?",
                        [String(decoding: try JSONSerialization.data(withJSONObject: operation), as: UTF8.self),
                         String(decoding: try JSONSerialization.data(withJSONObject: original), as: UTF8.self), edit.id])
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
        // Seen by the server's current history, so a running bootstrap keeps it.
        try execute("DELETE FROM replica_sweep WHERE collection=? AND id=?", [change.collection, change.id])
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
