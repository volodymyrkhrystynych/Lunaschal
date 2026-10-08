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
    /// Each loaded journal entry's attachments, in the server's order.
    @Published private(set) var journalAttachments: [String: [JournalAttachmentItem]] = [:]
    /// Calendar occurrences that can border a run of the journal feed, as on the web.
    @Published private(set) var journalOccurrences: [CalendarOccurrence] = []
    /// Series templates and their exceptions; the calendar expands them per view.
    /// Events made here and not yet on the server are laid over the replica's.
    @Published private(set) var calendarEvents: [CalendarEvent] = []
    @Published private(set) var pendingCalendarIDs: Set<String> = []
    @Published private(set) var calendarExceptions: [CalendarException] = []
    /// Wake and sleep per day key: the server's last answer with queued hand-set times on top.
    @Published private(set) var sleepDays: [String: SleepDay] = [:]
    private var journalLimit = 200
    private var journalQuery = ""
    @Published private(set) var pendingEdits: [PendingEdit] = []
    @Published private(set) var downloadingLibrary = false
    @Published private(set) var libraryMessage: String?
    @Published private(set) var libraryBytes: Int64 = 0
    @Published private(set) var libraryTextBytes: Int64 = 0
    /// Fics opened but not yet on the device, the one downloading first.
    @Published private(set) var ficQueue = FicQueue()
    @Published private(set) var ficDownload: FicDownloadStatus?
    @Published private(set) var ficErrors: [String: String] = [:]
    /// Bumped each time a fic page lands, so an open book redraws its chapters.
    @Published private(set) var ficDownloadRevision = 0
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
    /// Watch pomodoro runs the phone has acknowledged but the server hasn't had.
    private let pomodoros: PomodoroStore
    private let pomodoroSyncer: PomodoroSync
    /// Chat voice messages not yet on the server, and whatever the last pass said about them.
    @Published private(set) var chatRecordingQueue: [ChatRecording] = []
    let chatRecordings: ChatRecordingStore
    private let chatRecordingSyncer: ChatRecordingSync
    /// Todo tab changes the server hasn't had yet, and what it last turned down.
    @Published private(set) var todoQueue: [TodoOp] = []
    @Published var todoRefusals: [String] = []
    let todoOutbox: TodoOutbox
    private let todoSyncer: TodoSync
    /// Jobs feed decisions the server hasn't had yet, and what it last turned down.
    @Published private(set) var jobQueue: [JobDecisionOp] = []
    @Published var jobRefusals: [String] = []
    let jobStore: JobStore
    private let jobSyncer: JobSync
    private let calendarOutbox: CalendarOutbox
    private let calendarSyncer: CalendarSyncer
    /// The fic reader's reading spans and last-read chapters, until uploaded.
    let ficActivity: FicActivityStore
    private let ficActivitySyncer: FicActivitySync
    /// What the last sync pass (or local change) touched: screens reload when
    /// it touches what they show, and keep their place.
    @Published private(set) var syncChanges = SyncChanges()
    /// A fix the server hasn't been told about yet.
    private var unsentFix: (latitude: Double, longitude: Double)?
    let store: CaptureStore
    /// The UI's connection: small writes a tap makes, and lookups too quick to
    /// be worth a hop. Lists and anything decoded in bulk go through `reader`.
    let replica: ReplicaStore
    /// Reads the replica off the main thread, on its own connection.
    let reader: ReplicaReader
    let drawings: DrawingStore
    let drawingPublications: DrawingPublicationStore
    /// The iPad's paginated notes canvases, blank or over a newspaper issue.
    let notebooks: NotebookStore
    let uploads: RecordingUploadStore
    let transfers: TransferStore
    let media: MediaStore
    let daily: DailyStore
    let healthState: HealthStateStore
    @Published private(set) var healthStatus = HealthStateStore.Status()
    let recorder: Recorder
    private let watchReceiver: WatchReceiver
    private let syncer: CaptureSync
    private let dailySyncer: DailySync
    private let healthSyncer: HealthSync
    private lazy var healthSource: HealthKitSource? = HealthKitSource.isAvailable ? HealthKitSource() : nil
    private let replicaSyncer: ReplicaSync
    private let libraryWorker: LibraryDownload
    private var token: String?
    private var syncingTask: Task<Bool, Never>?
    /// A local change waits a moment before syncing, so a burst of them is one pass.
    private var pendingRequest: Task<Void, Never>?
    /// A pass asked for while one was running: run once it's done.
    private var queuedMode: SyncMode?
    private var libraryTask: Task<Void, Never>?
    private var ficTask: Task<Void, Never>?
    /// One client per network setting, kept for as long as the server and the
    /// sign-in stay the same. Each pass used to build its own, and with it a
    /// new connection and TLS handshake every 30 seconds.
    private var apis: [Bool: JournalAPI] = [:]
    private var apisFor: (server: URL, token: String)?
    /// The server's sync capabilities, asked once rather than every pass.
    private var capabilities: SyncCapabilities?
    private var journalLoads = 0
    /// When the library may next be re-downloaded after its cursor expired.
    private var libraryRetryAt: Date?
    private static let libraryStaleKey = "libraryNeedsBootstrap"
    /// The bulk download yielded to an opened fic and picks up once the queue empties.
    private var resumeLibraryAfterFics = false
    private var ficEstimate = TransferEstimate(started: Date())

    init(store: CaptureStore) throws {
        self.store = store
        uploads = try RecordingUploadStore(root: store.root.appendingPathComponent("recording-uploads", isDirectory: true))
        transfers = try TransferStore(root: store.root.appendingPathComponent("transfer-state", isDirectory: true))
        drawings = try DrawingStore(root: store.root.appendingPathComponent("drawings", isDirectory: true))
        drawingPublications = try DrawingPublicationStore(root: store.root.appendingPathComponent("drawing-publications", isDirectory: true))
        notebooks = try NotebookStore(root: store.root.appendingPathComponent("notebooks", isDirectory: true))
        #if DEBUG
        try NewspaperFixture.seedIfAsked(notebooks)
        #endif
        replica = try ReplicaStore(url: store.root.appendingPathComponent("replica.sqlite"))
        reader = ReplicaReader(url: store.root.appendingPathComponent("replica.sqlite"))
        media = try MediaStore(root: store.root.appendingPathComponent("downloaded-media", isDirectory: true))
        daily = try DailyStore(root: store.root.appendingPathComponent("daily", isDirectory: true))
        dailySyncer = DailySync(store: daily)
        healthState = try HealthStateStore(root: store.root.appendingPathComponent("apple-health", isDirectory: true))
        healthSyncer = HealthSync(store: healthState)
        workouts = try WorkoutStore(root: store.root.appendingPathComponent("workouts", isDirectory: true))
        workoutSyncer = WorkoutSync(store: workouts)
        pomodoros = try PomodoroStore(root: store.root.appendingPathComponent("pomodoro-outbox", isDirectory: true))
        pomodoroSyncer = PomodoroSync(store: pomodoros)
        chatRecordings = try ChatRecordingStore(root: store.root.appendingPathComponent("chat-recordings", isDirectory: true))
        chatRecordingSyncer = ChatRecordingSync(store: chatRecordings)
        todoOutbox = try TodoOutbox(root: store.root.appendingPathComponent("todo-outbox", isDirectory: true))
        todoSyncer = TodoSync(outbox: todoOutbox)
        jobStore = try JobStore(root: store.root.appendingPathComponent("jobs", isDirectory: true))
        jobSyncer = JobSync(store: jobStore)
        calendarOutbox = try CalendarOutbox(root: store.root.appendingPathComponent("calendar-outbox", isDirectory: true))
        calendarSyncer = CalendarSyncer(outbox: calendarOutbox)
        ficActivity = try FicActivityStore(root: store.root.appendingPathComponent("fic-activity", isDirectory: true))
        ficActivitySyncer = FicActivitySync(store: ficActivity)
        try chatRecordings.recoverInterrupted()
        recorder = Recorder(store: store)
        watchReceiver = try WatchReceiver(store: store, pomodoros: pomodoros)
        syncer = CaptureSync(store: store, uploads: uploads, transfers: transfers)
        replicaSyncer = ReplicaSync(url: store.root.appendingPathComponent("replica.sqlite"))
        libraryWorker = LibraryDownload(replicaURL: store.root.appendingPathComponent("replica.sqlite"),
                                        mediaURL: media.root)
        try store.recoverInterruptedRecordings()
        server = try store.server
        if let server { token = try SessionToken.read(server: server) }
        signedIn = token != nil
        captures = try store.list()
        draft = try store.draft()
        dailyLogs = try daily.list()
        healthStatus = healthState.status()
        workoutLogs = try workouts.list()
        chatRecordingQueue = try chatRecordings.list()
        todoQueue = try todoOutbox.list()
        jobQueue = try jobStore.pending()
        recentExercises = (try? JSONDecoder().decode([RecentExercise].self, from: Data(contentsOf: workoutCache("recent")))) ?? []
        recentWorkouts = (try? JSONDecoder().decode([WorkoutSession].self, from: Data(contentsOf: workoutCache("sessions")))) ?? []
        weather = try? JSONDecoder().decode(WeatherDay.self, from: Data(contentsOf: weatherCache))
        pendingEdits = try replica.edits().filter { $0.operation.collection == "journal_entries" }
        Task { await reloadJournal(); await reloadCalendar() }
        refreshLibraryBytes()
        recorder.onChange = { [weak self] in
            self?.reloadCaptures()
            self?.requestSync()
        }
        recorder.onError = { [weak self] in self?.message = $0.localizedDescription }
        watchReceiver.onChange = { [weak self] in self?.reloadCaptures(); self?.reloadQueues(); self?.requestSync() }
        watchReceiver.onError = { [weak self] in self?.message = $0.localizedDescription }
        watchReceiver.activate()
        // A screenshot from the Shortcuts action lands here even with no editor open.
        NotebookSession.shared.store = notebooks
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

    /// Off until turned on in Settings: reading Health is a permission sheet
    /// the user should see because they asked for it, not on first launch.
    var healthSyncEnabled: Bool {
        UserDefaults.standard.bool(forKey: "healthSyncEnabled") && healthSource != nil
    }

    var healthAvailable: Bool { healthSource != nil }

    /// Asks for read access, then syncs. Returns false only when the question
    /// itself failed; a refusal is invisible to us and simply uploads nothing.
    func enableHealth() async -> Bool {
        guard let healthSource else { message = "Health data isn't available on this device."; return false }
        do {
            try await healthSource.requestAuthorization()
            UserDefaults.standard.set(true, forKey: "healthSyncEnabled")
            requestSync()
            return true
        } catch {
            message = error.localizedDescription
            return false
        }
    }

    func disableHealth() {
        UserDefaults.standard.set(false, forKey: "healthSyncEnabled")
        onBackgroundSyncNeeded?()
    }

    /// Re-reads all of Health from the beginning. Safe: the server upserts
    /// by HealthKit UUID, so nothing is stored twice.
    func resendAllHealth() {
        do { try healthState.reset() } catch { message = error.localizedDescription }
        healthStatus = healthState.status()
        requestSync(manual: true)
    }

    /// A Health problem on this device (HealthKit refusing a query) is shown
    /// in Settings and doesn't fail the rest of the pass; the server being
    /// unreachable or the pass being cancelled still does.
    private func syncHealth(using api: JournalAPI, force: Bool) async throws {
        guard healthSyncEnabled, let healthSource,
              force || HealthSync.isDue(healthState.status(), now: Date()) else { return }
        defer { healthStatus = healthState.status() }
        do {
            try await healthSyncer.run(source: healthSource, transport: api)
        } catch {
            if Task.isCancelled || error is CancellationError || error is URLError || error is HTTPFailure { throw error }
        }
    }

    /// Everything, for the few places that changed more than they can name.
    func reload() {
        reloadCaptures()
        reloadQueues()
        Task { await reloadJournal(); await reloadCalendar() }
    }

    /// This device's captures and the composer's draft. Cheap: unchanged
    /// capture files aren't decoded again.
    func reloadCaptures() {
        do {
            captures = try store.list()
            draft = try store.draft()
        } catch { message = error.localizedDescription }
    }

    /// The small outboxes: what is waiting to go up.
    func reloadQueues() {
        do {
            dailyLogs = try daily.list()
            workoutLogs = try workouts.list()
            chatRecordingQueue = try chatRecordings.list()
            todoQueue = try todoOutbox.list()
            jobQueue = try jobStore.pending()
        } catch { message = error.localizedDescription }
    }

    /// The journal feed's entries, their attachments and the edits waiting on
    /// them, read and decoded off the main thread. A newer load supersedes an
    /// older one still running (typing a search).
    func reloadJournal() async {
        journalLoads += 1
        let load = journalLoads, query = journalQuery, limit = journalLimit
        do {
            let (records, count, attachments, edits) = try await reader.read { store in
                let records = try store.newestRecords(collection: "journal_entries", query: query, limit: limit)
                return (records, try store.count(collection: "journal_entries", query: query),
                        try store.relatedRecords(collection: "journal_attachments", field: "entryId", values: records.map(\.id)),
                        try store.edits().filter { $0.operation.collection == "journal_entries" })
            }
            guard load == journalLoads else { return }
            journalRecords = records
            journalCount = count
            journalAttachments = JournalAttachmentItem.grouped(attachments)
            pendingEdits = edits
            placeJournalOccurrences()
        } catch { message = error.localizedDescription }
    }

    /// The categorised calendar occurrences around the days the feed covers,
    /// for its borders. A search shows matches, not a day, so it has none, as
    /// on the web.
    private func placeJournalOccurrences() {
        let times = journalRecords.compactMap { JournalTimestamp.parse($0.data?["createdAt"]?.string) }
            + captures.map(\.createdAt)
        guard journalQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let range = JournalEventGroups.dayRange(times) else {
            journalOccurrences = []
            return
        }
        journalOccurrences = CalendarExpansion.expand(calendarEvents, exceptions: calendarExceptions,
                                                      start: range.start, end: range.end)
            .filter { !$0.event.categoryTags.isEmpty }
    }

    /// Every series and exception, not a page: a series anchored years ago
    /// still occurs today. Decoded off the main thread.
    func reloadCalendar() async {
        do {
            let (events, exceptions) = try await reader.read { store in
                (try store.records(collection: "calendar_events", limit: 100_000).compactMap(CalendarEvent.init(record:)),
                 try store.records(collection: "calendar_event_exceptions", limit: 100_000).compactMap(CalendarException.init(record:)))
            }
            let pending = try calendarOutbox.list()
            let overlay = CalendarOverlay(events: events, exceptions: exceptions, pending: pending)
            calendarEvents = overlay.events
            calendarExceptions = overlay.exceptions
            pendingCalendarIDs = overlay.pendingIDs
            sleepDays = CalendarSleep.overlay(sleepCache, pending: pending)
            placeJournalOccurrences()
        } catch { message = error.localizedDescription }
    }

    /// The server derives wake and sleep from the day's activity, so they are
    /// fetched rather than replicated, and kept here for the days already seen.
    private var sleepCacheURL: URL { calendarOutbox.root.appendingPathComponent("sleep.json") }
    private var sleepCache: [String: SleepDay] {
        (try? JSONDecoder().decode([String: SleepDay].self, from: Data(contentsOf: sleepCacheURL))) ?? [:]
    }

    /// Asks the server for a day's wake and sleep; offline, the cached answer stands.
    func refreshSleep(_ day: String) async {
        guard let api = chatAPI(), let fetched = try? await api.sleep(day: day) else { return }
        var cache = sleepCache
        cache[day] = fetched
        // A couple of months of days is plenty to page back through offline.
        for stale in cache.keys.sorted().dropLast(60) { cache[stale] = nil }
        try? JSONEncoder().encode(cache).write(to: sleepCacheURL, options: .atomic)
        await reloadCalendar()
    }

    private func refreshLibraryBytes() {
        Task {
            do {
                libraryBytes = try await libraryWorker.usedBytes()
                libraryTextBytes = try await libraryWorker.textBytes()
            }
            catch { message = error.localizedDescription }
        }
    }

    func saveText(_ text: String) -> Bool {
        do {
            try store.save(Capture(text: text.trimmingCharacters(in: .whitespacesAndNewlines)))
            reloadCaptures()
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    /// Typed reading commentary: a journal entry linked to the chapter, as
    /// the desktop reader's Commentary panel saves it.
    func saveCommentary(_ text: String, ficID: String, chapterID: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        do {
            try store.save(Capture(text: trimmed, ficID: ficID, chapterID: chapterID))
            reloadCaptures()
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    /// Spoken commentary: stopping the recording is the save, as with the
    /// desktop reader's microphone; the transcript arrives on the entry later.
    func startCommentaryRecording(ficID: String, chapterID: String) async {
        await recorder.start(mode: .transcribe, ficID: ficID, chapterID: chapterID)
    }

    /// Queue reading activity. Nothing here is worth an alert: losing a
    /// heartbeat costs a minute of reading time, never what was read.
    func queueReading(_ item: FicActivity, sync: Bool = false) {
        do { try ficActivity.enqueue(item) } catch { return }
        if sync { requestSync() }
    }

    #if DEBUG
    /// The journal fixture's new entry, landing as a sync would land it.
    func fixtureArrival() {
        guard let page = try? JournalFixture.arrivalPage() else { return }
        let changed = (try? replica.apply(page, startingBootstrap: false)) ?? []
        syncChanges = SyncChanges(collections: changed, full: false, token: syncChanges.token + 1)
        Task { await reloadJournal() }
    }
    #endif

    /// Only an actual change of search reloads: the feed calls this each time
    /// it appears, and sync keeps the list current otherwise.
    func searchJournal(_ query: String) {
        guard journalQuery != query else { return }
        journalLimit = 200
        journalQuery = query
        Task { await reloadJournal() }
    }

    func loadMoreJournal() {
        journalLimit += 200
        Task { await reloadJournal() }
    }

    /// Save entry: the typed text, its links and everything staged become one
    /// capture. A recording still running is stopped into the draft first.
    func saveEntry(_ text: String, youtubeURLs: [String], kind: CaptureKind = .journal) -> Bool {
        if recorder.activeID != nil { recorder.stop() }
        do {
            try store.commitDraft(text: text, youtubeURLs: youtubeURLs, kind: kind, location: location.recent)
            reloadCaptures(); requestSync(); return true
        } catch { message = error.localizedDescription; reloadCaptures(); return false }
    }

    // MARK: Notebooks

    /// Files a notebook's rendered pages, plus its one video, as a journal
    /// entry. The composer's draft is left alone.
    func saveNotebook(_ notebook: Notebook, text: String, pages: [(data: Data, name: String)]) -> Notebook? {
        do {
            let capture = try store.commitImages(text: text, youtubeURLs: notebook.youtubeURL.map { [$0] } ?? [],
                                                 images: pages, location: location.recent)
            let saved = try notebooks.markSaved(notebook.id, captureID: capture.id)
            reloadCaptures(); requestSync()
            return saved
        } catch { message = error.localizedDescription; reloadCaptures(); return nil }
    }

    /// The issues the server has archived: asked fresh when signed in, and
    /// otherwise read from the replica, which carries them once the library
    /// has been downloaded.
    func newspaperIssues() async -> [NewspaperIssue] {
        if let api = chatAPI(), let issues = try? await api.newspaperIssues() { return issues }
        return ((try? replica.records(collection: "newspaper_issues", limit: 400)) ?? [])
            .compactMap { NewspaperIssue(record: $0.data) }
    }

    /// Opens a notebook over `issue`: the unsaved one already made for it, or a
    /// new one once its PDF has downloaded. Nil (with a message) offline.
    func openNewspaper(_ issue: NewspaperIssue) async -> Notebook? {
        do {
            if let existing = try notebooks.unsavedNewspaper(date: issue.date) { markOpened(issue); return existing }
            guard let api = chatAPI() else {
                message = "Connect to your server to download this newspaper. Once downloaded it stays on this iPad."
                return nil
            }
            let scratch = FileManager.default.temporaryDirectory.appendingPathComponent("newspaper-downloads", isDirectory: true)
            let pdf = try await api.downloadIssuePDF(date: issue.date, into: scratch)
            defer { try? FileManager.default.removeItem(at: pdf) }
            let notebook = try notebooks.createNewspaper(date: issue.date, pdf: pdf, pageCount: issue.pageCount)
            markOpened(issue)
            return notebook
        } catch { message = error.localizedDescription; return nil }
    }

    private func markOpened(_ issue: NewspaperIssue) {
        guard let api = chatAPI() else { return }
        Task { try? await api.markIssueOpened(date: issue.date) }
    }

    // MARK: Workout

    /// Checks and queues one line. Returns the exercise it was read as, which
    /// becomes the selection for the next bare "20, 10", or nil if refused.
    func logWorkout(_ text: String, selected: String?) -> WorkoutEntry? {
        do {
            let (_, entry) = try workouts.log(text, selected: selected)
            reloadQueues(); requestSync(); return entry
        } catch { message = error.localizedDescription; return nil }
    }

    func discard(_ item: WorkoutLog) {
        do { try workouts.remove(item) } catch { message = error.localizedDescription }
        reloadQueues()
    }

    /// "Rate / location" needs the server; it is not queued.
    func updateWorkout(_ id: String, location: String?, intensity: Int?) async -> Bool {
        guard let api = chatAPI() else { message = "Connect to your server to rate a workout."; return false }
        do {
            try await api.updateWorkout(id, location: location, intensity: intensity)
            await refreshWorkouts(using: api)
            return true
        } catch { message = error.localizedDescription; return false }
    }

    // MARK: Chat

    /// The shared client for every screen's calls, which all need the server.
    func chatAPI() -> JournalAPI? { sharedAPI(cellular: allowCellular) }

    /// The long-lived client for this network setting: the cellular preference
    /// for ordinary calls, Wi-Fi only for the library, always for an opened fic.
    /// Rebuilt only when the server or the sign-in changes.
    private func sharedAPI(cellular: Bool) -> JournalAPI? {
        guard signedIn, let server, let token else { return nil }
        if apisFor?.server != server || apisFor?.token != token {
            apis = [:]
            apisFor = (server, token)
            capabilities = nil
        }
        if let api = apis[cellular] { return api }
        guard let api = try? JournalAPI(server: server, token: token, allowCellular: cellular, uploads: uploads) else { return nil }
        apis[cellular] = api
        return api
    }

    // MARK: Journal media

    /// Attachments fetched to be shown in the feed. Caches, so iOS may take the
    /// space back; a library download's copy is the one meant to last.
    private var journalMediaRoot: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("journal-media", isDirectory: true)
    }
    private var journalMediaFetches: [String: Task<URL?, Never>] = [:]

    /// A file on this device for an attachment, or its poster: the library
    /// download's copy if there is one, or else one fetched now and kept. Nil
    /// offline when nothing is saved yet.
    func journalMediaFile(_ item: JournalAttachmentItem, thumbnail: Bool = false) async -> URL? {
        // A WebM clip downloaded with the library is the original, which the
        // phone cannot play; the server's AAC copy is fetched instead.
        let playable = !thumbnail && item.media == .audio && !item.phonePlayable
        if !thumbnail, !playable,
           let saved = try? media.downloaded(collection: "journal_attachments", id: item.id) { return saved }
        let name = item.id + (thumbnail ? ".poster" : playable ? ".m4a" : "")
        let file = journalMediaRoot.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: file.path) { return file }
        if let running = journalMediaFetches[name] { return await running.value }
        guard let api = chatAPI() else { return nil }
        let fetch = Task<URL?, Never> {
            do {
                try await api.downloadJournalAttachment(item.id, thumbnail: thumbnail, playable: playable, to: file)
                return file
            } catch { return nil }
        }
        journalMediaFetches[name] = fetch
        defer { journalMediaFetches[name] = nil }
        return await fetch.value
    }

    /// Something to play an audio or video attachment with. Audio and short
    /// videos come from a file on this device; a video with none, such as the
    /// archived copy of a YouTube video, streams from the server.
    func journalPlayer(_ item: JournalAttachmentItem) async -> AVPlayer? {
        // The saved files have no extension, so the type is said outright.
        func player(_ url: URL, mime: String, options: [String: Any] = [:]) -> AVPlayer {
            var options = options
            if !mime.isEmpty { options[AVURLAssetOverrideMIMETypeKey] = mime }
            return AVPlayer(playerItem: AVPlayerItem(asset: AVURLAsset(url: url, options: options)))
        }
        if item.media == .audio {
            guard let file = await journalMediaFile(item) else { return nil }
            return player(file, mime: item.playerMIME)
        }
        if item.media == .video, let saved = try? media.downloaded(collection: "journal_attachments", id: item.id) {
            return player(saved, mime: item.mime)
        }
        guard signedIn, let server, let token, let api = chatAPI(),
              let url = try? api.journalAttachmentURL(item.id),
              let cookie = HTTPCookie(properties: [.name: "lunaschal_token", .value: token,
                                                   .domain: server.host ?? "", .path: "/"]) else { return nil }
        return player(url, mime: item.playerMIME,
                      options: [AVURLAssetHTTPCookiesKey: [cookie]])
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
    @discardableResult
    private func sendTodoChanges(using api: JournalAPI) async throws -> Bool {
        guard !todoQueue.isEmpty || !((try? todoOutbox.list()) ?? []).isEmpty else { return false }
        do {
            todoRefusals += try await todoSyncer.run(using: api)
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
            if (error as? URLError) != nil { throw error }
        }
        todoQueue = (try? todoOutbox.list()) ?? todoQueue
        return true
    }

    /// Queues a Queue or Dismiss from the jobs feed and starts sending it.
    func decideJob(_ job: FeedJob, _ decision: JobDecision) {
        do {
            try jobStore.decide(job, decision)
            jobQueue = try jobStore.pending()
        } catch { message = error.localizedDescription; return }
        requestSync()
    }

    /// Like a to-do change, a decision the server can't take right now waits
    /// for the next pass without holding up the uploads after it.
    private func sendJobDecisions(using api: JournalAPI) async throws {
        guard !((try? jobStore.pending()) ?? []).isEmpty else { return }
        do {
            jobRefusals += try await jobSyncer.run(using: api)
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
            if (error as? URLError) != nil { throw error }
        }
        jobQueue = (try? jobStore.pending()) ?? jobQueue
    }

    /// Saved on the device first, so it shows at once and survives being offline.
    @discardableResult
    func queueCalendar(_ change: CalendarChange) -> Bool {
        do {
            try calendarOutbox.append(change)
            Task { await reloadCalendar() }
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    @discardableResult
    private func sendCalendarEvents(using api: JournalAPI) async throws -> Bool {
        guard !((try? calendarOutbox.list()) ?? []).isEmpty else { return false }
        do {
            let refused = try await calendarSyncer.run(using: api)
            if !refused.isEmpty { message = refused.joined(separator: "\n") }
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
            if (error as? URLError) != nil { throw error }
        }
        return true
    }

    func discard(_ item: ChatRecording) {
        do { try chatRecordings.remove(item) } catch { message = error.localizedDescription }
        reloadQueues()
    }

    private func workoutCache(_ name: String) -> URL { workouts.root.appendingPathComponent("\(name).cache") }

    /// For the Workout page as it opens.
    func refreshServerWorkouts() async {
        guard let api = chatAPI() else { return }
        await refreshWorkouts(using: api)
    }

    /// For the Daily page as it opens.
    func refreshDailyStatus() async {
        guard let api = chatAPI() else { return }
        await refreshDaily(using: api)
    }

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
        reloadQueues()
    }

    private func logDaily(_ make: (DailyStore) throws -> DailyLog) -> Bool {
        do { _ = try make(daily); reloadQueues(); requestSync(); return true }
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
        reloadCaptures()
    }

    func discard(_ file: CaptureFile) {
        do { try store.discardStaged(file) } catch { message = error.localizedDescription }
        reloadCaptures()
    }

    func discard(_ clip: CaptureClip) {
        if recorder.activeID == clip.attachmentID { recorder.stop() }
        do { try store.discard(clip) } catch { message = error.localizedDescription }
        reloadCaptures()
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
            capabilities = nil
            requestSync(manual: true)
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func signOut() {
        cancelSync()
        pauseLibrary()
        pauseFicDownloads()
        ficQueue.removeAll()
        do {
            if let server { try SessionToken.remove(server: server) }
            token = nil
            signedIn = false
            apis = [:]
            apisFor = nil
            capabilities = nil
            onBackgroundSyncNeeded?()
        } catch { message = error.localizedDescription }
    }

    /// A local change: sync once things settle for a second, so a burst of
    /// taps is one pass. Manual ("Sync now", pull to refresh) is a full pass now.
    func requestSync(manual: Bool = false) {
        onBackgroundSyncNeeded?()
        if manual {
            do { try transfers.retryWaiting(now: Date()) }
            catch { message = error.localizedDescription; return }
            startSync(.full)
            return
        }
        pendingRequest?.cancel()
        pendingRequest = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard !Task.isCancelled else { return }
            self?.startSync(.changes)
        }
    }

    /// Back in the foreground: everything, including what is fetched rather than replicated.
    func syncOnForeground() { startSync(.full) }

    /// The foreground's periodic check: one request asks whether the server
    /// has anything new, and only what it names is pulled. Outboxes with
    /// nothing in them make no request at all.
    func checkIn() { startSync(.changes) }

    private func startSync(_ mode: SyncMode) {
        guard UIApplication.shared.applicationState == .active, signedIn, let server, let token else { return }
        guard !syncing, !backgroundSyncing else {
            // Asked for mid-pass: run it after, so nothing waits for the next check-in.
            queuedMode = queuedMode == .full || mode == .full ? .full : .changes
            return
        }
        syncing = true
        syncingTask = Task { [weak self] in
            guard let self else { return false }
            let ok = await performSync(server: server, token: token, mode: mode)
            if let next = queuedMode, !Task.isCancelled {
                queuedMode = nil
                startSync(next)
            }
            return ok
        }
    }

    func nextBackgroundSync() throws -> Date? {
        try BackgroundSyncPlan.next(captures: store.list(), attempts: transfers.all(),
            hasEdits: replica.edits().contains { $0.state == "pending" }
                || daily.list().contains { $0.state == .pending }
                || workouts.list().contains { $0.state == .pending }
                || !pomodoros.list().isEmpty
                || drawingPublications.all().contains { $0.state == "pending" }
                || !ficActivity.list().isEmpty
                || !todoOutbox.list().isEmpty
                || !jobStore.pending().isEmpty
                || (healthSyncEnabled && HealthSync.isDue(healthState.status(), now: Date())), signedIn: signedIn,
            enabled: backgroundSyncEnabled, now: Date())
    }

    func syncInBackground() async -> Bool {
        guard !syncing, !Task.isCancelled, signedIn, let server, let token,
              backgroundSyncEnabled else { return false }
        syncing = true; backgroundSyncing = true
        defer { backgroundSyncing = false }
        // Its own task, so the background lease's expiry can cancel it through `cancelSync`.
        let task = Task { await performSync(server: server, token: token, mode: .full) }
        syncingTask = task
        return await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
    }

    func leaveForeground() {
        if !backgroundSyncing { cancelSync() }
        pauseLibrary()
        pauseFicDownloads()
        onBackgroundSyncNeeded?()
    }

    func backgroundPreferenceChanged() {
        if backgroundSyncing { cancelSync() }
        onBackgroundSyncNeeded?()
    }

    /// The journal & library scope the UI's sync keeps current.
    private static let journalScope = [
        "journal_entries", "journal_attachments", "fics", "study_sources",
        "papers", "conversations", "knowledge_archives", "fic_folders", "fic_bookmarks",
    ]

    /// One pass: send what is waiting, then pull what changed. A `.changes`
    /// pass asks the server once which scopes have anything new and pulls only
    /// those; a `.full` pass (foreground, manual, background) pulls every
    /// scope and refreshes what is fetched rather than replicated.
    private func performSync(server: URL, token: String, mode: SyncMode) async -> Bool {
        timings = SyncTimings()
        let stalls = StallWatch()
        let full = mode == .full
        var changed: Set<String> = []
        // What this device had waiting before the pass, so only those screens reload.
        let hadCaptures = captures.contains { $0.state == .pending || ($0.kind == .food && $0.weather == nil) }
        let hadDaily = dailyLogs.contains { $0.state == .pending }
        let hadWorkouts = !workoutLogs.isEmpty
        let hadQueues = hadDaily || hadWorkouts || !chatRecordingQueue.isEmpty || !todoQueue.isEmpty || !jobQueue.isEmpty
        defer {
            syncing = false
            timed("refresh screens") {
                if full || hadCaptures { reloadCaptures() }
                if full || hadQueues { reloadQueues() }
                if full || !changed.isDisjoint(with: ["journal_entries", "journal_attachments"]) {
                    Task { await reloadJournal() }
                }
                if full || !changed.isDisjoint(with: CalendarSync.collections) { Task { await reloadCalendar() } }
            }
            if full || !changed.isEmpty {
                syncChanges = SyncChanges(collections: changed, full: full, token: syncChanges.token + 1)
            }
            watchReceiver.sendServerReceipts()
            onBackgroundSyncNeeded?()
            timings.longestStall = stalls.stop()
            lastSyncReport = timings.summary()
        }
        do {
            try Task.checkCancellation()
            guard let api = sharedAPI(cellular: allowCellular) else { return false }
            // Push. Each step makes no request when its outbox is empty.
            // First: a voice message is a question someone is waiting on.
            if !chatRecordingQueue.isEmpty {
                try await timed("chat voice") { try await chatRecordingSyncer.run(using: api) }
                changed.insert(SyncChanges.chat)
            }
            if try await timed("to-dos", { try await sendTodoChanges(using: api) }) { changed.insert(SyncChanges.todos) }
            try await timed("job decisions") { try await sendJobDecisions(using: api) }
            if try await timed("calendar changes", { try await sendCalendarEvents(using: api) }) {
                changed.formUnion(CalendarSync.collections)
            }
            try await timed("uploads") { try await syncer.run(using: api) }
            try await timed("daily") { try await dailySyncer.run(using: api) }
            try await timed("workouts") { try await workoutSyncer.run(using: api) }
            try await timed("pomodoro") { try await pomodoroSyncer.run(using: api) }
            try await timed("reading activity") { try await ficActivitySyncer.run(using: api) }
            // What the server works out rather than replicates: after this
            // device sent something it changes, or on a full pass.
            if full || hadWorkouts { await timed("workout history") { await refreshWorkouts(using: api) } }
            if full || hadDaily { await timed("daily status") { await refreshDaily(using: api) } }
            await timed("weather") { await refreshWeather(using: api) }

            // Pull.
            let caps = try await timed("capabilities") { try await serverCapabilities(api) }
            let calendar = CalendarSync.supported(by: caps.collections)
            let libraryScope = LibraryDownload.collections(knowledge: UserDefaults.standard.bool(forKey: "downloadKnowledge"))
            let scopes = [Self.journalScope, CalendarSync.collections, libraryScope]
            var pull = scopes.map { _ in true }
            if !full, caps.syncStatus {
                pull = try await timed("check for changes") { try await replicaSyncer.scopesToPull(using: api, scopes: scopes) }
            }
            let editsWaiting = !pendingEdits.isEmpty || ((try? replica.edits().contains { $0.state == "pending" }) ?? false)
            if pull[0] || editsWaiting {
                changed.formUnion(try await timed("journal & library") {
                    try await replicaSyncer.run(using: api, collections: Self.journalScope)
                })
            }
            if calendar, pull[1] {
                changed.formUnion(try await timed("calendar") {
                    try await replicaSyncer.run(using: api, collections: CalendarSync.collections, sendEdits: false)
                })
            }
            if calendar, full { await timed("sleep") { await refreshSleep(DayKey.of(Date())) } }
            try await timed("drawings") {
                for publication in try drawingPublications.all() where publication.state == "pending" {
                    try Task.checkCancellation()
                    let reply = try await api.publishDrawing(publication, store: drawingPublications)
                    try drawingPublications.receive(reply, for: publication)
                    changed.insert(SyncChanges.drawings)
                }
            }
            if !downloadingLibrary, pull[2] {
                try await timed("library text") {
                    try await libraryWorker.updateText(using: api, collections: libraryScope)
                }
                changed.formUnion(await libraryWorker.takeTextChanges())
                // The server stopped accepting the library's cursor (its history
                // was compacted while this device was away): only a Wi-Fi
                // download can bring the text up to date again. Remembered
                // across launches, and tried at most every ten minutes, since
                // off Wi-Fi it can only fail.
                if await libraryWorker.needsBootstrap { UserDefaults.standard.set(true, forKey: Self.libraryStaleKey) }
            }
            if UserDefaults.standard.bool(forKey: Self.libraryStaleKey), !downloadingLibrary,
               libraryRetryAt.map({ Date() >= $0 }) ?? true {
                libraryRetryAt = Date().addingTimeInterval(600)
                startLibraryDownload()
            }
            try await timed("health") { try await syncHealth(using: api, force: full) }
            // Once per device, after everything else has synced: never at launch,
            // where building the indexes on a large library outlasted the watchdog.
            try await timed("replica upkeep") { try await replicaSyncer.maintain() }
            // Anything ticked off while this pass was busy uploading.
            if try await timed("to-dos", { try await sendTodoChanges(using: api) }) { changed.insert(SyncChanges.todos) }
            if full || changed.contains("journal_entries") { tidyCaptures() }
            let retry = try transfers.all().compactMap(\.retryAt).min()
            syncMessage = retry.map { "Uploads will retry after \($0.formatted(date: .omitted, time: .shortened))." }
            return true
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { return false }
            // Being offline is normal; don't show an alert every retry.
            syncMessage = error.localizedDescription
            if let failure = error as? HTTPFailure {
                if [401, 403].contains(failure.status) { signedIn = false }
                // Perhaps a different server now: ask again next pass.
                if (400..<500).contains(failure.status) { capabilities = nil }
            }
            return false
        }
    }

    private func serverCapabilities(_ api: JournalAPI) async throws -> SyncCapabilities {
        if let capabilities { return capabilities }
        let fetched = try await api.serverCapabilities()
        capabilities = fetched
        return fetched
    }

    /// Settles synced journal captures against the replica's entries: marks
    /// one deleted elsewhere, drops one the entry now stands in for.
    private func tidyCaptures() {
        do {
            if try store.tidySynced(entry: { [replica] id in
                guard let record = try replica.record(collection: "journal_entries", id: id) else { return .unknown }
                return record.deleted ? .removed : .present
            }) > 0 { reloadCaptures() }
        } catch { /* Tidying only: the next pass tries again. */ }
    }

    /// The last pass's step times, for Settings → Transfers.
    @Published private(set) var lastSyncReport: String?
    private var timings = SyncTimings()

    private func timed<T>(_ step: String, _ work: () async throws -> T) async rethrows -> T {
        let start = Date()
        defer { timings.record(step, seconds: Date().timeIntervalSince(start)) }
        return try await work()
    }

    private func timed<T>(_ step: String, _ work: () throws -> T) rethrows -> T {
        let start = Date()
        defer { timings.record(step, seconds: Date().timeIntervalSince(start)) }
        return try work()
    }

    /// Cancelling the pass's task cancels its requests; the shared session
    /// itself stays usable for the next one.
    func cancelSync() {
        pendingRequest?.cancel()
        queuedMode = nil
        syncingTask?.cancel()
    }

    func startLibraryDownload() {
        guard !downloadingLibrary, let api = sharedAPI(cellular: false) else { return }
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
                downloadingLibrary = false; libraryTask = nil
                refreshLibraryBytes()
                syncChanges = SyncChanges(collections: Set(collections + ["fics"]), full: false, token: syncChanges.token + 1)
            }
            do {
                try Task.checkCancellation()
                let supported = try await libraryWorker.download(using: api, collections: collections,
                    mediaCollections: selected, budget: Int64(gigabytes) * 1024 * 1024 * 1024) { [weak self] status in
                        await self?.showLibraryProgress(status)
                    }
                UserDefaults.standard.set(false, forKey: Self.libraryStaleKey)
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
    }

    // MARK: Fic downloads

    /// Whether `book` can be read without the network: its PDF, or as many
    /// chapters as the server says it has.
    func isFicOnDevice(_ book: SyncChange) -> Bool {
        if book.data?["sourceType"]?.string == "pdf" {
            return (try? media.downloaded(collection: "fics", id: book.id)) != nil
        }
        let local = (try? replica.relatedCount(collection: "fic_chapters", field: "ficId", value: book.id)) ?? 0
        let expected = book.data?["chapterCount"]?.number.map(Int.init) ?? 1
        return local > 0 && local >= expected
    }

    /// Which of these books are wholly on the device, for the library list's
    /// badge: chapters counted for the page in one query, not one per row.
    func ficsOnDevice(_ books: [SyncChange]) -> Set<String> {
        let text = books.filter { $0.data?["sourceType"]?.string != "pdf" }
        return ficsOnDevice(books, chapterCounts: (try? replica.chapterCounts(bookIDs: text.map(\.id))) ?? [:])
    }

    /// The same, with the chapters already counted (off the main thread).
    func ficsOnDevice(_ books: [SyncChange], chapterCounts counts: [String: Int]) -> Set<String> {
        var out = Set<String>()
        for book in books {
            if book.data?["sourceType"]?.string == "pdf" {
                if (try? media.downloaded(collection: "fics", id: book.id)) != nil { out.insert(book.id) }
            } else {
                let local = counts[book.id] ?? 0
                if local > 0, local >= book.data?["chapterCount"]?.number.map(Int.init) ?? 1 { out.insert(book.id) }
            }
        }
        return out
    }

    /// Opening a fic that isn't on the device puts it at the front of the
    /// queue and starts it now, ahead of the library download and of any fic
    /// opened before it. Over any connection: opening a book is asking for it.
    func ensureFicOnDevice(_ book: SyncChange) {
        guard canDownloadFics, !isFicOnDevice(book) else { return }
        ficErrors[book.id] = nil
        let headChanged = ficQueue.prioritize(book.id, title: book.title)
        guard headChanged || ficTask == nil else { return }
        if downloadingLibrary {
            resumeLibraryAfterFics = true
            pauseLibrary()
        }
        restartFicDownloads()
    }

    /// Picks the queue back up after the app returns to the foreground.
    func resumeFicDownloads() {
        guard ficTask == nil, !ficQueue.isEmpty, canDownloadFics else { return }
        restartFicDownloads()
    }

    /// Signed in, or (debug builds) talking to the library fixture's stand-in server.
    var canDownloadFics: Bool {
        #if DEBUG
        if LibraryFixture.isActive { return true }
        #endif
        return signedIn
    }

    /// What a fic downloads from: the server, or the fixture's stand-in.
    /// Made per fic, since `pauseFicDownloads` cancels the one in use.
    private func ficTransport() -> (() throws -> FicDownloadTransport)? {
        #if DEBUG
        if LibraryFixture.isActive { return { LibraryFixture.Transport() } }
        #endif
        guard let api = sharedAPI(cellular: true) else { return nil }
        return { api }
    }

    func pauseFicDownloads() {
        ficTask?.cancel()
        ficTask = nil
        ficDownload = nil
    }

    private func restartFicDownloads() {
        let previous = ficTask, library = libraryTask
        previous?.cancel()
        ficTask = Task {
            // Only one writer at a time: let whatever was running let go first.
            await previous?.value
            await library?.value
            await runFicQueue()
        }
    }

    private func runFicQueue() async {
        while let entry = ficQueue.head {
            guard !Task.isCancelled else { return }
            guard let transport = ficTransport() else { break }
            ficDownload = FicDownloadStatus(id: entry.id, title: entry.title, fraction: 0, doneBytes: 0, totalBytes: 0,
                                            secondsLeft: nil)
            ficEstimate = TransferEstimate(started: Date())
            do {
                try await libraryWorker.downloadFic(entry.id, after: entry.after, using: try transport()) { [weak self] progress in
                    await self?.showFicProgress(entry, progress)
                }
                ficQueue.remove(entry.id)
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { return }
                ficErrors[entry.id] = error.localizedDescription
                ficQueue.remove(entry.id)
            }
            ficDownloadRevision += 1
        }
        ficDownload = nil
        ficTask = nil
        refreshLibraryBytes()
        if resumeLibraryAfterFics, ficQueue.isEmpty {
            resumeLibraryAfterFics = false
            startLibraryDownload()
        }
    }

    private func showFicProgress(_ entry: FicQueue.Entry, _ progress: FicDownloadProgress) {
        guard ficQueue.head?.id == entry.id else { return }
        ficQueue.record(entry.id, after: progress.after)
        ficDownload = FicDownloadStatus(id: entry.id, title: entry.title, fraction: progress.fraction,
            doneBytes: progress.doneBytes, totalBytes: progress.totalBytes,
            secondsLeft: ficEstimate.secondsLeft(done: progress.doneBytes, total: progress.totalBytes, now: Date()))
        ficDownloadRevision += 1
    }

    func removeLibraryMedia() {
        guard !downloadingLibrary else { return }
        do {
            try media.removeDownloadedCopies()
            libraryMessage = "Downloaded media removed. Captures and server originals are retained."
            refreshLibraryBytes()
        } catch { message = error.localizedDescription }
    }

    func removeLibraryMedia(collection: String, id: String) -> Bool {
        guard !downloadingLibrary else { return false }
        do {
            let freed = try media.removeDownloadedCopy(collection: collection, id: id)
            libraryMessage = "Device copy removed. \(ByteCountFormatter.string(fromByteCount: freed, countStyle: .file)) freed. Shared files may remain for other items."
            refreshLibraryBytes()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func edit(_ record: SyncChange, content: String, title: String) -> Bool {
        do {
            _ = try replica.queue(record: record, data: ["content": .string(content), "title": .string(title)])
            Task { await reloadJournal() }; requestSync(); return true
        } catch { message = error.localizedDescription; return false }
    }

    func delete(_ record: SyncChange) {
        do {
            _ = try replica.queue(record: record, data: [:], delete: true)
            Task { await reloadJournal() }; requestSync()
        } catch { message = error.localizedDescription }
    }

    func resolve(_ edit: PendingEdit, keepLocal: Bool) {
        do { try replica.resolve(edit, keepLocal: keepLocal); Task { await reloadJournal() }; requestSync() }
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
            reloadCaptures()
            requestSync()
        } catch { message = error.localizedDescription }
    }
}

/// What the fic download indicator shows.
struct FicDownloadStatus: Equatable {
    let id: String
    let title: String
    let fraction: Double
    let doneBytes: Int64
    let totalBytes: Int64
    let secondsLeft: TimeInterval?

    var detail: String {
        let size = totalBytes > 0
            ? "\(ByteCountFormatter.string(fromByteCount: doneBytes, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: totalBytes, countStyle: .file))"
            : "Starting…"
        return "\(Int((fraction * 100).rounded()))% · \(size) · \(TransferEstimate.describe(secondsLeft))"
    }
}

/// How much a sync pass does.
enum SyncMode { case changes, full }

/// What one sync pass (or library download) touched, so each screen reloads
/// for its own data only and keeps its place. `token` changes every time, so
/// two passes touching the same collections still both notify.
struct SyncChanges: Equatable {
    var collections: Set<String> = []
    /// A foreground, manual or background pass: everything fetched rather
    /// than replicated was refreshed too, so every screen may refresh.
    var full = false
    var token = 0

    func touches(_ names: Set<String>) -> Bool { full || !collections.isDisjoint(with: names) }

    /// Local outboxes, beside the replica's collection names.
    static let todos = "local:todos"
    static let chat = "local:chat"
    static let drawings = "local:drawings"
}
