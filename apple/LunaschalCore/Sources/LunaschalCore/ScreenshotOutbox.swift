import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

public struct SharedScreenshot: Codable, Equatable {
    public let id: String
    public let capturedAt: String
    public let filename: String
    public let contentType: String
}

public protocol ScreenshotTransport {
    func sendScreenshot(_ shot: SharedScreenshot, file: URL) async throws
}

/// Immutable per-image folders let the extension publish while the app syncs.
/// The server owns grouping; no journal entry is created on the client.
public final class ScreenshotOutbox {
    public let root: URL

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    @discardableResult
    public func append(file: URL, filename: String, contentType: String,
                       now: Date = Date(), timeZone: TimeZone = .current) throws -> SharedScreenshot {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = timeZone
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let shot = SharedScreenshot(id: ULID.make(now: now), capturedAt: formatter.string(from: now),
                                    filename: filename, contentType: contentType)
        let staging = root.appendingPathComponent(".staging-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: staging) }
        try FileManager.default.copyItem(at: file, to: staging.appendingPathComponent("image"))
        try JSONEncoder().encode(shot).write(to: staging.appendingPathComponent("manifest.json"), options: .atomic)
        try FileManager.default.moveItem(at: staging, to: root.appendingPathComponent(shot.id))
        return shot
    }

    public func list() throws -> [SharedScreenshot] {
        try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { ULID.isValid($0.lastPathComponent) }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .map { directory in
                let shot = try JSONDecoder().decode(SharedScreenshot.self,
                    from: Data(contentsOf: directory.appendingPathComponent("manifest.json")))
                guard shot.id == directory.lastPathComponent else { throw CocoaError(.fileReadCorruptFile) }
                return shot
            }
    }

    public func file(for shot: SharedScreenshot) -> URL {
        root.appendingPathComponent(shot.id).appendingPathComponent("image")
    }

    /// Request bodies handed to a background `URLSession`. They outlive the
    /// share extension, so they live beside the shots in the App Group rather
    /// than in a temporary directory the extension takes with it. Dot-named, so
    /// `list()` never reads it as a shot.
    public var bodies: URL { root.appendingPathComponent(".bodies", isDirectory: true) }

    public func body(for shot: SharedScreenshot) -> URL {
        bodies.appendingPathComponent(shot.id).appendingPathExtension("multipart")
    }

    /// A shot already handed to the background session is not handed twice.
    public func isHandedOff(_ shot: SharedScreenshot) -> Bool {
        FileManager.default.fileExists(atPath: body(for: shot).path)
    }

    /// Bodies whose shot has been delivered (its folder is gone) once they are
    /// old enough that no background upload can still be reading them.
    public func pruneBodies(olderThan age: TimeInterval = 24 * 60 * 60, now: Date = Date()) {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: bodies, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        for file in files {
            let id = file.deletingPathExtension().lastPathComponent
            guard !FileManager.default.fileExists(atPath: root.appendingPathComponent(id).path) else { continue }
            let modified = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            if let modified, now.timeIntervalSince(modified) < age { continue }
            try? FileManager.default.removeItem(at: file)
        }
    }

    /// Nonblocking process lock: the app and extension must not upload separate
    /// portions of the same queue concurrently. OS releases it after termination.
    public func run(using transport: ScreenshotTransport) async throws {
        let descriptor = open(root.appendingPathComponent(".upload.lock").path, O_CREAT | O_RDWR, mode_t(0o600))
        guard descriptor >= 0 else { throw CocoaError(.fileWriteUnknown) }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else { return }
        defer { _ = flock(descriptor, LOCK_UN) }
        for shot in try list() {
            try Task.checkCancellation()
            try await transport.sendScreenshot(shot, file: file(for: shot))
            try FileManager.default.removeItem(at: root.appendingPathComponent(shot.id))
        }
        pruneBodies()
    }

    /// `run`, retried a few times while the server is unreachable. A share
    /// happens the moment the phone wakes, which is exactly when a VPN tunnel
    /// (Tailscale's MagicDNS name included) may not be answering yet; a few
    /// seconds is often all it takes. Any other failure is returned at once.
    public func runRetrying(using transport: ScreenshotTransport,
                            delays: [TimeInterval] = [1, 2, 3],
                            sleep: (TimeInterval) async throws -> Void = { try await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000)) }) async throws {
        var remaining = delays[...]
        while true {
            do { return try await run(using: transport) }
            catch let error as URLError where error.isUnreachable {
                guard let delay = remaining.popFirst() else { throw error }
                try await sleep(delay)
            }
        }
    }
}

extension JournalAPI: ScreenshotTransport {
    /// The upload request and its multipart body file. Shared by the
    /// foreground send and the share extension's background handoff, so both
    /// post exactly the same thing; the caller owns (and removes) the body.
    public func screenshotUploadRequest(_ shot: SharedScreenshot, file: URL) throws -> (request: URLRequest, body: URL) {
        let (body, boundary) = try writeMultipart(
            fields: [("attachmentId", shot.id), ("capturedAt", shot.capturedAt)],
            files: [MultipartFile(field: "file", filename: shot.filename, contentType: shot.contentType, source: file)])
        var req = request("api/journal/screenshots", method: "POST")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        return (req, body)
    }

    public func sendScreenshot(_ shot: SharedScreenshot, file: URL) async throws {
        let (req, body) = try screenshotUploadRequest(shot, file: file)
        defer { try? FileManager.default.removeItem(at: body) }
        let (data, response) = try await session.upload(for: req, fromFile: body)
        try check(data, response)
        try Self.validateScreenshotReceipt(data, attachmentID: shot.id)
    }

    static func validateScreenshotReceipt(_ data: Data, attachmentID: String) throws {
        struct Receipt: Decodable {
            struct Attachment: Decodable { let id: String; let entryId: String }
            let id: String
            let attachment: Attachment
        }
        let receipt = try JSONDecoder().decode(Receipt.self, from: data)
        guard ULID.isValid(receipt.id), receipt.attachment.id == attachmentID,
              receipt.attachment.entryId == receipt.id else { throw CocoaError(.fileReadCorruptFile) }
    }
}
