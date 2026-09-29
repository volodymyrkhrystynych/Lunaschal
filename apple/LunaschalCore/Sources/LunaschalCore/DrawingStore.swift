import Foundation

public struct DrawingPage: Codable, Identifiable, Equatable {
    public let id: String
    public var title: String
    public let createdAt: Date
    public var updatedAt: Date
    public var checkpoint: String?

    public init(title: String, now: Date = Date()) {
        id = ULID.make(now: now)
        self.title = title
        createdAt = now
        updatedAt = now
    }
}

/// A checkpoint becomes visible only after its native ink and preview exist.
/// The previous version is retained so a failed write never replaces good ink.
/// The app owns this store on the main actor, independently of download cleanup.
public final class DrawingStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func pages() throws -> [DrawingPage] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(DrawingPage.self, from: Data(contentsOf: $0)) }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    public func create(title: String = "Untitled drawing") throws -> DrawingPage {
        let page = DrawingPage(title: title)
        try write(page)
        return page
    }

    public func page(_ id: String) throws -> DrawingPage {
        let value = try JSONDecoder().decode(DrawingPage.self, from: Data(contentsOf: manifest(id)))
        guard value.id == id else { throw CaptureError.invalidID }
        return value
    }

    public func rename(_ id: String, title: String) throws {
        var page = try page(id)
        page.title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if page.title.isEmpty { page.title = "Untitled drawing" }
        page.updatedAt = Date()
        try write(page)
    }

    @discardableResult
    public func checkpoint(_ id: String, native: Data, preview: Data) throws -> DrawingPage {
        try publish(try page(id), native: native, preview: preview)
    }

    /// Import as a new page, publishing no manifest until validation, preview
    /// generation and both payload writes succeed. Preserve the original bytes.
    public func importDrawing(title: String, native: Data, makePreview: (Data) throws -> Data) throws -> DrawingPage {
        guard !native.isEmpty else { throw DrawingError.incompleteCheckpoint }
        let preview = try makePreview(native)
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        return try publish(DrawingPage(title: trimmed.isEmpty ? "Imported drawing" : trimmed), native: native, preview: preview)
    }

    private func publish(_ original: DrawingPage, native: Data, preview: Data) throws -> DrawingPage {
        guard !native.isEmpty, !preview.isEmpty else { throw DrawingError.incompleteCheckpoint }
        var page = original
        let id = page.id
        let previous = page.checkpoint
        let revision = ULID.make()
        let directory = try checkpointDirectory(id, revision)
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        // Native data is the original. A preview can always be regenerated.
        try native.write(to: directory.appendingPathComponent("original.drawing"), options: .atomic)
        try preview.write(to: directory.appendingPathComponent("preview.png"), options: .atomic)
        page.checkpoint = revision
        page.updatedAt = Date()
        try write(page)
        // Keep current + previous only. A whole native drawing per stroke must
        // not turn one long session into an unbounded version archive.
        let parent = directory.deletingLastPathComponent()
        if let versions = try? fm.contentsOfDirectory(at: parent, includingPropertiesForKeys: nil) {
            for version in versions where ULID.isValid(version.lastPathComponent)
                && version.lastPathComponent != revision && version.lastPathComponent != previous {
                try? fm.removeItem(at: version)
            }
        }
        return page
    }

    public func nativeURL(_ page: DrawingPage) throws -> URL? {
        guard let checkpoint = page.checkpoint else { return nil }
        return try checkpointDirectory(page.id, checkpoint).appendingPathComponent("original.drawing")
    }

    public func previewURL(_ page: DrawingPage) throws -> URL? {
        guard let checkpoint = page.checkpoint else { return nil }
        return try checkpointDirectory(page.id, checkpoint).appendingPathComponent("preview.png")
    }

    public func restorePrevious(_ id: String, validate: (Data) throws -> Void) throws -> DrawingPage {
        var page = try page(id)
        let parent = root.appendingPathComponent(id, isDirectory: true)
        let candidates = try fm.contentsOfDirectory(at: parent, includingPropertiesForKeys: nil)
            .filter { ULID.isValid($0.lastPathComponent) && $0.lastPathComponent != page.checkpoint }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
        guard let previous = candidates.first(where: {
            fm.fileExists(atPath: $0.appendingPathComponent("original.drawing").path)
                && fm.fileExists(atPath: $0.appendingPathComponent("preview.png").path)
        }) else { throw DrawingError.noPreviousCheckpoint }
        try validate(Data(contentsOf: previous.appendingPathComponent("original.drawing")))
        page.checkpoint = previous.lastPathComponent
        page.updatedAt = Date()
        try write(page)
        return page
    }

    private func write(_ page: DrawingPage) throws {
        try JSONEncoder().encode(page).write(to: manifest(page.id), options: .atomic)
    }
    private func manifest(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id).appendingPathExtension("json")
    }
    private func checkpointDirectory(_ id: String, _ checkpoint: String) throws -> URL {
        guard ULID.isValid(id), ULID.isValid(checkpoint) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id, isDirectory: true).appendingPathComponent(checkpoint, isDirectory: true)
    }
}

public enum DrawingError: LocalizedError {
    case incompleteCheckpoint, noPreviousCheckpoint
    public var errorDescription: String? {
        switch self {
        case .incompleteCheckpoint: return "The drawing checkpoint is incomplete. Your previous saved version has been kept."
        case .noPreviousCheckpoint: return "No previous complete drawing checkpoint is available. Existing files have been kept."
        }
    }
}
