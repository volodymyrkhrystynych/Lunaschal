import XCTest
import UIKit
import UniformTypeIdentifiers
import LunaschalCore
@testable import Lunaschal

final class SharedScreenshotLoaderTests: XCTestCase {
    func testUnusableProviderDoesNotPublishAScreenshot() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let outbox = try ScreenshotOutbox(root: root)
        defer { try? FileManager.default.removeItem(at: root) }
        let provider = NSItemProvider()
        provider.registerDataRepresentation(forTypeIdentifier: UTType.png.identifier, visibility: .all) { completion in
            completion(nil, CocoaError(.fileReadUnknown))
            return nil
        }
        do {
            try await SharedScreenshotLoader.save(provider, to: outbox)
            XCTFail("An unreadable screenshot must not be reported as saved")
        } catch {}
        XCTAssertTrue(try outbox.list().isEmpty)
    }

    func testUnavailableFirstRepresentationFallsBackToPNG() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let outbox = try ScreenshotOutbox(root: root)
        defer { try? FileManager.default.removeItem(at: root) }
        let provider = NSItemProvider()
        provider.registerFileRepresentation(forTypeIdentifier: UTType.heic.identifier,
                                            fileOptions: [], visibility: .all) { completion in
            completion(nil, false, CocoaError(.fileReadUnknown))
            return nil
        }
        let image = UIGraphicsImageRenderer(size: CGSize(width: 12, height: 24)).image { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 12, height: 24))
        }
        let bytes = try XCTUnwrap(image.pngData())
        provider.registerDataRepresentation(forTypeIdentifier: UTType.png.identifier, visibility: .all) { completion in
            completion(bytes, nil)
            return nil
        }
        try await SharedScreenshotLoader.save(provider, to: outbox)
        let shots = try outbox.list()
        XCTAssertEqual(shots.count, 1)
        let shot = try XCTUnwrap(shots.first)
        XCTAssertEqual(shot.contentType, "image/png")
        XCTAssertEqual(try Data(contentsOf: outbox.file(for: shot)), bytes)
    }

    func testInMemoryScreenshotIsSavedAsAnImage() async throws {
        try await assertImageSaved(NSItemProvider.self)
    }

    func testDataIsUsedWhenTheProviderCannotSupplyAFile() async throws {
        try await assertImageSaved(DataOnlyImageProvider.self)
    }

    func testImageObjectIsUsedWhenEncodedRepresentationsAreUnavailable() async throws {
        try await assertImageSaved(ObjectOnlyImageProvider.self)
    }

    private func assertImageSaved(_ providerType: NSItemProvider.Type) async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let outbox = try ScreenshotOutbox(root: root)
        defer { try? FileManager.default.removeItem(at: root) }
        let image = UIGraphicsImageRenderer(size: CGSize(width: 12, height: 24)).image { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 12, height: 24))
        }
        try await SharedScreenshotLoader.save(providerType.init(object: image), to: outbox)
        let shot = try XCTUnwrap(outbox.list().first)
        XCTAssertNotNil(UIImage(contentsOfFile: outbox.file(for: shot).path))
    }

    func testProviderImageIsSavedBeforeItsTemporaryFileDisappears() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let outbox = try ScreenshotOutbox(root: root)
        defer { try? FileManager.default.removeItem(at: root) }
        let source = root.appendingPathComponent("source.png")
        let bytes = Data([137, 80, 78, 71])
        try bytes.write(to: source)
        let provider = NSItemProvider()
        provider.registerFileRepresentation(forTypeIdentifier: UTType.png.identifier,
                                            fileOptions: [], visibility: .all) { completion in
            completion(source, false, nil)
            return nil
        }
        try await SharedScreenshotLoader.save(provider, to: outbox)
        try FileManager.default.removeItem(at: source)
        let shot = try XCTUnwrap(outbox.list().first)
        XCTAssertEqual(shot.contentType, "image/png")
        XCTAssertEqual(try Data(contentsOf: outbox.file(for: shot)), bytes)
    }
}

private class DataOnlyImageProvider: NSItemProvider {
    override func loadFileRepresentation(forTypeIdentifier typeIdentifier: String,
                                         completionHandler: @escaping (URL?, Error?) -> Void) -> Progress {
        completionHandler(nil, CocoaError(.fileReadUnknown))
        return Progress(totalUnitCount: 1)
    }
}

private final class ObjectOnlyImageProvider: DataOnlyImageProvider {
    override func loadDataRepresentation(forTypeIdentifier typeIdentifier: String,
                                         completionHandler: @escaping (Data?, Error?) -> Void) -> Progress {
        completionHandler(nil, CocoaError(.fileReadUnknown))
        return Progress(totalUnitCount: 1)
    }
}
