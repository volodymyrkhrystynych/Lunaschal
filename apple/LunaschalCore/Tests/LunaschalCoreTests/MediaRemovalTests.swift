import XCTest
@testable import LunaschalCore

@MainActor
final class MediaRemovalTests: XCTestCase {
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    private func item(collection: String = "fics", digest: String = "a") -> MediaDescriptor {
        MediaDescriptor(collection: collection, id: ULID.make(), available: true, size: 6,
            sha256: String(repeating: digest, count: 64), mime: "application/pdf", url: nil, reason: nil)
    }

    private func publish(_ item: MediaDescriptor, store: MediaStore) async throws {
        try store.append(Data("abcdef".utf8), to: item, offset: 0)
        try await store.finish(item)
    }

    func testSharedBytesSurviveUntilLastReferenceIsRemovedAcrossReopen() async throws {
        let root = try directory(), first = item(), second = item(collection: "study_sources")
        let store = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        try await publish(first, store: store)
        XCTAssertTrue(try store.reuse(second))
        XCTAssertEqual(try store.removeDownloadedCopy(collection: first.collection, id: first.id), 0)
        XCTAssertNil(try store.downloaded(collection: first.collection, id: first.id))
        let reopened = try MediaStore(root: root)
        let remaining = try XCTUnwrap(reopened.downloaded(collection: second.collection, id: second.id))
        XCTAssertEqual(try Data(contentsOf: remaining), Data("abcdef".utf8))
        XCTAssertEqual(try reopened.removeDownloadedCopy(collection: second.collection, id: second.id), 6)
        XCTAssertFalse(FileManager.default.fileExists(atPath: remaining.path))
        XCTAssertEqual(try reopened.usedBytes(), 0)
    }

    func testRemovalKeepsOtherDownloadsPartialsAndCaptureOriginals() async throws {
        let root = try directory(), target = item(), other = item(digest: "b")
        let original = root.appendingPathComponent("original.m4a")
        try Data("voice".utf8).write(to: original)
        let store = try MediaStore(root: root.appendingPathComponent("downloads"), hash: { url in
            String(repeating: url.lastPathComponent.hasPrefix("a") ? "a" : "b", count: 64)
        })
        try await publish(target, store: store)
        try await publish(other, store: store)
        try store.append(Data("abc".utf8), to: target, offset: 0)
        XCTAssertEqual(try store.removeDownloadedCopy(collection: target.collection, id: target.id), 6)
        XCTAssertEqual(try store.offset(for: target, budget: 10_000), 3)
        XCTAssertNotNil(try store.downloaded(collection: other.collection, id: other.id))
        XCTAssertEqual(try Data(contentsOf: original), Data("voice".utf8))
    }

    func testCorruptReferencePreventsAnyRemoval() async throws {
        let root = try directory(), target = item()
        let store = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        try await publish(target, store: store)
        let broken = root.appendingPathComponent("fics-\(ULID.make()).json")
        try Data("broken".utf8).write(to: broken)
        let before = try store.usedBytes()
        XCTAssertThrowsError(try store.removeDownloadedCopy(collection: target.collection, id: target.id))
        XCTAssertEqual(try store.usedBytes(), before)
        XCTAssertNotNil(try store.downloaded(collection: target.collection, id: target.id))
    }

    func testMismatchedManifestIdentityCannotReadOrDeleteAnotherItem() async throws {
        let root = try directory(), target = item(), wrong = item()
        let store = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        try await publish(target, store: store)
        let manifest = root.appendingPathComponent("fics-\(target.id).json")
        try JSONEncoder().encode(wrong).write(to: manifest)
        let before = try store.usedBytes()
        XCTAssertThrowsError(try store.downloaded(collection: target.collection, id: target.id))
        XCTAssertThrowsError(try store.removeDownloadedCopy(collection: target.collection, id: target.id))
        XCTAssertEqual(try store.usedBytes(), before)
    }

    func testRepeatedRemovalAndInvalidIdentityAreSafe() async throws {
        let store = try MediaStore(root: directory(), hash: { _ in String(repeating: "a", count: 64) })
        let target = item()
        try await publish(target, store: store)
        XCTAssertEqual(try store.removeDownloadedCopy(collection: target.collection, id: target.id), 6)
        XCTAssertEqual(try store.removeDownloadedCopy(collection: target.collection, id: target.id), 0)
        XCTAssertThrowsError(try store.removeDownloadedCopy(collection: "../captures", id: target.id))
        XCTAssertThrowsError(try store.removeDownloadedCopy(collection: "fics", id: "../original"))
    }

    func testMissingCompletedBytesStillAllowsRemovingStaleManifest() async throws {
        let root = try directory(), target = item()
        let store = try MediaStore(root: root, hash: { _ in String(repeating: "a", count: 64) })
        try await publish(target, store: store)
        let file = try XCTUnwrap(store.downloaded(collection: target.collection, id: target.id))
        try FileManager.default.removeItem(at: file)
        XCTAssertEqual(try store.removeDownloadedCopy(collection: target.collection, id: target.id), 0)
        XCTAssertEqual(try store.usedBytes(), 0)
    }
}
