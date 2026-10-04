import Foundation

public struct StagedRecording: Codable, Equatable {
    public let capture: Capture
    public let server: URL
    public let boundary: String
    public let digest: String
    public let size: Int
}

/// Owned by the capture executor. A manifest publishes only after a complete
/// body is persisted. Credentials are supplied at request time, never stored here.
public final class RecordingUploadStore {
    public let root: URL
    private let hash: (URL) throws -> String
    private let fm = FileManager.default

    public init(root: URL, hash: @escaping (URL) throws -> String = { try MediaStore.sha256($0) }) throws {
        self.root = root
        self.hash = hash
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func prepare(_ capture: Capture, audioURL: URL, server: URL) throws -> StagedRecording {
        guard capture.mode != .text, capture.state == .pending else { throw CaptureError.invalidResponse }
        let directory = try folder(capture.id)
        let manifest = directory.appendingPathComponent("request.json")
        let address = try ServerAddress.parse(server.absoluteString)
        var identity = capture
        identity.lastError = nil
        identity.snapshot = nil
        if fm.fileExists(atPath: manifest.path) {
            let staged = try JSONDecoder().decode(StagedRecording.self, from: Data(contentsOf: manifest))
            guard staged.server == address else { throw CaptureError.differentServer }
            guard staged.capture == identity else { throw CaptureError.invalidResponse }
            let body = try bodyURL(staged)
            if (try? body.resourceValues(forKeys: [.fileSizeKey]).fileSize) == staged.size,
               (try? hash(body)) == staged.digest { return staged }
            // No request is active during prepare. Rebuild damaged staging
            // from the retained original before scheduling another attempt.
        }

        // An interrupted preparation has no manifest and cannot be scheduled.
        // Its body can be replaced; the original audio is outside this directory.
        try fm.createDirectory(at: directory, withIntermediateDirectories: true)
        let multipart = try RecordingMultipart(capture: capture, audioURL: audioURL)
        defer { try? fm.removeItem(at: multipart.url) }
        let temporary = directory.appendingPathComponent("body.preparing")
        if fm.fileExists(atPath: temporary.path) { try fm.removeItem(at: temporary) }
        try fm.copyItem(at: multipart.url, to: temporary)
        let staged = StagedRecording(capture: identity, server: address, boundary: multipart.boundary,
            digest: try hash(temporary), size: try temporary.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
        let body = try bodyURL(staged)
        if fm.fileExists(atPath: manifest.path) { try fm.removeItem(at: manifest) }
        if fm.fileExists(atPath: body.path) { try fm.removeItem(at: body) }
        try fm.moveItem(at: temporary, to: body)
        try JSONEncoder().encode(staged).write(to: manifest, options: .atomic)
        return staged
    }

    public func bodyURL(_ staged: StagedRecording) throws -> URL {
        try folder(staged.capture.id).appendingPathComponent("body.multipart")
    }

    /// The caller must load this state from its durable capture store first.
    /// A pending capture (including a lost acknowledgement) is never cleaned up.
    public func discardAfterSync(_ capture: Capture) throws {
        guard capture.state == .synced else { return }
        let directory = try folder(capture.id)
        if fm.fileExists(atPath: directory.path) { try fm.removeItem(at: directory) }
    }

    private func folder(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id, isDirectory: true)
    }
}
