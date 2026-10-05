import Foundation
import SwiftUI
import LunaschalCore
import AVFoundation
import UIKit

@MainActor
final class CaptureModel: ObservableObject {
    @Published private(set) var captures: [Capture] = []
    /// What the Capture tab has staged (clips, photos, files) and not yet saved.
    @Published private(set) var draft = CaptureDraft()
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
    /// The Daily tab: what this device has logged, and the server's record of today.
    @Published private(set) var dailyLogs: [DailyLog] = []
    @Published private(set) var dailyStatus: DailyStatus?
    @Published private(set) var selfieThumbnail: (id: String, data: Data)?
    /// The last weather the server sent, kept on disk so it shows offline.
    @Published private(set) var weather: WeatherDay?
    private var weatherFetchedAt: Date?
    let location = LocationProvider()
    /// The Workout page: queued sets, and the server's recent exercises and workouts.
    @Published private(set) var workoutLogs: [WorkoutLog] = []
    @Published private(set) var recentExercises: [RecentExercise] = []
    @Published private(set) var recentWorkouts: [WorkoutSession] = []
    let workouts: WorkoutStore
    private let workoutSyncer: WorkoutSync
    /// Chat voice messages not yet on the server, and whatever the last pass said about them.
    @Published private(set) var chatRecordingQueue: [ChatRecording] = []
    let chatRecordings: ChatRecordingStore
    private let chatRecordingSyncer: ChatRecordingSync
    /// Todo tab changes the server hasn't had yet, and what it last turned down.
    @Published private(set) var todoQueue: [TodoOp] = []
    @Published var todoRefusals: [String] = []
    let todoOutbox: TodoOutbox
    private let todoSyncer: TodoSync
    /// Bumped after each sync pass, so Chat can look for what it uploaded.
    @Published private(set) var syncPasses = 0
    /// A fix the server hasn't been told about yet.
    private var unsentFix: (latitude: Double, longitude: Double)?
    let store: CaptureStore
    let replica: ReplicaStore
    let drawings: DrawingStore
    let drawingPublications: DrawingPublicationStore
    let uploads: RecordingUploadStore
    let transfers: TransferStore
    let media: MediaStore
    let daily: DailyStore
    let recorder: Recorder
    private let watchReceiver: WatchReceiver
    private let syncer: CaptureSync
    private let dailySyncer: DailySync
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
        daily = try DailyStore(root: store.root.appendingPathComponent("daily", isDirectory: true))
        dailySyncer = DailySync(store: daily)
        workouts = try WorkoutStore(root: store.root.appendingPathComponent("workouts", isDirectory: true))
        workoutSyncer = WorkoutSync(store: workouts)
        chatRecordings = try ChatRecordingStore(root: store.root.appendingPathComponent("chat-recordings", isDirectory: true))
        chatRecordingSyncer = ChatRecordingSync(store: chatRecordings)
        todoOutbox = try TodoOutbox(root: store.root.appendingPathComponent("todo-outbox", isDirectory: true))
        todoSyncer = TodoSync(outbox: todoOutbox)
        try chatRecordings.recoverInterrupted()
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
        draft = try store.draft()
        dailyLogs = try daily.list()
        workoutLogs = try workouts.list()
        chatRecordingQueue = try chatRecordings.list()
        todoQueue = try todoOutbox.list()
        recentExercises = (try? JSONDecoder().decode([RecentExercise].self, from: Data(contentsOf: workoutCache("recent")))) ?? []
        recentWorkouts = (try? JSONDecoder().decode([WorkoutSession].self, from: Data(contentsOf: workoutCache("sessions")))) ?? []
        weather = try? JSONDecoder().decode(WeatherDay.self, from: Data(contentsOf: weatherCache))
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
        location.onFix = { [weak self] fix in
            self?.unsentFix = (fix.coordinate.latitude, fix.coordinate.longitude)
            self?.requestSync()
        }
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
            draft = try store.draft()
            dailyLogs = try daily.list()
            workoutLogs = try workouts.list()
            chatRecordingQueue = try chatRecordings.list()
            todoQueue = try todoOutbox.list()
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

    /// Save entry: the typed text, its links and everything staged become one
    /// capture. A recording still running is stopped into the draft first.
    func saveEntry(_ text: String, youtubeURLs: [String], kind: CaptureKind = .journal) -> Bool {
        if recorder.activeID != nil { recorder.stop() }
        do {
            try store.commitDraft(text: text, youtubeURLs: youtubeURLs, kind: kind, location: location.recent)
            reload(); requestSync(); return true
        } catch { message = error.localizedDescription; reload(); return false }
    }

    // MARK: Workout

    /// Checks and queues one line. Returns the exercise it was read as, which
    /// becomes the selection for the next bare "20, 10", or nil if refused.
    func logWorkout(_ text: String, selected: String?) -> WorkoutEntry? {
        do {
            let (_, entry) = try workouts.log(text, selected: selected)
            reload(); requestSync(); return entry
        } catch { message = error.localizedDescription; return nil }
    }

    func discard(_ item: WorkoutLog) {
        do { try workouts.remove(item) } catch { message = error.localizedDescription }
        reload()
    }

    /// "Rate / location" needs the server; it is not queued.
    func updateWorkout(_ id: String, location: String?, intensity: Int?) async -> Bool {
        guard signedIn, let server, let token else { message = "Connect to your server to rate a workout."; return false }
        do {
            let api = try JournalAPI(server: server, token: token, allowCellular: allowCellular)
            try await api.updateWorkout(id, location: location, intensity: intensity)
            await refreshWorkouts(using: api)
            return true
        } catch { message = error.localizedDescription; return false }
    }

    // MARK: Chat

