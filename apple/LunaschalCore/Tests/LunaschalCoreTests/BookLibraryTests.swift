import XCTest
@testable import LunaschalCore

final class BookLibraryTests: XCTestCase {
    private let epoch = ULID.make()
    private let folder = ULID.make()

    private func store() throws -> (ReplicaStore, URL) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("replica.sqlite")
        return (try ReplicaStore(url: url), url)
    }

    private func book(_ index: Int, title: String? = nil, filed: Bool = true) -> SyncChange {
        let id = ULID.make()
        return SyncChange(revision: Int64(index + 1), collection: "fics", id: id, deleted: false,
            data: ["id": .string(id), "title": .string(title ?? "Book \(index)"), "sourceType": .string("xenforo"),
                   "site": .string("forums.spacebattles.com"), "latestActivity": .number(Double(index)),
                   "folderIds": .array(filed ? [.string(folder)] : []), "tags": .array([.string("Magic")])])
    }

    private func chapter(_ book: SyncChange) -> SyncChange {
        let id = ULID.make()
        return SyncChange(revision: 100, collection: "fic_chapters", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(book.id), "title": .string("Chapter")])
    }

    private func chapter(_ book: SyncChange, position: Int) -> SyncChange {
        let id = ULID.make()
        return SyncChange(revision: 100 + Int64(position), collection: "fic_chapters", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(book.id), "title": .string("Chapter \(position)"),
                   "position": .number(Double(position))])
    }

    private func revised(_ record: SyncChange, _ changes: [String: JSONValue]) -> SyncChange {
        SyncChange(revision: record.revision + 1000, collection: record.collection, id: record.id, deleted: false,
                   data: (record.data ?? [:]).merging(changes) { $1 })
    }

    func testResumePointFollowsTheDesktopsOrder() throws {
        let (store, _) = try store()
        let book = book(1)
        let first = chapter(book, position: 0), second = chapter(book, position: 1), third = chapter(book, position: 2)
        try apply([book, third, second, first], to: store)
        // Nothing read anywhere: the first chapter, by position not arrival.
        XCTAssertEqual(try store.resumePoint(bookID: book.id)?.chapter.id, first.id)
        // The server's last-read chapter beats the first.
        try apply([revised(book, ["lastReadChapterId": .string(second.id)])], to: store, bootstrap: false)
        XCTAssertEqual(try store.resumePoint(bookID: book.id)?.chapter.id, second.id)
        // This device's own reading beats the server's.
        try store.saveReadingPosition(collection: "fic_chapters", id: third.id, version: "v", offset: 2, bookID: book.id)
        XCTAssertEqual(try store.resumePoint(bookID: book.id)?.chapter.id, third.id)
        XCTAssertNil(try store.resumePoint(bookID: book.id)?.fraction)
        // A continue bookmark beats both, at its position, while still pending.
        try store.queueBookmark(chapter: first, type: "continue", fraction: 0.4)
        let resume = try XCTUnwrap(store.resumePoint(bookID: book.id))
        XCTAssertEqual(resume.chapter.id, first.id)
        XCTAssertEqual(resume.fraction, 0.4)
    }

    func testChapterCountsCoverAPageOfBooksAndTextBytesCountTheirChapters() throws {
        let (store, _) = try store()
        let whole = book(1), partial = book(2), none = book(3)
        try apply([whole, partial, none, chapter(whole, position: 0), chapter(whole, position: 1),
                   chapter(partial, position: 0)], to: store)
        let counts = try store.chapterCounts(bookIDs: [whole.id, partial.id, none.id])
        XCTAssertEqual(counts, [whole.id: 2, partial.id: 1])
        let books = try store.storedBytes(collections: ["fics"])
        let all = try store.storedBytes(collections: ["fics", "fic_chapters"])
        XCTAssertGreaterThan(books, 0)
        XCTAssertGreaterThan(all, books, "chapter text counts toward what is downloaded")
        XCTAssertEqual(try store.storedBytes(collections: []), 0)
    }

    func testResumePointIsNilWithoutChapterText() throws {
        let (store, _) = try store()
        let pdf = book(1)
        try apply([pdf], to: store)
        XCTAssertNil(try store.resumePoint(bookID: pdf.id))
    }

    func testFolderCountsIncludeUnsorted() throws {
        let (store, _) = try store()
        try apply([book(1), book(2), book(3, filed: false)], to: store)
        XCTAssertEqual(try store.folderCounts(), [folder: 2, "unsorted": 1])
    }

    func testAProviderShowsItsBooksByLatestSiteActivity() throws {
        let (store, _) = try store()
        let older = book(1), newer = book(9)
        let ao3 = revised(book(5), ["site": .string("ao3")])
        try apply([older, ao3, newer], to: store)
        var filter = BookFilter()
        filter.source = "forums.spacebattles.com"
        XCTAssertEqual(try store.books(filter: filter).records.map(\.id), [newer.id, older.id])
        filter.source = "ao3"
        XCTAssertEqual(try store.books(filter: filter).records.map(\.id), [ao3.id])
    }

    private func apply(_ changes: [SyncChange], to store: ReplicaStore, bootstrap: Bool = true) throws {
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: bootstrap ? "bootstrap" : "delta",
            changes: changes, hasMore: false, cursor: ULID.make(),
            collections: Array(Set(changes.map(\.collection)))), startingBootstrap: bootstrap)
    }

    func testCombinedTitleTagSourceFolderFiltersAndPaginationUseSameCount() throws {
        let (store, _) = try store()
        let books = (0..<61).map { book($0) } + [book(80, title: "100% literal", filed: false)]
        try apply(books, to: store)
        var filter = BookFilter()
        filter.query = "ook Mag" // substring matching, with words split across title/tag
        filter.source = "forums.spacebattles.com"
        filter.folder = folder
        filter.tag = "Magic"
        let first = try store.books(filter: filter)
        XCTAssertEqual(first.count, 61)
        XCTAssertEqual(first.records.count, 50)
        XCTAssertEqual(first.records.first?.id, books[60].id)
        XCTAssertEqual(try store.books(filter: filter, limit: 100).records.count, 61)
        filter.source = "pdf"
        XCTAssertEqual(try store.books(filter: filter).count, 0)
        filter = BookFilter()
        filter.folder = "unsorted"
        filter.query = "%"
        XCTAssertEqual(try store.books(filter: filter).records.map(\.id), [books[61].id])
        XCTAssertEqual(try store.bookTags(), ["Magic"])
    }

    func testRecentlyReadUsesLocalProgressWithoutChangingActivityOrder() throws {
        let (store, _) = try store()
        let old = book(1), new = book(2)
        let chapter = chapter(old)
        try apply([old, new, chapter], to: store)
        try store.saveReadingPosition(collection: "fic_chapters", id: chapter.id, version: "v1", offset: 3, bookID: old.id)
        var recent = BookFilter()
        recent.sort = "recent"
        XCTAssertEqual(try store.books(filter: recent).records.first?.id, old.id)
        XCTAssertEqual(try store.books(filter: BookFilter()).records.first?.id, new.id)
        try store.markBookOpened(new.id)
        XCTAssertEqual(try store.books(filter: recent).records.first?.id, new.id)
    }

    func testOfflineFavoriteSurvivesReopenAndAcknowledgementWithoutDuplicates() throws {
        let (store, url) = try store()
        let book = book(1), chapter = chapter(book)
        try apply([book, chapter], to: store)
        try store.queueBookmark(chapter: chapter, type: "favorite", fraction: 0.5)
        let reopened = try ReplicaStore(url: url)
        let edit = try XCTUnwrap(reopened.bookmarkEdits(bookID: book.id).first)
        XCTAssertEqual(try reopened.bookmarks(bookID: book.id).count, 1)
        var favorites = BookFilter(); favorites.bookmark = "favorite"
        XCTAssertEqual(try reopened.books(filter: favorites).records.map(\.id), [book.id])
        let change = SyncChange(revision: 101, collection: "fic_bookmarks", id: edit.operation.recordId,
                                deleted: false, data: edit.original.data)
        let reply = OperationReply(operationId: edit.id, change: change, conflict: nil, current: nil, error: nil, resetRequired: nil)
        try reopened.acknowledge(edit.operation, reply: reply)
        XCTAssertTrue(try reopened.bookmarkEdits(bookID: book.id).isEmpty)
        XCTAssertEqual(try reopened.bookmarks(bookID: book.id).map(\.id), [change.id])
        try reopened.deleteBookmark(change)
        XCTAssertTrue(try reopened.bookmarks(bookID: book.id).isEmpty)
        XCTAssertEqual(try reopened.books(filter: favorites).count, 0)
    }

    func testContinueReplacementCarriesRevisionAndANewerOneReplacesItBeforeSync() throws {
        let (store, _) = try store()
        let book = book(1), current = chapter(book), later = chapter(book)
        let id = ULID.make()
        let saved = SyncChange(revision: 105, collection: "fic_bookmarks", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(book.id), "chapterId": .string(current.id),
                   "type": .string("continue"), "scrollPosition": .number(0)])
        try apply([book, current, later, saved], to: store)
        try store.queueBookmark(chapter: current, type: "continue", fraction: 0.8)
        let edit = try XCTUnwrap(store.bookmarkEdits(bookID: book.id).first)
        XCTAssertEqual(edit.operation.baseRevision, 105)
        XCTAssertEqual(edit.operation.data["previousContinueId"]?.string, id)
        XCTAssertEqual(try store.bookmarks(bookID: book.id).count, 1)

        // Moving it again before any sync replaces the unsent one, and still
        // names the server's continue point, not the one that never left.
        try store.queueBookmark(chapter: later, type: "continue", fraction: 0.3)
        let edits = try store.bookmarkEdits(bookID: book.id)
        XCTAssertEqual(edits.count, 1)
        let newer = try XCTUnwrap(edits.first)
        XCTAssertNotEqual(newer.id, edit.id)
        XCTAssertEqual(newer.operation.data["chapterId"]?.string, later.id)
        XCTAssertEqual(newer.operation.data["scrollPosition"]?.number, 0.3)
        XCTAssertEqual(newer.operation.baseRevision, 105)
        XCTAssertEqual(newer.operation.data["previousContinueId"]?.string, id)
        XCTAssertEqual(try store.bookmarks(bookID: book.id).map { $0.data?["chapterId"]?.string }, [later.id])

        // One held as a conflict is replaced too, by a newer choice.
        try store.hold(newer.operation, reply: OperationReply(operationId: nil, change: nil, conflict: true,
            current: saved, error: "Changed elsewhere", resetRequired: nil))
        XCTAssertEqual(try store.bookmarks(bookID: book.id).first?.id, id)
        try store.queueBookmark(chapter: current, type: "continue", fraction: 0.5)
        let afterConflict = try store.bookmarkEdits(bookID: book.id)
        XCTAssertEqual(afterConflict.map(\.state), ["pending"])
        try store.resolve(try XCTUnwrap(afterConflict.first), keepLocal: false)
        XCTAssertTrue(try store.bookmarkEdits(bookID: book.id).isEmpty)
    }

    func testABooksChaptersAreFoundThroughAnIndexNotByReadingEveryChapter() throws {
        let (store, _) = try store()
        let book = book(1)
        try apply([book] + (0..<3).map { chapter(book, position: $0) }, to: store)
        for field in ReplicaStore.relatedFields {
            let plan = try store.relatedQueryPlan(collection: "fic_chapters", field: field, value: book.id)
            XCTAssertTrue(plan.contains("replica_by_\(field)"), "\(field): \(plan)")
        }
        XCTAssertEqual(try store.relatedRecords(collection: "fic_chapters", field: "ficId", value: book.id).count, 3)
        XCTAssertEqual(try store.relatedCount(collection: "fic_chapters", field: "ficId", value: book.id), 3)
        XCTAssertEqual(try store.relatedRecords(collection: "fic_chapters", field: "ficId", values: [book.id]).count, 3)
        XCTAssertThrowsError(try store.relatedRecords(collection: "fic_chapters", field: "title", value: "x"))
    }

    func testAContinueReplacedWhileItsPredecessorWasSendingFollowsWhatTheServerKept() throws {
        let (store, _) = try store()
        let book = book(1), first = chapter(book), second = chapter(book)
        try apply([book, first, second], to: store)
        try store.queueBookmark(chapter: first, type: "continue", fraction: 0.2)
        // The sync pass has already sent this one when the reader moves on.
        let sending = try XCTUnwrap(store.bookmarkEdits(bookID: book.id).first)
        try store.queueBookmark(chapter: second, type: "continue", fraction: 0.6)
        XCTAssertEqual(try store.bookmarkEdits(bookID: book.id).first?.operation.data["previousContinueId"], .null)

        let kept = SyncChange(revision: 200, collection: "fic_bookmarks", id: sending.operation.recordId,
                              deleted: false, data: sending.original.data)
        try store.acknowledge(sending.operation, reply: OperationReply(operationId: sending.id, change: kept,
            conflict: nil, current: nil, error: nil, resetRequired: nil))

        let newer = try XCTUnwrap(store.bookmarkEdits(bookID: book.id).first)
        XCTAssertEqual(newer.operation.data["chapterId"]?.string, second.id)
        XCTAssertEqual(newer.operation.data["previousContinueId"]?.string, kept.id)
        XCTAssertEqual(newer.operation.baseRevision, 200)
        XCTAssertEqual(newer.original.revision, 200)
        XCTAssertEqual(try store.bookmarks(bookID: book.id).map { $0.data?["chapterId"]?.string }, [second.id])
    }

    func testSeveralFavoritesCanBeRemovedOfflineWithoutDuplicateDeletes() throws {
        let (store, _) = try store()
        let book = book(1), chapter = chapter(book)
        let favorites = (0..<2).map { index -> SyncChange in
            let id = ULID.make()
            return SyncChange(revision: Int64(101 + index), collection: "fic_bookmarks", id: id, deleted: false,
                data: ["id": .string(id), "ficId": .string(book.id), "chapterId": .string(chapter.id),
                       "type": .string("favorite"), "scrollPosition": .number(Double(index))])
        }
        try apply([book, chapter] + favorites, to: store)
        for favorite in favorites { try store.deleteBookmark(favorite) }
        XCTAssertTrue(try store.bookmarks(bookID: book.id).isEmpty)
        XCTAssertEqual(try store.bookmarkEdits(bookID: book.id).count, 2)
        XCTAssertThrowsError(try store.deleteBookmark(favorites[0]))
    }
}
