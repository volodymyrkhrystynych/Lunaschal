import Foundation

public struct DrawingOperation: Codable, Equatable {
    public let id: String
    public let epoch: String
    public let paperId: String
    public let pageId: String
    public let baseRevision: Int64
    public let title: String
    public let format: String
}

public struct DrawingPublication: Codable {
    public let operation: DrawingOperation
    public let server: URL
    public let checkpoint: String
    public let inkHash: String
    public let previewHash: String
    public var state: String
    public var revision: Int64?
    public var error: String?
}

public struct DrawingReply: Codable {
    public let operationId: String?
    public let changes: [SyncChange]?
    public let error: String?
    public let conflict: Bool?
    public let resetRequired: Bool?
}

/// Explicit Save snapshots ink independently of the rolling local checkpoints.
/// One unresolved publication per page; retries never adopt a newer revision.
public final class DrawingPublicationStore {
    public let root: URL
    private let fm = FileManager.default
    private let hash: (URL) throws -> String

    public init(root: URL, hash: @escaping (URL) throws -> String = { try MediaStore.sha256($0) }) throws {
        self.root = root
        self.hash = hash
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func publication(_ pageID: String) throws -> DrawingPublication? {
        let file = try manifest(pageID)
        guard fm.fileExists(atPath: file.path) else { return nil }
        let value = try JSONDecoder().decode(DrawingPublication.self, from: Data(contentsOf: file))
        guard value.operation.pageId == pageID, ULID.isValid(value.operation.id),
              ULID.isValid(value.operation.paperId) else { throw CaptureError.invalidID }
        return value
    }

    public func all() throws -> [DrawingPublication] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .compactMap { try publication($0.deletingPathExtension().lastPathComponent) }
    }

    @discardableResult
    public func queue(_ page: DrawingPage, drawings: DrawingStore, server: URL, epoch: String) throws -> DrawingPublication {
        let address = try ServerAddress.parse(server.absoluteString)
        guard ULID.isValid(epoch), let checkpoint = page.checkpoint,
              let ink = try drawings.nativeURL(page), let preview = try drawings.previewURL(page) else {
            throw DrawingError.incompleteCheckpoint
        }
        let previous = try publication(page.id)
        if let previous {
            guard previous.server == address else { throw CaptureError.differentServer }
            guard previous.operation.epoch == epoch else { throw ReplicaError.needsBootstrap }
            guard previous.state == "synced" else { throw ReplicaError.editAlreadyPending }
            if previous.checkpoint == checkpoint { return previous }
        }
        let operation = DrawingOperation(id: ULID.make(), epoch: epoch,
            paperId: previous?.operation.paperId ?? ULID.make(), pageId: page.id,
            baseRevision: previous?.revision ?? 0, title: String(page.title.prefix(500)), format: "pencilkit-v1")
        let directory = try folder(operation.id)
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        try fm.copyItem(at: ink, to: directory.appendingPathComponent("original.drawing"))
        try fm.copyItem(at: preview, to: directory.appendingPathComponent("preview.png"))
        let value = DrawingPublication(operation: operation, server: address, checkpoint: checkpoint,
            inkHash: try hash(ink), previewHash: try hash(preview), state: "pending")
        try write(value)
        // Only a confirmed previous publication is eligible for cleanup.
        if let previous { try? fm.removeItem(at: folder(previous.operation.id)) }
        return value
    }

    public func payloads(_ value: DrawingPublication) throws -> (ink: URL, preview: URL) {
        let directory = try folder(value.operation.id)
        let ink = directory.appendingPathComponent("original.drawing")
        let preview = directory.appendingPathComponent("preview.png")
        guard try hash(ink) == value.inkHash, try hash(preview) == value.previewHash else {
            throw DrawingError.incompleteCheckpoint
        }
        return (ink, preview)
    }

