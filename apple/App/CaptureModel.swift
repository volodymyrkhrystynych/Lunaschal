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
    @Published private(set) var journalRecords: [SyncChange] = []
    @Published private(set) var pendingEdits: [PendingEdit] = []
    @Published private(set) var libraryRecords: [SyncChange] = []
    @Published private(set) var downloadingLibrary = false
    @Published private(set) var libraryMessage: String?
    let store: CaptureStore
    let replica: ReplicaStore
    let media: MediaStore
    let recorder: Recorder
    private let watchReceiver: WatchReceiver
    private let syncer: CaptureSync
    private let replicaSyncer: ReplicaSync
    private let librarySyncer: ReplicaSync
    private var activeAPI: JournalAPI?
    private var libraryAPI: JournalAPI?
    private var token: String?
    private var syncingTask: Task<Void, Never>?

    init(store: CaptureStore) throws {
        self.store = store
        replica = try ReplicaStore(url: store.root.appendingPathComponent("replica.sqlite"))
        media = try MediaStore(root: store.root.appendingPathComponent("downloaded-media", isDirectory: true))
        recorder = Recorder(store: store)
        watchReceiver = try WatchReceiver(store: store)
        syncer = CaptureSync(store: store)
        replicaSyncer = ReplicaSync(store: replica)
        librarySyncer = ReplicaSync(store: replica)
        try store.recoverInterruptedRecordings()
        server = try store.server
        if let server { token = try SessionToken.read(server: server) }
        signedIn = token != nil
        captures = try store.list()
        journalRecords = try replica.records(collection: "journal_entries")
        pendingEdits = try replica.edits()
        libraryRecords = try replica.records(collection: "fics")
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

    func reload() {
        do {
            captures = try store.list()
            journalRecords = try replica.records(collection: "journal_entries")
            pendingEdits = try replica.edits()
            libraryRecords = try replica.records(collection: "fics")
        } catch { message = error.localizedDescription }
    }

    func saveText(_ text: String) -> Bool {
        do {
            try store.save(Capture(text: text.trimmingCharacters(in: .whitespacesAndNewlines)))
            reload()
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
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
            token = value
            signedIn = true
            message = nil
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func signOut() {
        cancelSync()
        do {
            if let server { try SessionToken.remove(server: server) }
            token = nil
            signedIn = false
        } catch { message = error.localizedDescription }
    }

    func requestSync() {
        guard UIApplication.shared.applicationState == .active,
              !syncing, signedIn, let server, let token else { return }
        syncing = true
        syncingTask = Task {
            defer { syncing = false; activeAPI = nil; reload() }
            do {
                let api = try JournalAPI(server: server, token: token, allowCellular: allowCellular)
                activeAPI = api
                try await syncer.run(using: api)
                try await replicaSyncer.run(using: api, collections: [
                    "journal_entries", "journal_attachments", "fics", "study_sources",
                    "papers", "conversations", "knowledge_archives",
                ])
                syncMessage = nil
            } catch {
                if Task.isCancelled { return }
                // Being offline is normal; don't show an alert every retry.
                syncMessage = error.localizedDescription
                if let failure = error as? HTTPFailure, [401, 403].contains(failure.status) {
                    signedIn = false
                }
            }
        }
    }

    func cancelSync() {
        syncingTask?.cancel()
        activeAPI?.cancel()
        libraryAPI?.cancel()
    }

    func downloadLibrary() async {
        guard !downloadingLibrary, signedIn, let server, let token else { return }
        downloadingLibrary = true
        libraryMessage = "Downloading reading content over Wi-Fi…"
        defer { downloadingLibrary = false; libraryAPI = nil; reload() }
        do {
            let api = try JournalAPI(server: server, token: token, allowCellular: false)
            libraryAPI = api
            var collections = ["fic_chapters", "fic_folders", "fic_bookmarks", "messages",
                               "paper_pages", "paper_page_images", "newspaper_issues", "newspaper_frontpages"]
            if UserDefaults.standard.bool(forKey: "downloadKnowledge") { collections.append("wiki_articles") }
            try await librarySyncer.run(using: api, collections: collections, sendEdits: false)
            let gigabytes = max(1, UserDefaults.standard.integer(forKey: "libraryBudgetGB") == 0
                ? 20 : UserDefaults.standard.integer(forKey: "libraryBudgetGB"))
            for collection in MediaDescriptor.collections {
                if UserDefaults.standard.object(forKey: "download-\(collection)") as? Bool == false { continue }
                var after = ""
                while true {
                    let page = try await api.mediaPage(collection: collection, after: after)
                    for item in page.items where item.available {
                        try Task.checkCancellation()
                        if try media.reuse(item) { continue }
                        var offset = try media.offset(for: item, budget: Int64(gigabytes) * 1024 * 1024 * 1024)
                        guard let size = item.size else { throw MediaError.invalidManifest }
                        if size == 0 { try media.append(Data(), to: item, offset: 0) }
                        while offset < size {
                            libraryMessage = "Downloading \(collection.replacingOccurrences(of: "_", with: " ")): \(offset / 1024) / \(size / 1024) KB"
                            let chunk = try await api.mediaChunk(item, offset: offset, count: min(1024 * 1024, size - offset))
                            try media.append(chunk, to: item, offset: offset)
                            offset += Int64(chunk.count)
                        }
                        try await media.finish(item)
                    }
                    if !page.hasMore { break }
                    guard page.after > after else { throw MediaError.invalidManifest }
                    after = page.after
                }
            }
            libraryMessage = "Reading text and available active media downloaded. Archive videos are excluded."
        } catch { libraryMessage = error.localizedDescription }
    }

    func pauseLibrary() { libraryAPI?.cancel() }

    func removeLibraryMedia() {
        guard !downloadingLibrary else { return }
        do {
            try media.removeDownloadedCopies()
            libraryMessage = "Downloaded media removed. Captures and server originals are retained."
            reload()
        } catch { message = error.localizedDescription }
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
                item.state = .pending
                item.lastError = nil
                try store.save(item)
            }
            reload()
            requestSync()
        } catch { message = error.localizedDescription }
    }
}
