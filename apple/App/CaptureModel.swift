import Foundation
import SwiftUI
import LunaschalCore
import AVFoundation
import UIKit

@MainActor
final class CaptureModel: ObservableObject {
    @Published private(set) var captures: [Capture] = []
    @Published private(set) var server: URL?
    @Published private(set) var signedIn = false
    @Published private(set) var syncing = false
    @Published private(set) var signingIn = false
    @Published var message: String?
    @Published private(set) var syncMessage: String?
    @Published var backgroundStatus = "Background sync is idle."
    var onBackgroundSyncNeeded: (() -> Void)?
    private var backgroundSyncing = false
    @Published private(set) var journalRecords: [SyncChange] = []
    @Published private(set) var journalCount = 0
    private var journalLimit = 200
    private var journalQuery = ""
    @Published private(set) var pendingEdits: [PendingEdit] = []
    @Published private(set) var downloadingLibrary = false
    @Published private(set) var libraryMessage: String?
    @Published private(set) var libraryBytes: Int64 = 0
    let store: CaptureStore
    let replica: ReplicaStore
    let drawings: DrawingStore
    let drawingPublications: DrawingPublicationStore
    let uploads: RecordingUploadStore
    let transfers: TransferStore
    let media: MediaStore
    let recorder: Recorder
    private let watchReceiver: WatchReceiver
    private let syncer: CaptureSync
    private let replicaSyncer: ReplicaSync
    private let libraryWorker: LibraryDownload
    private var activeAPI: JournalAPI?
    private var libraryAPI: JournalAPI?
    private var token: String?
    private var syncingTask: Task<Void, Never>?
    private var libraryTask: Task<Void, Never>?

    init(store: CaptureStore) throws {
        self.store = store
        uploads = try RecordingUploadStore(root: store.root.appendingPathComponent("recording-uploads", isDirectory: true))
        transfers = try TransferStore(root: store.root.appendingPathComponent("transfer-state", isDirectory: true))
        drawings = try DrawingStore(root: store.root.appendingPathComponent("drawings", isDirectory: true))
        drawingPublications = try DrawingPublicationStore(root: store.root.appendingPathComponent("drawing-publications", isDirectory: true))
        replica = try ReplicaStore(url: store.root.appendingPathComponent("replica.sqlite"))
        media = try MediaStore(root: store.root.appendingPathComponent("downloaded-media", isDirectory: true))
        recorder = Recorder(store: store)
        watchReceiver = try WatchReceiver(store: store)
        syncer = CaptureSync(store: store, uploads: uploads, transfers: transfers)
        replicaSyncer = ReplicaSync(store: replica)
        libraryWorker = LibraryDownload(replicaURL: store.root.appendingPathComponent("replica.sqlite"),
                                        mediaURL: media.root)
        try store.recoverInterruptedRecordings()
        server = try store.server
        if let server { token = try SessionToken.read(server: server) }
        signedIn = token != nil
        captures = try store.list()
        journalRecords = try replica.records(collection: "journal_entries")
        journalCount = try replica.count(collection: "journal_entries")
        pendingEdits = try replica.edits().filter { $0.operation.collection == "journal_entries" }
        refreshLibraryBytes()
        recorder.onChange = { [weak self] in
            self?.reload()
            self?.requestSync()
        }
        recorder.onError = { [weak self] in self?.message = $0.localizedDescription }
        watchReceiver.onChange = { [weak self] in self?.reload(); self?.requestSync() }
        watchReceiver.onError = { [weak self] in self?.message = $0.localizedDescription }
        watchReceiver.activate()
    }

    var allowCellular: Bool {
        // Defaults to true, including before Settings has ever been opened.
        UserDefaults.standard.object(forKey: "allowCellularSync") as? Bool ?? true
    }

    var backgroundSyncEnabled: Bool {
        UserDefaults.standard.object(forKey: "backgroundSyncEnabled") as? Bool ?? true
    }

    func reload() {
        do {
            captures = try store.list()
            journalRecords = try replica.records(collection: "journal_entries", query: journalQuery, limit: journalLimit)
            journalCount = try replica.count(collection: "journal_entries", query: journalQuery)
            pendingEdits = try replica.edits().filter { $0.operation.collection == "journal_entries" }
        } catch { message = error.localizedDescription }
    }

