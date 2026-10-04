import Foundation

/// Small, append-oriented capture outbox. Each manifest is replaced atomically;
/// recordings are separate files and never loaded into the manifest or pruned.
/// Call from one executor (the app uses MainActor).
public final class CaptureStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [Capture] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" && $0.lastPathComponent != "server.json" }
            .map { try JSONDecoder().decode(Capture.self, from: Data(contentsOf: $0)) }
            .sorted { $0.createdAt > $1.createdAt }
    }

    public func save(_ capture: Capture) throws {
        guard ULID.isValid(capture.id), capture.attachmentID.map(ULID.isValid) ?? true else {
            throw CaptureError.invalidID
        }
        if let link = capture.youtubeURL {
            guard capture.mode == .text, capture.linkAttachmentID.map(ULID.isValid) == true,
                  try YouTubeLink.canonical(link) == link else { throw LinkError.invalidURL }
        }
        if capture.mode == .text && capture.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            throw CaptureError.emptyText
        }
        try JSONEncoder().encode(capture).write(to: manifest(capture.id), options: .atomic)
    }

    public func load(_ id: String) throws -> Capture {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return try JSONDecoder().decode(Capture.self, from: Data(contentsOf: manifest(id)))
    }

    public func audioURL(_ capture: Capture) throws -> URL {
        guard let id = capture.attachmentID, ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id).appendingPathExtension("m4a")
    }

    public func finishRecording(_ id: String) throws {
        var capture = try load(id)
        let url = try audioURL(capture)
        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0 else { throw CaptureError.missingAudio }
        capture.state = .pending
        capture.lastError = nil
        try save(capture)
    }

    /// Do not automatically upload an unfinalized container after an OS kill.
    /// The UI offers playback/export and an explicit recovery action.
    public func recoverInterruptedRecordings() throws {
        for var capture in try list() where capture.state == .recording {
            capture.state = .interrupted
            capture.lastError = "Recording was interrupted. Check playback before keeping it."
            try save(capture)
        }
    }

    public var server: URL? {
        get throws {
            let file = root.appendingPathComponent("server.json")
            guard fm.fileExists(atPath: file.path) else { return nil }
            return try ServerAddress.parse(JSONDecoder().decode(String.self, from: Data(contentsOf: file)))
        }
    }

    public func bind(to address: URL) throws {
        let normalized = try ServerAddress.parse(address.absoluteString)
        if let existing = try server, existing != normalized { throw CaptureError.differentServer }
        try JSONEncoder().encode(normalized.absoluteString)
            .write(to: root.appendingPathComponent("server.json"), options: .atomic)
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }
}
