import Foundation

/// Organizing the library from the phone: a book's rating, review, shelf and
/// folders, which of its chapters have been read, and what the forum showed
/// as the user's own reaction on each chapter (the proof it was read).
///
/// Edits go through the replica outbox like bookmarks do, and are last write
/// wins on the server (`backend/mobile_sync/fic_edits.py`): they are one
/// person's choices about their own library, so there is nothing to merge.
public enum FicLibrary {
    /// Replicated per chapter; their own sync scope, so a server without them
    /// never fails the journal's sync.
    public static let trackingCollections = ["fic_chapter_reads", "fic_chapter_reactions"]

    public static func supported(by serverCollections: [String]) -> Bool {
        trackingCollections.allSatisfy(serverCollections.contains)
    }

    /// What the phone may change about a book. Matches `fic_edits.FIC_FIELDS`.
    public static let editableFields: Set<String> = ["rating", "review", "shelf", "folderIds"]

    /// Collections whose refused edits are dropped rather than held for
    /// review: a refusal means the book, chapter or folder is gone.
    static let lastWriteWins: Set<String> = ["fics", "fic_chapter_reads", "fic_chapter_reactions"]

    /// The library filter's pseudo-folder for books on none of the user's own
    /// lists on their site (watched, bookmarked, followed, favorited): read
    /// off each book's `backlog` flag, which the server derives.
    public static let backlogFolder = "backlog"

    /// Whether the server can like this chapter on its forum: a forum book,
    /// and a chapter that came from a post.
    public static func isLikeable(book: SyncChange?, chapter: SyncChange) -> Bool {
        book?.data?["sourceType"]?.string == "xenforo" && chapter.data?["sourceUrl"]?.string?.contains("/post-") == true
    }

    public enum Shelf: String, CaseIterable, Identifiable, Sendable {
        case toRead = "to_read", reading, paused, finished, dropped
        public var id: String { rawValue }

        public var label: String {
            switch self {
            case .toRead: "To read"
            case .reading: "Reading"
            case .paused: "Paused"
            case .finished: "Finished"
            case .dropped: "Dropped"
            }
        }

        public var systemImage: String {
            switch self {
            case .toRead: "tray"
            case .reading: "book"
            case .paused: "pause.circle"
            case .finished: "checkmark.seal"
            case .dropped: "archivebox"
            }
        }

        /// Finished and dropped books are the archive: out of the default list.
        public var isArchived: Bool { self == .finished || self == .dropped }

        public init(book: SyncChange) {
            self = book.data?["shelf"]?.string.flatMap(Shelf.init(rawValue:)) ?? .reading
        }
    }

    /// The shelf filter's choices beyond a single shelf.
    public static let activeShelves = "active"
    public static let archivedShelves = "archived"
    public static let allShelves = "all"

    /// How far down a chapter counts as having read it.
    public static let autoReadFraction = 0.95

    /// Whether reaching `fraction` of a chapter should mark it read, once.
    public static func shouldAutoMark(fraction: Double, alreadyRead: Bool) -> Bool {
        !alreadyRead && fraction >= autoReadFraction
    }

    /// Whether a chapter is the main story, the chapters the unread count
    /// counts: imports store the default lowercase, XenForo the forum's own
    /// "Threadmarks".
    public static func isMainStory(_ chapter: SyncChange) -> Bool {
        (chapter.data?["category"]?.string ?? "threadmarks").lowercased() == "threadmarks"
    }

    /// The chapters from the first through `chapterID`, for "Mark all up to
    /// here read". Empty when the chapter isn't in the outline.
    public static func chapters(through chapterID: String, in outline: [SyncChange]) -> [String] {
        guard let end = outline.firstIndex(where: { $0.id == chapterID }) else { return [] }
        let category = outline[end].data?["category"]?.string
        return outline[...end].filter { $0.data?["category"]?.string == category }.map(\.id)
    }

