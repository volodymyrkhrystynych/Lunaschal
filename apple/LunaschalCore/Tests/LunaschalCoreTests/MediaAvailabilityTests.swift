import XCTest
@testable import LunaschalCore

@MainActor
final class MediaAvailabilityTests: XCTestCase {
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }
    private func item(id: String = ULID.make(), available: Bool = true, digest: String = "a") -> MediaDescriptor {
        MediaDescriptor(collection: "fics", id: id, available: available, size: available ? 6 : nil,
            sha256: available ? String(repeating: digest, count: 64) : nil,
            mime: "application/pdf", url: nil, reason: available ? nil : "Archive excluded or file missing")
    }

    func testAvailabilitySurvivesReopenAndOnlyVerifiedBytesAreDownloaded() async throws {
        let root = try directory(), book = item()
        let store = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .metadataOnly)
        try store.observe(book)
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .pending)
        try store.append(Data("abc".utf8), to: book, offset: 0)
        let reopened = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        XCTAssertEqual(try reopened.availability(collection: "fics", id: book.id), .partial(received: 3, total: 6))
        try reopened.append(Data("def".utf8), to: book, offset: 3)
        XCTAssertEqual(try reopened.availability(collection: "fics", id: book.id), .partial(received: 6, total: 6))
        try await reopened.finish(book)
        XCTAssertEqual(try reopened.availability(collection: "fics", id: book.id), .downloaded)
    }

    func testServerUnavailabilityDoesNotHideAnExistingOfflineCopy() async throws {
        let root = try directory(), book = item()
        let store = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        try store.append(Data("abcdef".utf8), to: book, offset: 0)
        try await store.finish(book)
        try store.observe(item(id: book.id, available: false))
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .downloaded)
        try store.removeDownloadedCopy(collection: "fics", id: book.id)
        let reopened = try MediaStore(root: root)
        XCTAssertEqual(try reopened.availability(collection: "fics", id: book.id), .unavailable("Archive excluded or file missing"))
    }

    func testNewVersionDoesNotReuseOldPartialProgressOrHideOldCompletedCopy() async throws {
        let store = try MediaStore(root: directory(), hash: { _ in String(repeating: "a", count: 64) })
        let book = item(), updated = item(digest: "b")
        let next = item(id: book.id, digest: "b")
        try store.observe(book)
        try store.append(Data("abc".utf8), to: book, offset: 0)
        try store.observe(next)
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .pending)
        try store.append(Data("def".utf8), to: book, offset: 3)
        try await store.finish(book)
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .downloaded)
        try store.observe(updated)
        XCTAssertEqual(try store.availability(collection: "fics", id: updated.id), .pending)
    }

    func testRemovalPreservesLastObservationAndWholeCleanupClearsIt() async throws {
        let store = try MediaStore(root: directory(), hash: { _ in String(repeating: "a", count: 64) })
        let book = item()
        try store.observe(book)
        try store.append(Data("abcdef".utf8), to: book, offset: 0)
        try await store.finish(book)
        try store.removeDownloadedCopy(collection: "fics", id: book.id)
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .pending)
        try store.removeDownloadedCopies()
        XCTAssertEqual(try store.availability(collection: "fics", id: book.id), .metadataOnly)
        XCTAssertEqual(try store.usedBytes(), 0)
    }

    func testMismatchedObservationCannotSupplyStatusForAnotherRecord() async throws {
        let root = try directory(), book = item(), other = item()
        let store = try MediaStore(root: root)
        try JSONEncoder().encode(other).write(to: root.appendingPathComponent("fics-\(book.id).observed"))
        XCTAssertThrowsError(try store.availability(collection: "fics", id: book.id))
        XCTAssertThrowsError(try store.availability(collection: "fics", id: "../private"))
        XCTAssertThrowsError(try store.observe(item(id: "../private")))
    }
}
