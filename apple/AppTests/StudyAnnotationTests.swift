import XCTest
import PDFKit
import PencilKit
import UIKit
import LunaschalCore
@testable import Lunaschal

@MainActor
final class StudyAnnotationViewTests: XCTestCase {
    private func fixture() throws -> (URL, Data, StudyAnnotationStore) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.pdf")
        let data = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 595, height: 842)).pdfData { context in
            for index in 0..<2 {
                context.beginPage()
                ("Page \(index)" as NSString).draw(at: CGPoint(x: 30, y: 30),
                    withAttributes: [.font: UIFont.systemFont(ofSize: 18)])
            }
        }
        try data.write(to: source)
        let store = try StudyAnnotationStore(root: root.appendingPathComponent("ink"),
            sourceID: ULID.make(), version: MediaStore.sha256(source))
        return (source, data, store)
    }

    private func ink() -> PKDrawing {
        let points = [CGPoint(x: 100, y: 100), CGPoint(x: 300, y: 200)].enumerated().map { index, point in
            PKStrokePoint(location: point, timeOffset: Double(index), size: CGSize(width: 5, height: 5),
                          opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        return PKDrawing(strokes: [PKStroke(ink: PKInk(.pen, color: .blue),
            path: PKStrokePath(controlPoints: points, creationDate: Date()))])
    }

    func testPageNavigationSavesInkAndReopeningKeepsOriginalPDFUntouched() throws {
        let (source, original, store) = try fixture()
        let model = try StudyAnnotationModel(file: source, mime: "application/pdf", store: store)
        XCTAssertEqual(model.canvas.drawingPolicy, .pencilOnly)
        model.canvas.drawing = ink()
        model.changed()
        model.go(to: 1)
        XCTAssertEqual(model.pageIndex, 1)
        XCTAssertTrue(model.canvas.drawing.strokes.isEmpty)
        let reopened = try StudyAnnotationModel(file: source, mime: "application/pdf", store: store)
        XCTAssertEqual(reopened.canvas.drawing.strokes.count, 1)
        XCTAssertEqual(reopened.canvas.drawing.strokes[0].path[0].location, CGPoint(x: 100, y: 100))
        XCTAssertEqual(try Data(contentsOf: source), original)
        reopened.exportPage()
        XCTAssertNotNil(UIImage(contentsOfFile: try XCTUnwrap(reopened.exportURL).path))
    }

    func testErasingAllInkPersistsAnEmptyDrawing() throws {
        let (source, _, store) = try fixture()
        let model = try StudyAnnotationModel(file: source, mime: "application/pdf", store: store)
        model.canvas.drawing = ink()
        model.changed()
        XCTAssertTrue(model.save())
        model.canvas.drawing = PKDrawing()
        model.changed()
        XCTAssertTrue(model.save())
        let reopened = try StudyAnnotationModel(file: source, mime: "application/pdf", store: store)
        XCTAssertTrue(reopened.canvas.drawing.strokes.isEmpty)
    }

    func testMissingSavedInkDisablesEditingInsteadOfOverwritingIt() throws {
        let (source, _, store) = try fixture()
        let model = try StudyAnnotationModel(file: source, mime: "application/pdf", store: store)
        model.canvas.drawing = ink()
        model.changed()
        XCTAssertTrue(model.save())
        let drawings = try store.drawingStore(page: 0)
        let page = try XCTUnwrap(store.drawing(page: 0))
        let path = try XCTUnwrap(drawings.nativeURL(page))
        try FileManager.default.removeItem(at: path)
        let reopened = try StudyAnnotationModel(file: source, mime: "application/pdf", store: store)
        XCTAssertNotNil(reopened.error)
        XCTAssertFalse(reopened.canvas.isUserInteractionEnabled)
        XCTAssertFalse(reopened.save())
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }
}
