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

    func testContinueReplacementCarriesRevisionAndPendingChangeCannotBeOverwritten() throws {
        let (store, _) = try store()
        let book = book(1), chapter = chapter(book)
        let id = ULID.make()
        let saved = SyncChange(revision: 105, collection: "fic_bookmarks", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(book.id), "chapterId": .string(chapter.id),
                   "type": .string("continue"), "scrollPosition": .number(0)])
        try apply([book, chapter, saved], to: store)
        try store.queueBookmark(chapter: chapter, type: "continue", fraction: 0.8)
        let edit = try XCTUnwrap(store.bookmarkEdits(bookID: book.id).first)
        XCTAssertEqual(edit.operation.baseRevision, 105)
        XCTAssertEqual(edit.operation.data["previousContinueId"]?.string, id)
        XCTAssertEqual(try store.bookmarks(bookID: book.id).count, 1)
        XCTAssertThrowsError(try store.queueBookmark(chapter: chapter, type: "continue", fraction: 0.9))
        try store.hold(edit.operation, reply: OperationReply(operationId: nil, change: nil, conflict: true,
            current: saved, error: "Changed elsewhere", resetRequired: nil))
        XCTAssertEqual(try store.bookmarks(bookID: book.id).first?.id, id)
        try store.resolve(try XCTUnwrap(store.bookmarkEdits(bookID: book.id).first), keepLocal: false)
        XCTAssertTrue(try store.bookmarkEdits(bookID: book.id).isEmpty)
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