    /// A book's value of a field with its waiting edit applied, as SQL against
    /// `replica_records`. A book has at most one waiting edit (`queueFicEdit`
    /// merges), so filtering and sorting see what the user just chose.
    static func field(_ key: String) -> String {
        let waiting = """
            FROM replica_outbox o WHERE o.state='pending' AND json_extract(o.operation,'$.collection')='fics'
                AND json_extract(o.operation,'$.recordId')=replica_records.id
                AND json_type(o.operation,'$.data.\(key)') IS NOT NULL
            """
        return """
            (CASE WHEN EXISTS (SELECT 1 \(waiting))
                THEN (SELECT json_extract(o.operation,'$.data.\(key)') \(waiting) ORDER BY o.rowid DESC LIMIT 1)
                ELSE json_extract(replica_records.payload,'$.\(key)') END)
            """
    }
}

/// What the forum showed on one chapter's post. A nil `reaction` is "checked,
/// not reacted"; a chapter with no entry at all has never been checked.
public struct ChapterReaction: Equatable, Sendable {
    public let reaction: String?
    public let checkedAt: Date
    /// "site" (read off the forum), "app" (asked for here) or "assumed"
    /// (opened on the site, where the user presses Like themselves).
    public var source: String? = nil
    /// "like"/"unlike" while the server has still to make it on the forum.
    public var pending: String? = nil
    /// Why the server's last try at making it failed.
    public var error: String? = nil

    public init(reaction: String?, checkedAt: Date, source: String? = nil, pending: String? = nil, error: String? = nil) {
        self.reaction = reaction
        self.checkedAt = checkedAt
        self.source = source
        self.pending = pending
        self.error = error
    }

    public var isLiked: Bool { reaction != nil }
}

/// A book's reaction proof at a glance: how many chapters carry a reaction of
/// the user's, out of how many were checked, and when the newest check was.
public struct ReactionSummary: Equatable, Sendable {
    public let reacted: Int
    public let checked: Int
    public let lastChecked: Date?
}

public extension ReplicaStore {
    /// Queue a change to a book. A change still waiting to sync is merged into
    /// this one and replaced, under a new operation id, so an edit already on
    /// its way is never altered in flight: the server applies the old one,
    /// then this one, which carries everything.
    func queueFicEdit(bookID: String, _ changes: [String: JSONValue]) throws {
        guard let epoch = try epoch else { throw ReplicaError.needsBootstrap }
        guard let book = try record(collection: "fics", id: bookID), !book.deleted, !changes.isEmpty,
              Set(changes.keys).isSubset(of: FicLibrary.editableFields) else { throw ReplicaError.invalidEdit }
        if let shelf = changes["shelf"], shelf.string.flatMap(FicLibrary.Shelf.init(rawValue:)) == nil {
            throw ReplicaError.invalidEdit
        }
        if let rating = changes["rating"], rating != .null, !(1...5).contains(rating.int ?? 0) {
            throw ReplicaError.invalidEdit
        }
        let waiting = try edits().filter { $0.operation.collection == "fics" && $0.operation.recordId == bookID }
        var merged: [String: JSONValue] = [:]
        for edit in waiting where edit.state == "pending" { merged.merge(edit.operation.data) { _, later in later } }
        merged.merge(changes) { _, later in later }
        let operation = ReplicaOperation(epoch: epoch, record: book, action: "update", data: merged)
        try transaction {
            for edit in waiting { try execute("DELETE FROM replica_outbox WHERE id=?", [edit.id]) }
            try execute("INSERT INTO replica_outbox(id,operation,original) VALUES (?,?,?)",
                        [operation.id, try json(operation), try json(book)])
        }
    }

