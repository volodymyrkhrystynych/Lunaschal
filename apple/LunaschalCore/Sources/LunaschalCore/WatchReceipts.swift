import Foundation

public struct WatchServerReceipt: Codable, Equatable {
    public let captureID: String
    public let attachmentID: String

    public init(capture: Capture) throws {
        guard capture.mode != .text, ULID.isValid(capture.id),
              let attachment = capture.attachmentID, ULID.isValid(attachment) else { throw CaptureError.invalidResponse }
        captureID = capture.id
        attachmentID = attachment
    }

    public func matches(_ capture: Capture) -> Bool {
        ULID.isValid(captureID) && ULID.isValid(attachmentID)
            && capture.id == captureID && capture.attachmentID == attachmentID && capture.mode != .text
    }
}

/// Receipt files are intentionally outside the capture manifest namespace.
/// A server receipt means the phone received the upload acknowledgement; it
/// does not claim that transcription finished or that a server backup exists.
public final class WatchReceipts {
    private let store: CaptureStore
    private let fm = FileManager.default
    public init(store: CaptureStore) { self.store = store }

    public func markOrigin(_ capture: Capture) throws {
        try write(WatchServerReceipt(capture: capture), suffix: "watch-origin")
    }

    public func pendingServerReceipts() throws -> [WatchServerReceipt] {
        try store.list().compactMap { capture in
            guard capture.state == .synced,
                  try read(capture, suffix: "watch-origin") != nil,
                  try read(capture, suffix: "watch-server-confirmed") == nil else { return nil }
            return try WatchServerReceipt(capture: capture)
        }
    }

    public func acceptServerReceipt(_ receipt: WatchServerReceipt) throws {
        guard ULID.isValid(receipt.captureID), ULID.isValid(receipt.attachmentID) else { throw CaptureError.invalidID }
        if !fm.fileExists(atPath: store.root.appendingPathComponent(receipt.captureID + ".json").path) {
            // A receipt can be replayed after the user removed the Watch copy.
            // Only an exact, already-durable receipt can acknowledge that replay.
            let saved = store.root.appendingPathComponent(receipt.captureID + ".server-receipt")
            guard try JSONDecoder().decode(WatchServerReceipt.self, from: Data(contentsOf: saved)) == receipt else {
                throw CaptureError.invalidResponse
            }
            return
        }
        let capture = try store.load(receipt.captureID)
        guard receipt.matches(capture), capture.state != .recording,
              capture.state != .interrupted else { throw CaptureError.invalidResponse }
        try write(receipt, suffix: "server-receipt")
    }

    public func confirmDelivery(_ receipt: WatchServerReceipt) throws {
        let capture = try store.load(receipt.captureID)
        guard capture.state == .synced, receipt.matches(capture),
              try read(capture, suffix: "watch-origin") != nil else { throw CaptureError.invalidResponse }
        try write(receipt, suffix: "watch-server-confirmed")
    }

    public func serverReceived(_ capture: Capture) throws -> Bool {
        try read(capture, suffix: "server-receipt") != nil
    }

    /// Called only after the user confirms removal on the Watch. Never a
    /// phone cleanup operation. Missing bytes permit retry after a partial
    /// removal; the manifest stays until the audio removal has succeeded.
    public func removeWatchCopy(_ id: String) throws {
        let capture = try store.load(id)
        guard capture.id == id, capture.state != .recording, capture.state != .interrupted,
              try serverReceived(capture) else { throw CaptureError.invalidResponse }
        let audio = try store.audioURL(capture)
        if fm.fileExists(atPath: audio.path) { try fm.removeItem(at: audio) }
        try fm.removeItem(at: store.root.appendingPathComponent(id + ".json"))
        // Receipts carry no audio; keep the server receipt so a repeated
        // delivery can still be recognized without recreating the capture.
    }

    private func read(_ capture: Capture, suffix: String) throws -> WatchServerReceipt? {
        guard ULID.isValid(capture.id) else { throw CaptureError.invalidID }
        let url = store.root.appendingPathComponent(capture.id + "." + suffix)
        guard fm.fileExists(atPath: url.path) else { return nil }
        let receipt = try JSONDecoder().decode(WatchServerReceipt.self, from: Data(contentsOf: url))
        guard receipt.matches(capture) else { throw CaptureError.invalidResponse }
        return receipt
    }

    private func write(_ receipt: WatchServerReceipt, suffix: String) throws {
        guard ULID.isValid(receipt.captureID), ULID.isValid(receipt.attachmentID) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(receipt).write(to: store.root.appendingPathComponent(receipt.captureID + "." + suffix), options: .atomic)
    }
}
