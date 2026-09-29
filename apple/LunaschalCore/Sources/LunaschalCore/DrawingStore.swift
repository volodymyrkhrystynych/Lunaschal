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
        try JSONDecoder().decode(DrawingPage.self, from: Data(contentsOf: manifest(id)))
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
        guard !native.isEmpty, !preview.isEmpty else { throw DrawingError.incompleteCheckpoint }
        var page = try page(id)
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
    case incompleteCheckpoint
    public var errorDescription: String? { "The drawing checkpoint is incomplete. Your previous saved version has been kept." }
}
