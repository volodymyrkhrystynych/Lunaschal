import XCTest
import UniformTypeIdentifiers
import LunaschalCore
@testable import Lunaschal

final class SharedScreenshotLoaderTests: XCTestCase {
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
