import Foundation

@MainActor
public final class CaptureSync {
    public private(set) var isRunning = false
    private let store: CaptureStore
    private let uploads: RecordingUploadStore?

    public init(store: CaptureStore, uploads: RecordingUploadStore? = nil) {
        self.store = store
        self.uploads = uploads
    }

    public func run(using transport: JournalTransport) async throws {
        guard !isRunning else { return }
        isRunning = true
        defer { isRunning = false }
        // Recover cleanup interrupted after a durable acknowledgement.
        for capture in try store.list() where capture.state == .synced {
            try uploads?.discardAfterSync(capture)
        }
        for var capture in try store.list().reversed() where capture.state == .pending {
            try Task.checkCancellation()
            do {
                try await transport.send(capture, audioURL: capture.attachmentID == nil ? nil : store.audioURL(capture))
                capture.state = .synced
                capture.lastError = nil
                try store.save(capture)
            } catch {
                capture.lastError = error.localizedDescription
                if let http = error as? HTTPFailure {
                    if http.status == 401 || http.status == 403 { throw error }
                    if !http.retryAutomatically { capture.state = .failed }
                }
                try store.save(capture)
                // A rejected individual capture does not block the next one.
                if capture.state != .failed { throw error }
            }
            try uploads?.discardAfterSync(try store.load(capture.id))
        }
        // Read back server titles and transcripts for this device's recent
        // captures. This is deliberately not historical library replication.
        for var capture in try store.list().filter({ $0.state == .synced }).prefix(30) {
            try Task.checkCancellation()
            do {
                capture.snapshot = try await transport.fetch(capture.id)
                capture.lastError = nil
                try store.save(capture)
            } catch let error as HTTPFailure where error.status == 404 {
                // Never resurrect an entry deleted on another device.
                capture.lastError = "Removed on server. This device still holds its original capture."
                try store.save(capture)
            }
        }
    }
}
