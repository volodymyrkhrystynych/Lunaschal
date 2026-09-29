import XCTest
@testable import LunaschalCore

final class DrawingTests: XCTestCase {
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    func testCheckpointReopensAndPreviousNativeVersionIsRetained() throws {
        let root = try directory(), store = try DrawingStore(root: root)
        let page = try store.create()
        let first = try store.checkpoint(page.id, native: Data("first ink".utf8), preview: Data("first preview".utf8))
        let second = try store.checkpoint(page.id, native: Data("second ink".utf8), preview: Data("second preview".utf8))
        let reopened = try DrawingStore(root: root)
        XCTAssertEqual(try reopened.page(page.id), second)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(reopened.nativeURL(first))), Data("first ink".utf8))
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(reopened.nativeURL(second))), Data("second ink".utf8))
    }

    func testFailedCheckpointDoesNotReplacePublishedInk() throws {
        let root = try directory(), store = try DrawingStore(root: root)
        let page = try store.create()
        let saved = try store.checkpoint(page.id, native: Data([1]), preview: Data([2]))
        XCTAssertThrowsError(try store.checkpoint(page.id, native: Data([3]), preview: Data()))
        XCTAssertEqual(try store.page(page.id), saved)
        // Simulate a filesystem refusing new checkpoint directories.
        let folder = root.appendingPathComponent(page.id)
        let preserved = root.appendingPathComponent("preserved")
        try FileManager.default.moveItem(at: folder, to: preserved)
        try Data([0]).write(to: folder)
        XCTAssertThrowsError(try store.checkpoint(page.id, native: Data([3]), preview: Data([4])))
        XCTAssertEqual(try store.page(page.id), saved)
    }

    func testRenamePreservesInkAndRejectsPathTraversal() throws {
        let store = try DrawingStore(root: directory())
        let page = try store.create()
        let saved = try store.checkpoint(page.id, native: Data([1]), preview: Data([2]))
        try store.rename(page.id, title: "Sketch")
        XCTAssertEqual(try store.page(page.id).checkpoint, saved.checkpoint)
        XCTAssertThrowsError(try store.page("../outside"))
    }

    func testRepeatedCheckpointsKeepOnlyCurrentAndPreviousGeneration() throws {
        let root = try directory(), store = try DrawingStore(root: root)
        let page = try store.create()
        var latest = page
        for value in UInt8(1)...10 {
            latest = try store.checkpoint(page.id, native: Data([value]), preview: Data([value]))
        }
        let versions = try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent(page.id).path)
        XCTAssertEqual(versions.count, 2)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(store.nativeURL(latest))), Data([10]))
    }
}
