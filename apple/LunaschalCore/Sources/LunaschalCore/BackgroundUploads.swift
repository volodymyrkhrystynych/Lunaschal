import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Hands a prepared request to the system to send. The app's implementation is
/// a background `URLSession`, which keeps uploading after Lunaschal is closed;
/// the system relaunches the app to report the result, which is written back
/// into `BackgroundUploads` with `finish`.
public protocol BackgroundUploading: AnyObject {
    func handOff(_ request: URLRequest, body: URL, taskID: String) throws
}

/// The device's record of the journal attachments too big to send inline — a
/// video, a Live Photo — while the system uploads them in the background.
///
/// Each one goes through three states, one folder per attachment id:
/// handed off (`body.multipart` + `request.json`), then landed (`ack.json`, the
/// server's answer) or failed (`failed.json`, its status). The capture stays
/// pending throughout and the outbox is still what confirms it: a landed ack is
/// validated exactly like an inline upload's would be, and only then is the
/// capture marked synced and this record dropped. The server's attachment-id
/// replay contract covers the rest, so a background upload that landed after
/// the device forgot about it is answered, not stored twice.
public final class BackgroundUploads {
    /// Anything over this goes up in the background, whatever its kind.
    public static let threshold = 20 * 1024 * 1024

    public enum State: Equatable {
        case none
        case inFlight
        case landed(Data)
        case failed(status: Int)
    }

    struct Handoff: Codable {
        let captureID: String
        let attachmentID: String
        let boundary: String
    }

    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    /// Whether this file is one the background session should carry.
    public static func shouldHandOff(_ file: CaptureFile, size: Int) -> Bool {
        file.isVideo || file.isLivePhoto || size > threshold
    }

    public func state(_ attachmentID: String) throws -> State {
        let folder = try folder(attachmentID)
        let ack = folder.appendingPathComponent("ack.json")
        if fm.fileExists(atPath: ack.path) { return .landed(try Data(contentsOf: ack)) }
        let failed = folder.appendingPathComponent("failed.json")
        if fm.fileExists(atPath: failed.path) {
            return .failed(status: (try? JSONDecoder().decode(Int.self, from: Data(contentsOf: failed))) ?? 0)
        }
        return fm.fileExists(atPath: folder.appendingPathComponent("request.json").path) ? .inFlight : .none
    }

    /// Whether any of `capture`'s attachments is still on its way up. Hand-off
    /// happens only once everything else in the capture has landed, so while
    /// this is true there is nothing to re-send.
    public func isWaiting(on capture: Capture) throws -> Bool {
        try capture.files.contains { try state($0.attachmentID) == .inFlight }
    }

    /// Writes the multipart body where the background session can read it for
    /// as long as it takes — the system reads the file, not our memory — and
    /// records the hand-off before the request exists, so a crash between the
    /// two leaves something `reconcile` can clear.
    public func prepare(_ capture: Capture, file: CaptureFile, fileURL: URL) throws -> (body: URL, boundary: String) {
        let folder = try folder(file.attachmentID)
        try reset(file.attachmentID)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        let multipart = try AttachmentMultipart(capture: capture, file: file, fileURL: fileURL)
        let body = folder.appendingPathComponent("body.multipart")
        try fm.moveItem(at: multipart.url, to: body)
        let handoff = Handoff(captureID: capture.id, attachmentID: file.attachmentID, boundary: multipart.boundary)
        try JSONEncoder().encode(handoff).write(to: folder.appendingPathComponent("request.json"), options: .atomic)
        return (body, multipart.boundary)
    }

    /// The system's report for one task. 2xx keeps the server's answer for the
    /// outbox to validate; anything else is remembered by status so the next
    /// sync pass can decide whether to retry. A transport error (`status` nil)
    /// clears the record: the next pass hands the file off again.
    public func finish(_ attachmentID: String, status: Int?, data: Data) throws {
        let folder = try folder(attachmentID)
        guard fm.fileExists(atPath: folder.appendingPathComponent("request.json").path) else { return }
        guard let status else { return try reset(attachmentID) }
        if (200..<300).contains(status) {
            try data.write(to: folder.appendingPathComponent("ack.json"), options: .atomic)
        } else {
            try JSONEncoder().encode(status).write(to: folder.appendingPathComponent("failed.json"), options: .atomic)
        }
        try? fm.removeItem(at: folder.appendingPathComponent("body.multipart"))
    }

    /// Clears hand-offs the system no longer has a task for — the app was
    /// force-quit, which cancels background uploads, or it crashed between
    /// recording a hand-off and starting it. `live` is every task the session
    /// still reports.
    public func reconcile(live: Set<String>) throws {
        for id in try attachmentIDs() where try state(id) == .inFlight && !live.contains(id) {
            try reset(id)
        }
    }

    public func reset(_ attachmentID: String) throws {
        let folder = try folder(attachmentID)
        if fm.fileExists(atPath: folder.path) { try fm.removeItem(at: folder) }
    }

    /// Once the capture is synced its records are not needed.
    public func discardAfterSync(_ capture: Capture) throws {
        guard capture.state == .synced else { return }
        for file in capture.files { try reset(file.attachmentID) }
    }

    func attachmentIDs() throws -> [String] {
        try fm.contentsOfDirectory(atPath: root.path).filter(ULID.isValid)
    }

    private func folder(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id, isDirectory: true)
    }
}

/// Thrown by a send that left part of a capture with the background session:
/// not a failure, so it neither counts as an attempt nor shows an error.
public struct UploadDeferred: Error {
    public init() {}
}
