import Foundation

@MainActor
public final class CaptureSync {
    public private(set) var isRunning = false
    private let store: CaptureStore
    private let uploads: RecordingUploadStore?
    private let transfers: TransferStore?
    private let now: () -> Date
    /// When each meal's weather was last asked for, this launch.
    private var weatherAsked: [String: Date] = [:]

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
        for listed in try store.list().reversed() where listed.state == .pending {
            try Task.checkCancellation()
            let attempt = try transfers?.begin(listed.id, now: now())
            if transfers != nil && attempt == nil { continue }
            // Read again: the list was taken before the sends ahead of this one
            // awaited, and the entry's words may have been edited since.
            var capture = try store.load(listed.id)
            guard capture.state == .pending else {
                if let attempt { try transfers?.finish(attempt, outcome: .cancelled, now: now()) }
                continue
            }
            // Recorded before sending, so a crash mid-send leaves it locked.
            let mayBeOnServer = capture.mayBeOnServer
            capture.mayBeOnServer = true
            try store.save(capture)
            do {
                try await transport.send(capture, audioURL: capture.attachmentID == nil ? nil : store.audioURL(capture),
                                         files: capture.files.map(store.fileURL),
                                         clips: capture.clips.map(store.clipURL))
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
                // Failed before the entry could reach the server: as editable as before.
                if JournalAPI.failedBeforeSending(error) { capture.mayBeOnServer = mayBeOnServer }
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
        // A meal's weather arrives a moment after it is saved; ask until it has
        // some, but only for a day, and no more than every ten minutes each: a
        // meal saved with no location never gets any, and was asked about on
        // every pass forever.
        for var capture in try store.list().filter({ $0.state == .synced && $0.kind == .food && $0.entryID == nil && $0.weather == nil
            && now().timeIntervalSince($0.createdAt) < 86_400 }) {
            try Task.checkCancellation()
            if let asked = weatherAsked[capture.id], now().timeIntervalSince(asked) < 600 { continue }
            weatherAsked[capture.id] = now()
            guard let weather = try? await transport.foodWeather(capture.id) else { continue }
            capture.weather = weather
            try store.save(capture)
        }
        // A journal capture's title, polished text and transcripts are read from
        // the replica's copy of its entry, which sync already brings; they used
        // to be fetched again here, thirty requests every pass.
    }
}
