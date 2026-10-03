import Foundation
import XCTest
@testable import LunaschalCore

final class ReadingPositionTests: XCTestCase {
    func testPositionSurvivesReopenButNeverCarriesAcrossChangedContent() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("replica.sqlite")
        let store = try ReplicaStore(url: url)
        let id = ULID.make()
        try store.saveReadingPosition(collection: "fics", id: id, version: "pdf-digest", offset: 12)
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.readingPosition(collection: "fics", id: id, version: "pdf-digest"), 12)
        XCTAssertNil(try reopened.readingPosition(collection: "fics", id: id, version: "replacement-digest"))
        XCTAssertNil(try reopened.readingPosition(collection: "study_sources", id: id, version: "pdf-digest"))
        XCTAssertThrowsError(try reopened.saveReadingPosition(collection: "fics", id: id, version: "pdf-digest", offset: -1))
        XCTAssertEqual(try reopened.readingPosition(collection: "fics", id: id, version: "pdf-digest"), 12)
    }

    func testContinueChapterIsScopedToBookAndDoesNotResurrectDeletedChapters() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("replica.sqlite")
        let store = try ReplicaStore(url: url)
        let book = ULID.make(), chapterID = ULID.make(), epoch = ULID.make()
        let chapter = SyncChange(revision: 1, collection: "fic_chapters", id: chapterID, deleted: false,
            data: ["id": .string(chapterID), "ficId": .string(book), "contentText": .string("Paragraph one\n\nParagraph two")])
        try store.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [chapter],
            hasMore: false, cursor: "one", collections: ["fic_chapters"]), startingBootstrap: true)
        try store.saveReadingPosition(collection: "fic_chapters", id: chapterID, version: epoch + ":1", offset: 1, bookID: book)
        XCTAssertThrowsError(try store.saveReadingPosition(collection: "fic_chapters", id: chapterID, version: epoch + ":1", offset: 0, bookID: ULID.make()))
        let reopened = try ReplicaStore(url: url)
        XCTAssertEqual(try reopened.lastReadChapter(bookID: book), chapter)
        XCTAssertEqual(try reopened.readingPosition(collection: "fic_chapters", id: chapterID, version: epoch + ":1"), 1)
        try reopened.resetCursors()
        XCTAssertEqual(try reopened.lastReadChapter(bookID: book), chapter)
        let deleted = SyncChange(revision: 2, collection: "fic_chapters", id: chapterID, deleted: true, data: nil)
        try reopened.apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "delta", changes: [deleted],
            hasMore: false, cursor: "two", collections: ["fic_chapters"]), startingBootstrap: false)
        XCTAssertNil(try reopened.lastReadChapter(bookID: book))
        XCTAssertThrowsError(try reopened.saveReadingPosition(collection: "fic_chapters", id: chapterID, version: epoch + ":1", offset: 0, bookID: book))
        XCTAssertTrue(try reopened.edits().isEmpty)
    }
}
