import XCTest
@testable import LunaschalCore

final class FicLibraryTests: XCTestCase {
    private let epoch = ULID.make()
    private let folder = ULID.make()

    private func store() throws -> (ReplicaStore, URL) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("replica.sqlite")
        return (try ReplicaStore(url: url), url)
    }

    private func book(_ index: Int, shelf: String? = nil, rating: Int? = nil, unread: Int = 0) -> SyncChange {
        let id = ULID.make()
        var data: [String: JSONValue] = [
            "id": .string(id), "title": .string("Book \(index)"), "latestActivity": .number(Double(index)),
            "folderIds": .array([]), "tags": .array([]), "unreadCount": .number(Double(unread)),
        ]
        if let shelf { data["shelf"] = .string(shelf) }
        if let rating { data["rating"] = .number(Double(rating)) }
        return SyncChange(revision: Int64(index + 1), collection: "fics", id: id, deleted: false, data: data)
    }

    private func chapter(_ book: SyncChange, position: Int, category: String = "threadmarks") -> SyncChange {
        let id = ULID.make()
        return SyncChange(revision: 100 + Int64(position), collection: "fic_chapters", id: id, deleted: false,
            data: ["id": .string(id), "ficId": .string(book.id), "title": .string("Chapter \(position)"),
                   "position": .number(Double(position)), "category": .string(category)])
    }

    private func apply(_ changes: [SyncChange], to store: ReplicaStore, bootstrap: Bool = true) throws {
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: bootstrap ? "bootstrap" : "delta",
            changes: changes, hasMore: false, cursor: ULID.make(),
            collections: Array(Set(changes.map(\.collection)))), startingBootstrap: bootstrap)
    }

    private func ids(_ store: ReplicaStore, _ change: (inout BookFilter) -> Void = { _ in }) throws -> [String] {
        var filter = BookFilter()
        change(&filter)
        return try store.books(filter: filter, limit: 100).records.map(\.id)
    }

    func testTheDefaultListHidesTheArchiveAndTheShelfFilterFindsIt() throws {
        let (store, _) = try store()
        let reading = book(1), legacy = book(2, shelf: nil), dropped = book(3, shelf: "dropped"),
            finished = book(4, shelf: "finished"), paused = book(5, shelf: "paused")
        try apply([reading, legacy, dropped, finished, paused], to: store)
        XCTAssertEqual(Set(try ids(store)), [reading.id, legacy.id, paused.id])
        XCTAssertEqual(Set(try ids(store) { $0.shelf = FicLibrary.archivedShelves }), [dropped.id, finished.id])
        XCTAssertEqual(try ids(store) { $0.shelf = "paused" }, [paused.id])
        XCTAssertEqual(try ids(store) { $0.shelf = FicLibrary.allShelves }.count, 5)
    }

    func testDroppingOfflineMovesTheBookToTheArchiveAtOnceAndSurvivesReopen() throws {
        let (store, url) = try store()
        let book = book(1)
        try apply([book], to: store)
        try store.queueFicEdit(bookID: book.id, ["rating": .number(2), "review": .string("Lost me."), "shelf": .string("dropped")])
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try ids(reopened), [])
        let archived = try reopened.books(filter: { var f = BookFilter(); f.shelf = FicLibrary.archivedShelves; return f }()).records
        XCTAssertEqual(archived.map(\.id), [book.id])
        XCTAssertEqual(archived.first?.data?["review"]?.string, "Lost me.")
        XCTAssertEqual(FicLibrary.Shelf(book: archived[0]), .dropped)
    }

    func testASecondEditMergesIntoOneOperationUnderANewId() throws {
        let (store, _) = try store()
        let book = book(1)
        try apply([book], to: store)
        try store.queueFicEdit(bookID: book.id, ["rating": .number(4)])
        let first = try XCTUnwrap(store.edits().first)
        try store.queueFicEdit(bookID: book.id, ["shelf": .string("finished")])
        let edits = try store.edits()
        XCTAssertEqual(edits.count, 1)
        XCTAssertNotEqual(edits[0].id, first.id)
        XCTAssertEqual(edits[0].operation.data, ["rating": .number(4), "shelf": .string("finished")])
    }

    func testFolderFilterAndCountsFollowAWaitingFolderEdit() throws {
        let (store, _) = try store()
        let book = book(1)
        try apply([book], to: store)
        try store.queueFicEdit(bookID: book.id, ["folderIds": .array([.string(folder)])])
        XCTAssertEqual(try ids(store) { $0.folder = folder }, [book.id])
        XCTAssertEqual(try ids(store) { $0.folder = "unsorted" }, [])
        XCTAssertEqual(try store.folderCounts()[folder], 1)
    }

    func testRatingFilterAndSort() throws {
        let (store, _) = try store()
        let unrated = book(1), good = book(2, rating: 4), great = book(3, rating: 5)
        try apply([unrated, good, great], to: store)
        XCTAssertEqual(try ids(store) { $0.sort = "rating" }, [great.id, good.id, unrated.id])
        XCTAssertEqual(try ids(store) { $0.minRating = 4; $0.sort = "rating" }, [great.id, good.id])
        try store.queueFicEdit(bookID: unrated.id, ["rating": .number(5)])
        XCTAssertEqual(Set(try ids(store) { $0.minRating = 5 }), [unrated.id, great.id])
    }

    func testInvalidEditsAreRefused() throws {
        let (store, _) = try store()
        let book = book(1)
        try apply([book], to: store)
        XCTAssertThrowsError(try store.queueFicEdit(bookID: book.id, ["shelf": .string("archived")]))
        XCTAssertThrowsError(try store.queueFicEdit(bookID: book.id, ["rating": .number(9)]))
        XCTAssertThrowsError(try store.queueFicEdit(bookID: book.id, ["title": .string("x")]))
        XCTAssertNoThrow(try store.queueFicEdit(bookID: book.id, ["rating": .null]))
    }

    func testReadMarksOverlayTheServerAndMoveTheUnreadCount() throws {
        let (store, _) = try store()
        let book = book(1, unread: 2)
        let first = chapter(book, position: 0), second = chapter(book, position: 1)
        let serverRead = SyncChange(revision: 200, collection: "fic_chapter_reads", id: first.id, deleted: false,
                                    data: ["id": .string(first.id), "chapterId": .string(first.id), "ficId": .string(book.id), "createdAt": .number(1)])
        try apply([book, first, second, serverRead], to: store)
        XCTAssertEqual(try store.readChapterIDs(bookID: book.id), [first.id])
        try store.setChaptersRead(bookID: book.id, chapterIDs: [first.id, second.id], read: true)
        // Only the chapter the server doesn't already have is sent.
        XCTAssertEqual(try store.edits().map(\.operation.recordId), [second.id])
        XCTAssertEqual(try store.readChapterIDs(bookID: book.id), [first.id, second.id])
        XCTAssertEqual(try ids(store).count, 1)
        XCTAssertEqual(try store.books(filter: BookFilter()).records[0].data?["unreadCount"]?.int, 1)
        // Unmarking a mark that hasn't synced withdraws it; nothing goes out.
        try store.setChaptersRead(bookID: book.id, chapterIDs: [second.id], read: false)
        XCTAssertTrue(try store.edits().isEmpty)
        try store.setChaptersRead(bookID: book.id, chapterIDs: [first.id], read: false)
        let delete = try XCTUnwrap(store.edits().first)
        XCTAssertEqual(delete.operation.action, "delete")
        XCTAssertEqual(delete.operation.baseRevision, 200)
        XCTAssertEqual(try store.readChapterIDs(bookID: book.id), [])
    }

    func testARefusedLibraryEditIsDroppedNotHeldForReview() throws {
        let (store, _) = try store()
        let book = book(1)
        try apply([book], to: store)
        try store.queueFicEdit(bookID: book.id, ["rating": .number(3)])
        let edit = try XCTUnwrap(store.edits().first)
        try store.hold(edit.operation, reply: OperationReply(operationId: edit.id, change: nil, conflict: true,
                                                             current: nil, error: "gone", resetRequired: nil))
        XCTAssertTrue(try store.edits().isEmpty)
    }

    func testReactionsAndTheirSummary() throws {
        let (store, _) = try store()
        let book = book(1)
        let a = chapter(book, position: 0), b = chapter(book, position: 1)
        func reaction(_ chapter: SyncChange, _ name: String?, at: Double) -> SyncChange {
            SyncChange(revision: 300 + Int64(at), collection: "fic_chapter_reactions", id: chapter.id, deleted: false,
                       data: ["id": .string(chapter.id), "chapterId": .string(chapter.id), "ficId": .string(book.id),
                              "reaction": name.map(JSONValue.string) ?? .null, "checkedAt": .number(at)])
        }
        try apply([book, a, b, reaction(a, "Like", at: 10), reaction(b, nil, at: 20)], to: store)
        let found = try store.reactions(bookID: book.id)
        XCTAssertEqual(found[a.id]?.reaction, "Like")
        XCTAssertNil(found[b.id]?.reaction)
        XCTAssertEqual(found[b.id]?.checkedAt, Date(timeIntervalSince1970: 20))
        XCTAssertEqual(try store.reactionSummaries(bookIDs: [book.id])[book.id],
                       ReactionSummary(reacted: 1, checked: 2, lastChecked: Date(timeIntervalSince1970: 20)))
    }

    func testAutoReadAndMarkUpToHere() {
        XCTAssertTrue(FicLibrary.shouldAutoMark(fraction: 0.96, alreadyRead: false))
        XCTAssertFalse(FicLibrary.shouldAutoMark(fraction: 0.9, alreadyRead: false))
        XCTAssertFalse(FicLibrary.shouldAutoMark(fraction: 1, alreadyRead: true))
        let book = book(1)
        let outline = [chapter(book, position: 0), chapter(book, position: 1, category: "sidestory"),
                       chapter(book, position: 2), chapter(book, position: 3)]
        XCTAssertEqual(FicLibrary.chapters(through: outline[2].id, in: outline), [outline[0].id, outline[2].id])
        XCTAssertEqual(FicLibrary.chapters(through: "missing", in: outline), [])
        XCTAssertTrue(FicLibrary.isMainStory(chapter(book, position: 0, category: "Threadmarks")))
        XCTAssertFalse(FicLibrary.isMainStory(chapter(book, position: 0, category: "Sidestory")))
    }
}
