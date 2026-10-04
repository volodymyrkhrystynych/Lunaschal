import XCTest
@testable import LunaschalCore

final class StudyAnnotationTests: XCTestCase {
    private func directory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testInkSurvivesReopeningAndIsIsolatedBySourceVersionAndPage() throws {
        let root = try directory(), id = ULID.make()
        let version = String(repeating: "a", count: 64)
        let first = try StudyAnnotationStore(root: root, sourceID: id, version: version)
        try first.save(page: 0, native: Data("page one".utf8), preview: Data("preview".utf8))
        try first.save(page: 1, native: Data("page two".utf8), preview: Data("preview".utf8))
        let reopened = try StudyAnnotationStore(root: root, sourceID: id, version: version)
        XCTAssertEqual(try reopened.ink(page: 0), Data("page one".utf8))
        XCTAssertEqual(try reopened.ink(page: 1), Data("page two".utf8))
        XCTAssertNil(try reopened.ink(page: 2))
        let replacement = try StudyAnnotationStore(root: root, sourceID: id, version: String(repeating: "b", count: 64))
        XCTAssertNil(try replacement.ink(page: 0))
        let other = try StudyAnnotationStore(root: root, sourceID: ULID.make(), version: version)
        XCTAssertNil(try other.ink(page: 0))
    }

    func testFailedSavePreservesLastInkAndPreviousCheckpointCanBeRestored() throws {
        let store = try StudyAnnotationStore(root: directory(), sourceID: ULID.make(), version: String(repeating: "a", count: 64))
        try store.save(page: 0, native: Data("first".utf8), preview: Data("preview".utf8))
        XCTAssertThrowsError(try store.save(page: 0, native: Data(), preview: Data()))
        XCTAssertEqual(try store.ink(page: 0), Data("first".utf8))
        try store.save(page: 0, native: Data("second".utf8), preview: Data("preview".utf8))
        let drawings = try store.drawingStore(page: 0)
        let page = try XCTUnwrap(store.drawing(page: 0))
        _ = try drawings.restorePrevious(page.id) { _ in }
        XCTAssertEqual(try store.ink(page: 0), Data("first".utf8))
    }

    func testInvalidIdentifiersAndPageNumbersCannotEscapeAnnotationRoot() throws {
        let root = try directory()
        XCTAssertThrowsError(try StudyAnnotationStore(root: root, sourceID: "../bad", version: String(repeating: "a", count: 64)))
        XCTAssertThrowsError(try StudyAnnotationStore(root: root, sourceID: ULID.make(), version: "../source"))
        let store = try StudyAnnotationStore(root: root, sourceID: ULID.make(), version: String(repeating: "a", count: 64))
        XCTAssertThrowsError(try store.ink(page: -1))
    }
}