    /// A book as the user last left it: its waiting edit applied, and its
    /// unread count moved by read marks that haven't synced yet.
    func overlaid(_ book: SyncChange) throws -> SyncChange {
        guard book.collection == "fics", var data = book.data else { return book }
        let pending = try edits().filter { $0.state == "pending" }
        if let edit = pending.last(where: { $0.operation.collection == "fics" && $0.operation.recordId == book.id }) {
            data.merge(edit.operation.data) { _, mine in mine }
        }
        let reads = pending.filter {
            $0.operation.collection == "fic_chapter_reads" && $0.operation.data["ficId"]?.string == book.id
        }
        if !reads.isEmpty, let unread = data["unreadCount"]?.int {
            let delta = reads.reduce(0) { $0 + ($1.operation.action == "create" ? -1 : 1) }
            data["unreadCount"] = .number(Double(max(0, unread + delta)))
        }
        return SyncChange(revision: book.revision, collection: book.collection, id: book.id, deleted: book.deleted, data: data)
    }

    /// The chapters of a book marked read, waiting marks included.
    func readChapterIDs(bookID: String) throws -> Set<String> {
        var read = Set(try rows("""
            SELECT id FROM replica_records WHERE collection='fic_chapter_reads' AND deleted=0
            AND json_extract(payload,'$.ficId')=?
            """, [bookID]).map { $0[0] })
        for edit in try edits() where edit.state == "pending" && edit.operation.collection == "fic_chapter_reads"
            && edit.operation.data["ficId"]?.string == bookID {
            if edit.operation.action == "create" { read.insert(edit.operation.recordId) }
            else { read.remove(edit.operation.recordId) }
        }
        return read
    }

    /// Mark chapters read or unread. A mark waiting the other way is
    /// withdrawn, and nothing is sent for a chapter the server already has in
    /// the wanted state.
    func setChaptersRead(bookID: String, chapterIDs: [String], read: Bool) throws {
        guard let epoch = try epoch else { throw ReplicaError.needsBootstrap }
        guard ULID.isValid(bookID), chapterIDs.allSatisfy(ULID.isValid) else { throw ReplicaError.invalidEdit }
        let waiting = Dictionary(grouping: try edits().filter { $0.operation.collection == "fic_chapter_reads" },
                                 by: { $0.operation.recordId })
        try transaction {
            for chapterID in chapterIDs {
                for edit in waiting[chapterID] ?? [] { try execute("DELETE FROM replica_outbox WHERE id=?", [edit.id]) }
                let server = try record(collection: "fic_chapter_reads", id: chapterID)
                guard (server.map { !$0.deleted } ?? false) != read else { continue }
                let base = server ?? SyncChange(revision: 0, collection: "fic_chapter_reads", id: chapterID, deleted: false,
                                                data: ["id": .string(chapterID), "chapterId": .string(chapterID), "ficId": .string(bookID)])
                let operation = ReplicaOperation(epoch: epoch, record: base, action: read ? "create" : "delete",
                                                 data: ["ficId": .string(bookID)])
                try execute("INSERT INTO replica_outbox(id,operation,original) VALUES (?,?,?)",
                            [operation.id, try json(operation), try json(base)])
            }
        }
    }

    /// Each checked chapter's reaction, keyed by chapter id, with likes
    /// still waiting to sync applied.
    func reactions(bookID: String) throws -> [String: ChapterReaction] {
        var found: [String: ChapterReaction] = [:]
        for row in try rows("""
            SELECT id,COALESCE(json_extract(payload,'$.reaction'),''),COALESCE(json_extract(payload,'$.checkedAt'),0),
                json_extract(payload,'$.source'),json_extract(payload,'$.pending'),json_extract(payload,'$.pushError')
            FROM replica_records WHERE collection='fic_chapter_reactions' AND deleted=0
            AND json_extract(payload,'$.ficId')=?
            """, [bookID]) {
            found[row[0]] = ChapterReaction(reaction: row[1].isEmpty ? nil : row[1],
                                            checkedAt: Date(timeIntervalSince1970: Double(row[2]) ?? 0),
                                            source: row[3].isEmpty ? nil : row[3],
                                            pending: row[4].isEmpty ? nil : row[4],
                                            error: row[5].isEmpty ? nil : row[5])
        }
        for edit in try edits() where edit.state == "pending" && edit.operation.collection == "fic_chapter_reactions"
            && edit.operation.data["ficId"]?.string == bookID {
            found[edit.operation.recordId] = Self.applying(edit.operation.data, to: found[edit.operation.recordId])
        }
        return found
    }

