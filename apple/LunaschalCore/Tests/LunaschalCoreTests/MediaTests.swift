import XCTest
@testable import LunaschalCore

@MainActor
final class MediaTests: XCTestCase {
    private func descriptor(size: Int64 = 6) -> MediaDescriptor {
        MediaDescriptor(collection: "journal_attachments", id: ULID.make(), available: true, size: size,
                        sha256: String(repeating: "a", count: 64), mime: "audio/mp4", url: nil, reason: nil)
    }
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    func testResumeAfterReopeningAndPublishOnlyVerifiedCompleteFile() async throws {
        let root = try directory(), item = descriptor()
        let hasher: @Sendable (URL) throws -> String = { url in
            XCTAssertEqual(try Data(contentsOf: url), Data("abcdef".utf8))
            return item.sha256!
        }
        let first = try MediaStore(root: root, hash: hasher)
        try first.append(Data("abc".utf8), to: item, offset: 0)
        XCTAssertNil(try first.downloaded(collection: item.collection, id: item.id))
        let reopened = try MediaStore(root: root, hash: hasher)
        XCTAssertEqual(try reopened.offset(for: item, budget: 1000), 3)
        try reopened.append(Data("def".utf8), to: item, offset: 3)
        try await reopened.finish(item)
        let file = try XCTUnwrap(reopened.downloaded(collection: item.collection, id: item.id))
        XCTAssertEqual(try Data(contentsOf: file), Data("abcdef".utf8))
    }

    func testCorruptFileNeverBecomesAvailableAndCanBeRetried() async throws {
        let store = try MediaStore(root: directory(), hash: { _ in "wrong" }), item = descriptor()
        try store.append(Data("broken".utf8), to: item, offset: 0)
        do { try await store.finish(item); XCTFail("Corrupt download was accepted") }
        catch MediaError.integrity {}
        XCTAssertNil(try store.downloaded(collection: item.collection, id: item.id))
        XCTAssertEqual(try store.offset(for: item, budget: 1000), 0)
    }

    func testBudgetOffsetAndOverrunProtection() async throws {
        let store = try MediaStore(root: directory(), hash: { _ in "" }), item = descriptor()
        XCTAssertThrowsError(try store.offset(for: item, budget: 5))
        try store.append(Data("abc".utf8), to: item, offset: 0)
        XCTAssertThrowsError(try store.append(Data("abc".utf8), to: item, offset: 0))
        XCTAssertThrowsError(try store.append(Data("abcd".utf8), to: item, offset: 3))
    }

    func testRemovingDownloadsDoesNotTouchCaptureSiblings() async throws {
        let root = try directory()
        let original = root.appendingPathComponent("original.m4a")
        try Data("original".utf8).write(to: original)
        let store = try MediaStore(root: root.appendingPathComponent("downloads"), hash: { _ in "" })
        try store.append(Data("abc".utf8), to: descriptor(), offset: 0)
        try store.removeDownloadedCopies()
        XCTAssertEqual(try Data(contentsOf: original), Data("original".utf8))
        XCTAssertEqual(try store.usedBytes(), 0)
    }

    #if canImport(CryptoKit)
    func testCryptoKitKnownDigest() async throws {
        let file = try directory().appendingPathComponent("input")
        try Data("abc".utf8).write(to: file)
        XCTAssertEqual(try MediaStore.sha256(file), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }
    #endif
}
