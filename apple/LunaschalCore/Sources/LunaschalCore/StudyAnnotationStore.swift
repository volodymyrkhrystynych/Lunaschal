import Foundation

/// Ink is separate from replaceable downloads and keyed to the exact source
/// version, so replacement PDFs can never inherit marks on the wrong pages.
public final class StudyAnnotationStore {
    public let root: URL

    public init(root: URL, sourceID: String, version: String) throws {
        guard ULID.isValid(sourceID), version.count == 64,
              version.allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw CaptureError.invalidID
        }
        self.root = root.appendingPathComponent(sourceID, isDirectory: true)
            .appendingPathComponent(version, isDirectory: true)
        try FileManager.default.createDirectory(at: self.root, withIntermediateDirectories: true)
    }

    public func drawingStore(page: Int) throws -> DrawingStore {
        guard page >= 0 else { throw CaptureError.invalidID }
        return try DrawingStore(root: root.appendingPathComponent(String(page), isDirectory: true))
    }

    public func drawing(page: Int) throws -> DrawingPage? {
        try drawingStore(page: page).pages().first
    }

    public func ink(page: Int) throws -> Data? {
        let store = try drawingStore(page: page)
        guard let drawing = try store.pages().first, let url = try store.nativeURL(drawing) else { return nil }
        return try Data(contentsOf: url)
    }

    public func save(page: Int, native: Data, preview: Data) throws {
        let store = try drawingStore(page: page)
        let drawing = try store.pages().first ?? store.create(title: "Page \(page + 1)")
        try store.checkpoint(drawing.id, native: native, preview: preview)
    }
}
