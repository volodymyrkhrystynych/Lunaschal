import XCTest
import PencilKit
import UIKit
import LunaschalCore
@testable import Lunaschal

final class DrawingImportTests: XCTestCase {
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    func testExportedNativeStrokeImportsWithoutLosingEditableInk() throws {
        let root = try directory()
        let points = [CGPoint(x: 100, y: 200), CGPoint(x: 300, y: 400)].enumerated().map { index, point in
            PKStrokePoint(location: point, timeOffset: Double(index), size: CGSize(width: 5, height: 5),
                          opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        let path = PKStrokePath(controlPoints: points, creationDate: Date())
        let drawing = PKDrawing(strokes: [PKStroke(ink: PKInk(.pen, color: .blue), path: path)])
        let original = drawing.dataRepresentation()
        let source = root.appendingPathComponent("My sketch.drawing")
        try original.write(to: source)
        let store = try DrawingStore(root: root.appendingPathComponent("drawings"))
        let page = try DrawingImport.importFile(source, into: store)
        let saved = try Data(contentsOf: XCTUnwrap(store.nativeURL(page)))
        XCTAssertEqual(saved, original)
        XCTAssertEqual(try Data(contentsOf: source), original)
        XCTAssertEqual(page.title, "My sketch")
        let restored = try PKDrawing(data: saved)
        XCTAssertEqual(restored.strokes.count, 1)
        XCTAssertEqual(restored.strokes[0].path.count, 2)
        XCTAssertEqual(restored.strokes[0].path[0].location, points[0].location)
        let preview = try Data(contentsOf: XCTUnwrap(store.previewURL(page)))
        XCTAssertNotNil(UIImage(data: preview))
        XCTAssertEqual(UIImage(data: preview)?.size, CGSize(width: 1240, height: 1754))
    }

    func testInvalidInkLeavesSourceAndLibraryUntouched() throws {
        let root = try directory()
        let source = root.appendingPathComponent("invalid.drawing")
        let data = Data("This is not native ink".utf8)
        try data.write(to: source)
        let store = try DrawingStore(root: root.appendingPathComponent("drawings"))
        let existing = try store.create()
        XCTAssertThrowsError(try DrawingImport.importFile(source, into: store))
        XCTAssertEqual(try store.pages(), [existing])
        XCTAssertEqual(try Data(contentsOf: source), data)
    }

    func testOversizedImportIsRejectedBeforeDecoding() throws {
        let root = try directory()
        let source = root.appendingPathComponent("oversized.drawing")
        FileManager.default.createFile(atPath: source.path, contents: nil)
        let handle = try FileHandle(forWritingTo: source)
        try handle.truncate(atOffset: UInt64(DrawingImport.maximumBytes + 1))
        try handle.close()
        let store = try DrawingStore(root: root.appendingPathComponent("drawings"))
        XCTAssertThrowsError(try DrawingImport.importFile(source, into: store)) { error in
            XCTAssertTrue(error is DrawingImport.ImportError)
        }
        XCTAssertTrue(try store.pages().isEmpty)
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path))
    }

    func testEmptyNativeDrawingIsNotPublishedAsRestoredInk() throws {
        let root = try directory()
        let source = root.appendingPathComponent("blank.drawing")
        let original = PKDrawing().dataRepresentation()
        try original.write(to: source)
        let store = try DrawingStore(root: root.appendingPathComponent("drawings"))
        XCTAssertThrowsError(try DrawingImport.importFile(source, into: store)) { error in
            XCTAssertTrue(error is DrawingImport.ImportError)
        }
        XCTAssertTrue(try store.pages().isEmpty)
        XCTAssertEqual(try Data(contentsOf: source), original)
    }
}