    private func refreshLibraryBytes() {
        Task {
            do { libraryBytes = try await libraryWorker.usedBytes() }
            catch { message = error.localizedDescription }
        }
    }

    func saveText(_ text: String) -> Bool {
        do {
            try store.save(Capture(text: text.trimmingCharacters(in: .whitespacesAndNewlines)))
            reload()
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func searchJournal(_ query: String) {
        if journalQuery != query { journalLimit = 200 }
        journalQuery = query
        reload()
    }

    func loadMoreJournal() {
        journalLimit += 200
        reload()
    }

    func saveLink(_ link: String, commentary: String) -> Bool {
        do {
            let url = try YouTubeLink.canonical(link)
            let text = commentary.trimmingCharacters(in: .whitespacesAndNewlines)
            try store.save(Capture(text: text.isEmpty ? url : text, youtubeURL: url))
            reload(); requestSync(); return true
        } catch { message = error.localizedDescription; return false }
    }

    func login(address: String, password: String, code: String) async -> Bool {
        guard !signingIn else { return false }
        signingIn = true
        defer { signingIn = false }
        do {
            let url = try ServerAddress.parse(address)
            if let server, server != url { throw CaptureError.differentServer }
            let api = try JournalAPI(server: url, token: nil, allowCellular: allowCellular)
            let value = try await api.login(password: password, code: code)
            try store.bind(to: url)
            server = url
            try SessionToken.save(value, server: url)
            try transfers.resumeAuthentication(now: Date())
            token = value
            signedIn = true
            message = nil
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func signOut() {
        cancelSync()
        pauseLibrary()
        do {
            if let server { try SessionToken.remove(server: server) }
            token = nil
            signedIn = false
            onBackgroundSyncNeeded?()
        } catch { message = error.localizedDescription }
    }

    func requestSync(manual: Bool = false) {
        onBackgroundSyncNeeded?()
        guard UIApplication.shared.applicationState == .active,
              !syncing, !backgroundSyncing, signedIn, let server, let token else { return }
        if manual {
            do { try transfers.retryWaiting(now: Date()) }
            catch { message = error.localizedDescription; return }
        }
        syncing = true
        syncingTask = Task { _ = await performSync(server: server, token: token) }
    }

    func nextBackgroundSync() throws -> Date? {
        try BackgroundSyncPlan.next(captures: store.list(), attempts: transfers.all(),
            hasEdits: replica.edits().contains { $0.state == "pending" }
                || drawingPublications.all().contains { $0.state == "pending" }, signedIn: signedIn,
            enabled: backgroundSyncEnabled, now: Date())
    }

    func syncInBackground() async -> Bool {
        guard !syncing, !Task.isCancelled, signedIn, let server, let token,
              backgroundSyncEnabled else { return false }
        syncing = true; backgroundSyncing = true
        defer { backgroundSyncing = false }
        return await performSync(server: server, token: token)
    }

    func leaveForeground() {
        if !backgroundSyncing { cancelSync() }
        pauseLibrary()
        onBackgroundSyncNeeded?()
    }

    func backgroundPreferenceChanged() {
        if backgroundSyncing { cancelSync() }
        onBackgroundSyncNeeded?()
    }

    private func performSync(server: URL, token: String) async -> Bool {
        defer {
            syncing = false; activeAPI = nil; reload()
            watchReceiver.sendServerReceipts()
            onBackgroundSyncNeeded?()
        }
        do {
            try Task.checkCancellation()
            let api = try JournalAPI(server: server, token: token, allowCellular: allowCellular, uploads: uploads)
            activeAPI = api
            try await syncer.run(using: api)
            try await replicaSyncer.run(using: api, collections: [
                "journal_entries", "journal_attachments", "fics", "study_sources",
                "papers", "conversations", "knowledge_archives", "fic_folders", "fic_bookmarks",
            ])
            for publication in try drawingPublications.all() where publication.state == "pending" {
                try Task.checkCancellation()
                let reply = try await api.publishDrawing(publication, store: drawingPublications)
                try drawingPublications.receive(reply, for: publication)
            }
            if !downloadingLibrary {
                try await libraryWorker.updateText(using: api, collections: LibraryDownload.collections(
                    knowledge: UserDefaults.standard.bool(forKey: "downloadKnowledge")))
            }
            let retry = try transfers.all().compactMap(\.retryAt).min()
            syncMessage = retry.map { "Uploads will retry after \($0.formatted(date: .omitted, time: .shortened))." }
            return true
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { return false }
            // Being offline is normal; don't show an alert every retry.
            syncMessage = error.localizedDescription
            if let failure = error as? HTTPFailure, [401, 403].contains(failure.status) {
                signedIn = false
            }
            return false
        }
    }

    func cancelSync() {
        syncingTask?.cancel()
        activeAPI?.cancel()
    }

    func startLibraryDownload() {
        guard !downloadingLibrary, signedIn, let server, let token else { return }
        downloadingLibrary = true
        libraryMessage = "Downloading reading content over Wi-Fi…"
        let collections = LibraryDownload.collections(
            knowledge: UserDefaults.standard.bool(forKey: "downloadKnowledge"))
        let selected = MediaDescriptor.collections.filter {
            UserDefaults.standard.object(forKey: "download-\($0)") as? Bool != false
        }
        let configured = UserDefaults.standard.integer(forKey: "libraryBudgetGB")
        let gigabytes = max(1, configured == 0 ? 20 : configured)
        libraryTask = Task {
            defer {
                downloadingLibrary = false; libraryAPI = nil; libraryTask = nil
                reload(); refreshLibraryBytes()
            }
            do {
                try Task.checkCancellation()
                let api = try JournalAPI(server: server, token: token, allowCellular: false)
                libraryAPI = api
                let supported = try await libraryWorker.download(using: api, collections: collections,
                    mediaCollections: selected, budget: Int64(gigabytes) * 1024 * 1024 * 1024) { [weak self] status in
                        await self?.showLibraryProgress(status)
                    }
                libraryMessage = !supported.contains("fics") && selected.contains("fics")
                    ? "Downloads complete. Update the server to include PDF books."
                    : "Reading text and available active media downloaded. Archive videos are excluded."
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled {
                    libraryMessage = "Downloads paused. Saved content is available; resume over Wi-Fi."
                } else { libraryMessage = error.localizedDescription }
            }
        }
    }

    private func showLibraryProgress(_ status: String) { libraryMessage = status }

    func pauseLibrary() {
        libraryTask?.cancel()
        libraryAPI?.cancel()
    }

    func removeLibraryMedia() {
        guard !downloadingLibrary else { return }
        do {
            try media.removeDownloadedCopies()
            libraryMessage = "Downloaded media removed. Captures and server originals are retained."
            reload(); refreshLibraryBytes()
        } catch { message = error.localizedDescription }
    }

    func removeLibraryMedia(collection: String, id: String) -> Bool {
        guard !downloadingLibrary else { return false }
        do {
            let freed = try media.removeDownloadedCopy(collection: collection, id: id)
            libraryMessage = "Device copy removed. \(ByteCountFormatter.string(fromByteCount: freed, countStyle: .file)) freed. Shared files may remain for other items."
            reload(); refreshLibraryBytes()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func edit(_ record: SyncChange, content: String, title: String) -> Bool {
        do {
            _ = try replica.queue(record: record, data: ["content": .string(content), "title": .string(title)])
            reload(); requestSync(); return true
        } catch { message = error.localizedDescription; return false }
    }

    func delete(_ record: SyncChange) {
        do {
            _ = try replica.queue(record: record, data: [:], delete: true)
            reload(); requestSync()
        } catch { message = error.localizedDescription }
    }

    func resolve(_ edit: PendingEdit, keepLocal: Bool) {
        do { try replica.resolve(edit, keepLocal: keepLocal); reload(); requestSync() }
        catch { message = error.localizedDescription }
    }

    func retry(_ capture: Capture) {
        do {
            if capture.state == .interrupted {
                // Reject a broken/unfinalized AAC container without erasing it.
                let audio = try AVAudioPlayer(contentsOf: store.audioURL(capture))
                guard audio.duration > 0 else { throw CaptureError.missingAudio }
                try store.finishRecording(capture.id)
            } else {
                var item = try store.load(capture.id)
                guard item.state == .failed else { return }
                try transfers.retryNow(item.id, now: Date())
                item.state = .pending
                item.lastError = nil
                try store.save(item)
            }
            reload()
            requestSync()
        } catch { message = error.localizedDescription }
    }
}
