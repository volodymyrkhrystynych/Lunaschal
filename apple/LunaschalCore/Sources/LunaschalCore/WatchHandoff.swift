import Foundation

public struct WatchEnvelope: Codable {
    public let version: Int
    public let capture: Capture
    public let bytes: Int64
    public let sha256: String

    public init(capture: Capture, bytes: Int64, sha256: String) throws {
        version = 1
        self.capture = capture
        self.bytes = bytes
        self.sha256 = sha256
        try validate()
    }

    public func validate() throws {
        guard version == 1, capture.mode != .text, capture.state == .pending,
              ULID.isValid(capture.id), capture.attachmentID.map(ULID.isValid) == true,
              bytes > 0, bytes <= 512 * 1024 * 1024, sha256.count == 64,
              sha256.allSatisfy({ "0123456789abcdef".contains($0) }) else {
            throw CaptureError.invalidResponse
        }
    }
}

/// The connectivity delegate must keep the system's temporary file before it
/// returns. Staging does that synchronously, separately from the main outbox.
public final class WatchInbox {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func stage(file: URL, metadata: Data) throws {
        let envelope = try JSONDecoder().decode(WatchEnvelope.self, from: metadata)
        try envelope.validate()
        guard try file.resourceValues(forKeys: [.fileSizeKey]).fileSize == Int(envelope.bytes) else {
            throw CaptureError.missingAudio
        }
        let id = UUID().uuidString
        let temporary = root.appendingPathComponent(".\(id)", isDirectory: true)
        try fm.createDirectory(at: temporary, withIntermediateDirectories: true)
        try metadata.write(to: temporary.appendingPathComponent("envelope.json"), options: .atomic)
        try fm.copyItem(at: file, to: temporary.appendingPathComponent("audio.m4a"))
        try fm.moveItem(at: temporary, to: root.appendingPathComponent(id, isDirectory: true))
    }

    /// Returns only durably imported identities. Replays never reset a capture's
    /// upload status or overwrite text/transcripts already received by the phone.
    public func drain(into store: CaptureStore, hash: (URL) throws -> String,
                      onFailure: ((Error) -> Void)? = nil) throws -> [String] {
        var received: [String] = []
        let directories = try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { !$0.lastPathComponent.hasPrefix(".") }
        for directory in directories {
            do {
            let envelope = try JSONDecoder().decode(WatchEnvelope.self,
                from: Data(contentsOf: directory.appendingPathComponent("envelope.json")))
            try envelope.validate()
            let file = directory.appendingPathComponent("audio.m4a")
            guard try file.resourceValues(forKeys: [.fileSizeKey]).fileSize == Int(envelope.bytes),
                  try hash(file) == envelope.sha256 else { throw MediaError.integrity }
            let destination = try store.audioURL(envelope.capture)
            if fm.fileExists(atPath: destination.path) {
                guard try hash(destination) == envelope.sha256 else { throw MediaError.integrity }
            } else {
                let pending = store.root.appendingPathComponent(".watch-\(UUID().uuidString)")
                try fm.copyItem(at: file, to: pending)
                try fm.moveItem(at: pending, to: destination)
            }
            let manifest = store.root.appendingPathComponent(envelope.capture.id).appendingPathExtension("json")
            if fm.fileExists(atPath: manifest.path) {
                let existing = try store.load(envelope.capture.id)
                guard existing.attachmentID == envelope.capture.attachmentID,
                      existing.createdAt == envelope.capture.createdAt,
                      existing.mode == envelope.capture.mode else { throw CaptureError.invalidResponse }
            } else { try store.save(envelope.capture) }
            received.append(envelope.capture.id)
            try fm.removeItem(at: directory)
            } catch {
                if let onFailure { onFailure(error) }
                else { throw error }
            }
        }
        return received
    }
}