    /// A client for the Chat tab's calls, which all need the server.
    func chatAPI() -> JournalAPI? {
        guard signedIn, let server, let token else { return nil }
        return try? JournalAPI(server: server, token: token, allowCellular: allowCellular)
    }

    /// Queues a Todo tab change and starts sending it.
    func queueTodo(_ change: TodoChange) {
        do {
            try todoOutbox.append(change)
            todoQueue = try todoOutbox.list()
        } catch { message = error.localizedDescription; return }
        requestSync()
    }

    /// A to-do change the server can't take right now waits for the next
    /// pass without holding up the uploads after it.
    private func sendTodoChanges(using api: JournalAPI) async throws {
        guard !todoQueue.isEmpty || !((try? todoOutbox.list()) ?? []).isEmpty else { return }
        do {
            todoRefusals += try await todoSyncer.run(using: api)
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
            if (error as? URLError) != nil { throw error }
        }
        todoQueue = (try? todoOutbox.list()) ?? todoQueue
    }

    func discard(_ item: ChatRecording) {
        do { try chatRecordings.remove(item) } catch { message = error.localizedDescription }
        reload()
    }

    private func workoutCache(_ name: String) -> URL { workouts.root.appendingPathComponent("\(name).cache") }

    private func refreshWorkouts(using api: JournalAPI) async {
        if let recent = try? await api.recentExercises() {
            recentExercises = recent
            try? JSONEncoder().encode(recent).write(to: workoutCache("recent"), options: .atomic)
        }
        if let sessions = try? await api.recentWorkouts(limit: 4) {
            recentWorkouts = sessions
            try? JSONEncoder().encode(sessions).write(to: workoutCache("sessions"), options: .atomic)
        }
    }

    // MARK: Daily

    func logWeight(_ weight: Double) -> Bool { logDaily { try $0.logWeight(weight) } }

    func logCalories(_ calories: Int, description: String) -> Bool {
        logDaily { try $0.logCalories(calories, description: description) }
    }

    func logSelfie(_ image: UIImage) -> Bool {
        logDaily { store in
            guard let jpeg = image.jpegData(compressionQuality: 0.85) else { throw DailyError.missingImage }
            return try store.logSelfie(jpeg: jpeg)
        }
    }

    /// Drops a log the server refused; nothing else can make it succeed.
    func discard(_ log: DailyLog) {
        do { try daily.remove(log) } catch { message = error.localizedDescription }
        reload()
    }

    private func logDaily(_ make: (DailyStore) throws -> DailyLog) -> Bool {
        do { _ = try make(daily); reload(); requestSync(); return true }
        catch { message = error.localizedDescription; return false }
    }

    /// Reads the server's record of today, and the selfie's thumbnail when this
    /// device doesn't already hold the picture. Failing here fails nothing else.
    private func refreshDaily(using api: JournalAPI) async {
        let day = DayKey.of(Date())
        guard let status = try? await api.dailyStatus(day: day) else { return }
        dailyStatus = status
        if let selfie = status.selfie, selfieThumbnail?.id != selfie.id,
           let data = try? await api.selfieThumbnail(selfie.id) {
            selfieThumbnail = (selfie.id, data)
        }
    }

    // Not `.json`: the capture store reads every .json in its root as a capture.
    private var weatherCache: URL { store.root.appendingPathComponent("weather-today.cache") }

    /// The forecast changes hourly at most, and every sync would otherwise ask.
    private func refreshWeather(using api: JournalAPI) async {
        // A new fix moves the forecast to where the phone is now.
        if let fix = unsentFix,
           let fresh = try? await api.updateWeatherLocation(latitude: fix.latitude, longitude: fix.longitude) {
            unsentFix = nil
            return keepWeather(fresh)
        }
        if let weatherFetchedAt, Date().timeIntervalSince(weatherFetchedAt) < 10 * 60,
           weather?.isFor(day: DayKey.of(Date())) == true { return }
        guard let fresh = try? await api.weatherToday() else { return }
        keepWeather(fresh)
    }

    private func keepWeather(_ fresh: WeatherDay) {
        weather = fresh
        weatherFetchedAt = Date()
        try? JSONEncoder().encode(fresh).write(to: weatherCache, options: .atomic)
    }

    /// Copies something just picked into the draft.
    func stage(_ make: (CaptureStore) throws -> CaptureFile) {
        do { _ = try make(store) } catch { message = error.localizedDescription }
        reload()
    }

    func discard(_ file: CaptureFile) {
        do { try store.discardStaged(file) } catch { message = error.localizedDescription }
        reload()
    }

    func discard(_ clip: CaptureClip) {
        if recorder.activeID == clip.attachmentID { recorder.stop() }
        do { try store.discard(clip) } catch { message = error.localizedDescription }
        reload()
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
                || daily.list().contains { $0.state == .pending }
                || workouts.list().contains { $0.state == .pending }
                || drawingPublications.all().contains { $0.state == "pending" }
                || !todoOutbox.list().isEmpty, signedIn: signedIn,
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
            syncing = false; activeAPI = nil; reload(); syncPasses += 1
            watchReceiver.sendServerReceipts()
            onBackgroundSyncNeeded?()
        }
        do {
            try Task.checkCancellation()
            let api = try JournalAPI(server: server, token: token, allowCellular: allowCellular, uploads: uploads)
            activeAPI = api
            // First: a voice message is a question someone is waiting on.
            try await chatRecordingSyncer.run(using: api)
            try await sendTodoChanges(using: api)
            try await syncer.run(using: api)
            try await dailySyncer.run(using: api)
            try await workoutSyncer.run(using: api)
            await refreshWorkouts(using: api)
            await refreshDaily(using: api)
            await refreshWeather(using: api)
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
            // Anything ticked off while this pass was busy uploading.
            try await sendTodoChanges(using: api)
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