    /// What a like the user just asked for makes of a chapter's reaction, the
    /// same rules the server applies (`backend/fanfic/likes.py`).
    static func applying(_ like: [String: JSONValue], to current: ChapterReaction?) -> ChapterReaction? {
        let liked = like["liked"]?.bool ?? false
        if like["assumed"]?.bool == true {
            guard liked, current?.reaction == nil, current?.pending == nil else { return current }
            return ChapterReaction(reaction: "Like", checkedAt: current?.checkedAt ?? Date(), source: "assumed")
        }
        return ChapterReaction(reaction: liked ? (current?.reaction ?? "Like") : nil,
                               checkedAt: current?.checkedAt ?? Date(), source: "app",
                               pending: liked ? "like" : "unlike")
    }

    /// Like or un-like a forum chapter. `assumed` is "opened on the site": it
    /// shows the chapter liked and asks the server to send nothing, and is
    /// dropped outright when the chapter already shows a reaction. A like
    /// still waiting is replaced, so only the newest tap is ever sent.
    func setChapterLiked(bookID: String, chapterID: String, liked: Bool, assumed: Bool = false) throws {
        guard let epoch = try epoch else { throw ReplicaError.needsBootstrap }
        guard ULID.isValid(bookID), ULID.isValid(chapterID) else { throw ReplicaError.invalidEdit }
        if assumed, try reactions(bookID: bookID)[chapterID]?.reaction != nil { return }
        let waiting = try edits().filter {
            $0.state == "pending" && $0.operation.collection == "fic_chapter_reactions" && $0.operation.recordId == chapterID
        }
        let server = try record(collection: "fic_chapter_reactions", id: chapterID)
        let base = server ?? SyncChange(revision: 0, collection: "fic_chapter_reactions", id: chapterID, deleted: false,
                                        data: ["id": .string(chapterID), "chapterId": .string(chapterID), "ficId": .string(bookID)])
        let operation = ReplicaOperation(epoch: epoch, record: base, action: "update",
                                         data: ["ficId": .string(bookID), "liked": .bool(liked), "assumed": .bool(assumed)])
        try transaction {
            for edit in waiting { try execute("DELETE FROM replica_outbox WHERE id=?", [edit.id]) }
            try execute("INSERT INTO replica_outbox(id,operation,original) VALUES (?,?,?)",
                        [operation.id, try json(operation), try json(base)])
        }
    }

    /// Reaction proof per book, for the library list.
    func reactionSummaries(bookIDs: [String]) throws -> [String: ReactionSummary] {
        guard !bookIDs.isEmpty else { return [:] }
        let marks = Array(repeating: "?", count: bookIDs.count).joined(separator: ",")
        var found: [String: ReactionSummary] = [:]
        for row in try rows("""
            SELECT json_extract(payload,'$.ficId'),
                SUM(json_extract(payload,'$.reaction') IS NOT NULL),COUNT(*),MAX(json_extract(payload,'$.checkedAt'))
            FROM replica_records WHERE collection='fic_chapter_reactions' AND deleted=0
            AND json_extract(payload,'$.ficId') IN (\(marks)) GROUP BY 1
            """, bookIDs) {
            found[row[0]] = ReactionSummary(reacted: Int(row[1]) ?? 0, checked: Int(row[2]) ?? 0,
                                            lastChecked: Double(row[3]).map(Date.init(timeIntervalSince1970:)))
        }
        return found
    }
}
