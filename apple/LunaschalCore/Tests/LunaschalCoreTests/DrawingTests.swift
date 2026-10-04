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

    func testRecoveryValidatesPreviousInkBeforePublishingIt() throws {
        let store = try DrawingStore(root: directory())
        let page = try store.create()
        let first = try store.checkpoint(page.id, native: Data([1]), preview: Data([2]))
        let second = try store.checkpoint(page.id, native: Data([3]), preview: Data([4]))
        XCTAssertThrowsError(try store.restorePrevious(page.id, validate: { _ in throw DrawingError.incompleteCheckpoint }))
        XCTAssertEqual(try store.page(page.id), second)
        let restored = try store.restorePrevious(page.id) { XCTAssertEqual($0, Data([1])) }
        XCTAssertEqual(restored.checkpoint, first.checkpoint)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(store.nativeURL(second))), Data([3]))
    }

    func testMismatchedManifestCannotRedirectAWritingOperation() throws {
        let root = try directory(), store = try DrawingStore(root: root)
        let first = try store.create(title: "First")
        let second = try store.create(title: "Second")
        try JSONEncoder().encode(second).write(to: root.appendingPathComponent(first.id).appendingPathExtension("json"))
        XCTAssertThrowsError(try store.rename(first.id, title: "Wrong target"))
        XCTAssertEqual(try store.page(second.id).title, "Second")
    }

    func testImportedInkReopensAsIndependentPagesAndPreservesExactBytes() throws {
        let root = try directory(), store = try DrawingStore(root: root)
        let native = Data([0, 1, 2, 255])
        let first = try store.importDrawing(title: "  Sketch  ", native: native) { _ in Data([3]) }
        let second = try store.importDrawing(title: "Sketch", native: native) { _ in Data([4]) }
        XCTAssertNotEqual(first.id, second.id)
        let reopened = try DrawingStore(root: root)
        XCTAssertEqual(try reopened.pages().count, 2)
        XCTAssertEqual(try reopened.page(first.id).title, "Sketch")
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(reopened.nativeURL(first))), native)
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(reopened.previewURL(second))), Data([4]))
    }

    func testRejectedImportDoesNotPublishPageOrChangeExistingInk() throws {
        let store = try DrawingStore(root: directory())
        let original = try store.importDrawing(title: "Original", native: Data([1])) { _ in Data([2]) }
        XCTAssertThrowsError(try store.importDrawing(title: "Invalid", native: Data([3])) { _ in
            throw DrawingError.incompleteCheckpoint
        })
        XCTAssertThrowsError(try store.importDrawing(title: "Missing preview", native: Data([3])) { _ in Data() })
        XCTAssertThrowsError(try store.importDrawing(title: "Empty", native: Data()) { _ in
            XCTFail("Empty imports must not reach native decoding")
            return Data([2])
        })
        XCTAssertEqual(try store.pages(), [original])
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(store.nativeURL(original))), Data([1]))
    }
}
