import Foundation

@MainActor
public final class CaptureSync {
    public private(set) var isRunning = false
    private let store: CaptureStore
    private let uploads: RecordingUploadStore?
    private let transfers: TransferStore?
    private let now: () -> Date

    public init(store: CaptureStore, uploads: RecordingUploadStore? = nil,
                transfers: TransferStore? = nil, now: @escaping () -> Date = Date.init) {
        self.store = store
        self.uploads = uploads
        self.transfers = transfers
        self.now = now
    }

    public func run(using transport: JournalTransport) async throws {
        guard !isRunning else { return }
        isRunning = true
        defer { isRunning = false }
        try transfers?.recoverInterrupted(now: now())
        // Recover cleanup interrupted after a durable acknowledgement.
        for capture in try store.list() where capture.state == .synced {
            try uploads?.discardAfterSync(capture)
            try transfers?.discardAfterSync(capture)
        }
        if try transfers?.all().contains(where: { $0.state == .authentication }) == true {
            throw HTTPFailure(status: 401)
        }
        for var capture in try store.list().reversed() where capture.state == .pending {
            try Task.checkCancellation()
            let attempt = try transfers?.begin(capture.id, now: now())
            if transfers != nil && attempt == nil { continue }
            do {
                try await transport.send(capture, audioURL: capture.attachmentID == nil ? nil : store.audioURL(capture))
                if let attempt, try transfers?.isCurrent(attempt) != true { throw CancellationError() }
                capture.state = .synced
                capture.lastError = nil
                try store.save(capture)
            } catch {
                if let attempt, try transfers?.isCurrent(attempt) != true { throw CancellationError() }
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled {
                    if let attempt { try transfers?.finish(attempt, outcome: .cancelled, now: now()) }
                    throw error
                }
                capture.lastError = error.localizedDescription
                var outcome = TransferStore.Outcome.retryable
                if let http = error as? HTTPFailure {
                    if http.status == 401 || http.status == 403 { outcome = .authentication }
                    else if !http.retryAutomatically { capture.state = .failed; outcome = .rejected }
                }
                try store.save(capture)
                if let attempt { try transfers?.finish(attempt, outcome: outcome, now: now()) }
                // A rejected individual capture does not block the next one.
                if capture.state != .failed { throw error }
            }
            try uploads?.discardAfterSync(try store.load(capture.id))
            try transfers?.discardAfterSync(try store.load(capture.id))
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
