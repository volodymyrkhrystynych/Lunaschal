import XCTest
import UIKit
import PDFKit
import LunaschalCore
@testable import Lunaschal

final class PDFLibraryTests: XCTestCase {
    @MainActor
    func testNativePDFReaderRestoresPageAndReportsNavigation() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let bytes = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 595, height: 842)).pdfData { context in
            for index in 0..<3 {
                context.beginPage()
                ("Page \(index)" as NSString).draw(at: CGPoint(x: 30, y: 30), withAttributes: [.font: UIFont.systemFont(ofSize: 18)])
            }
        }
        let source = root.appendingPathComponent("book.pdf")
        try bytes.write(to: source)
        let reader = LocalPDFView(url: source, initialPage: 2).makePDFView()
        let document = try XCTUnwrap(reader.document)
        XCTAssertEqual(document.index(for: try XCTUnwrap(reader.currentPage)), 2)
        let coordinator = LocalPDFView.Coordinator()
        var observed: Int?
        coordinator.observe(reader) { observed = $0 }
        reader.go(to: try XCTUnwrap(document.page(at: 1)))
        XCTAssertEqual(observed, 1)
        let fallback = LocalPDFView(url: source, initialPage: 100).makePDFView()
        XCTAssertEqual(fallback.document?.index(for: try XCTUnwrap(fallback.currentPage)), 0)
    }

    @MainActor
    func testResumedBookOpensInNativeReaderAfterStoreReopens() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let bytes = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 595, height: 842)).pdfData { context in
            context.beginPage()
            ("An offline book" as NSString).draw(at: CGPoint(x: 30, y: 30),
                withAttributes: [.font: UIFont.systemFont(ofSize: 18)])
        }
        let source = root.appendingPathComponent("source.pdf")
        try bytes.write(to: source)
        let id = ULID.make()
        let manifest: [String: Any] = ["collection": "fics", "id": id, "available": true,
            "size": bytes.count, "sha256": try MediaStore.sha256(source), "mime": "application/pdf"]
        let item = try JSONDecoder().decode(MediaDescriptor.self, from: JSONSerialization.data(withJSONObject: manifest))
        let directory = root.appendingPathComponent("downloads")
        let first = try MediaStore(root: directory)
        let split = bytes.count / 2
        try first.append(bytes.prefix(split), to: item, offset: 0)
        let resumed = try MediaStore(root: directory)
        XCTAssertEqual(try resumed.offset(for: item, budget: 1_000_000), Int64(split))
        try resumed.append(bytes.dropFirst(split), to: item, offset: Int64(split))
        try await resumed.finish(item)
        try FileManager.default.removeItem(at: source)
        let reopened = try MediaStore(root: directory)
        let file = try XCTUnwrap(reopened.downloaded(collection: "fics", id: id))
        let reader = LocalPDFView(url: file).makePDFView()
        XCTAssertTrue(reader.autoScales)
        XCTAssertEqual(reader.document?.pageCount, 1)
        XCTAssertTrue(reader.document?.string?.contains("An offline book") == true)
    }
}