    /// Establish the revision this imported copy was actually read from. A
    /// newer replica revision must never silently authorize overwriting it.
    public func adopt(_ page: DrawingPage, paperID: String, native: SyncChange, drawings: DrawingStore,
                      server: URL, epoch: String, replacingCheckpoint: String? = nil) throws {
        let address = try ServerAddress.parse(server.absoluteString)
        let previous = try publication(page.id)
        if let previous {
            guard previous.state == "synced", previous.checkpoint == replacingCheckpoint,
                  previous.server == address, previous.operation.epoch == epoch,
                  previous.operation.paperId == paperID else { throw ReplicaError.editAlreadyPending }
        }
        guard ULID.isValid(paperID), ULID.isValid(epoch),
              native.id == page.id, native.collection == "paper_native_ink", !native.deleted, native.revision > 0,
              let ink = try drawings.nativeURL(page), let preview = try drawings.previewURL(page),
              native.data?["sha256"]?.string == (try hash(ink)),
              native.data?["previewSha256"]?.string == (try hash(preview)), let checkpoint = page.checkpoint else {
            throw CaptureError.invalidResponse
        }
        let operation = DrawingOperation(id: ULID.make(), epoch: epoch, paperId: paperID, pageId: page.id,
            baseRevision: native.revision, title: String(page.title.prefix(500)), format: "pencilkit-v1")
        let directory = try folder(operation.id)
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        try fm.copyItem(at: ink, to: directory.appendingPathComponent("original.drawing"))
        try fm.copyItem(at: preview, to: directory.appendingPathComponent("preview.png"))
        try write(DrawingPublication(operation: operation, server: address,
            checkpoint: checkpoint, inkHash: try hash(ink), previewHash: try hash(preview), state: "synced", revision: native.revision))
        if let previous { try? fm.removeItem(at: folder(previous.operation.id)) }
    }

    public func receive(_ reply: DrawingReply, for sent: DrawingPublication) throws {
        guard var current = try publication(sent.operation.pageId), current.operation == sent.operation,
              current.state == "pending" else { throw CaptureError.invalidResponse }
        if reply.conflict == true || reply.resetRequired == true || reply.changes == nil {
            current.state = "conflict"
            current.error = reply.error ?? "The server refused this drawing. The queued copy is kept."
        } else {
            guard reply.operationId == sent.operation.id,
                  let native = reply.changes?.first(where: { $0.collection == "paper_native_ink" && $0.id == sent.operation.pageId }),
                  !native.deleted, native.revision > 0, native.data?["sha256"]?.string == sent.inkHash,
                  native.data?["previewSha256"]?.string == sent.previewHash else { throw CaptureError.invalidResponse }
            current.state = "synced"
            current.revision = native.revision
            current.error = nil
        }
        try write(current)
    }

    private func write(_ value: DrawingPublication) throws {
        try JSONEncoder().encode(value).write(to: manifest(value.operation.pageId), options: .atomic)
    }
    private func manifest(_ pageID: String) throws -> URL {
        guard ULID.isValid(pageID) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(pageID).appendingPathExtension("json")
    }
    private func folder(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id, isDirectory: true)
    }
}

/// Stream the two immutable files; do not hold a 64 MB drawing in request RAM.
public struct DrawingMultipart {
    public let url: URL
    public let boundary = "Lunaschal-\(UUID().uuidString)"

    public init(operation: DrawingOperation, ink: URL, preview: URL) throws {
        url = FileManager.default.temporaryDirectory.appendingPathComponent(boundary)
        try Data().write(to: url)
        do {
            let output = try FileHandle(forWritingTo: url)
            defer { try? output.close() }
            func text(_ text: String) throws { try output.write(contentsOf: Data(text.utf8)) }
            try text("--\(boundary)\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n")
            try output.write(contentsOf: JSONEncoder().encode(operation))
            try text("\r\n")
            for (name, file, maximum) in [("ink", ink, 64 * 1024 * 1024), ("preview", preview, 16 * 1024 * 1024)] {
                let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                guard size > 0, size <= maximum else { throw DrawingError.incompleteCheckpoint }
                try text("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"; filename=\"\(name)\"\r\nContent-Type: application/octet-stream\r\n\r\n")
                let input = try FileHandle(forReadingFrom: file)
                defer { try? input.close() }
                while let chunk = try input.read(upToCount: 64 * 1024), !chunk.isEmpty { try output.write(contentsOf: chunk) }
                try text("\r\n")
            }
            try text("--\(boundary)--\r\n")
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw error
        }
    }
}
