import SwiftUI
import WatchKit
import WatchConnectivity
import AVFoundation
import UserNotifications
import WidgetKit
import LunaschalCore

@main
struct LunaschalWatchApp: App {
    @WKApplicationDelegateAdaptor(WatchLifecycle.self) private var lifecycle
    var body: some Scene {
        WindowGroup {
            switch WatchModel.startup {
            case .success(let model): WatchCaptureView(model: model, recorder: model.recorder)
            case .failure(let error): Text("Could not open recordings: \(error.localizedDescription)")
            }
        }
    }
}

@MainActor
final class WatchModel: NSObject, ObservableObject, WCSessionDelegate {
    static let startup: Result<WatchModel, Error> = Result {
        let root = try FileManager.default.url(for: .applicationSupportDirectory,
            in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("Captures", isDirectory: true)
        return try WatchModel(store: CaptureStore(root: root))
    }
    let store: CaptureStore
    nonisolated private let receiptRoot: URL
    nonisolated private let pomodoroOutbox: URL
    let recorder: Recorder
    let pomodoro: PomodoroModel
    let workout: WorkoutModel
    /// The buzz at the end of Focus, Timeout and Workout.
    let alarm = WatchAlarm()
    @Published var captures: [Capture] = []
    @Published var received: Set<String> = []
    @Published var uploaded: Set<String> = []
    @Published var message: String?
    /// What the recorder is making, so the face, the Controls and the
    /// Record/Transcribe pages can show it.
    @Published private(set) var recordingMode: CaptureMode?
    /// When the clip going now started, for its page's elapsed time.
    @Published private(set) var recordingSince: Date?

    private init(store: CaptureStore) throws {
        self.store = store
        receiptRoot = store.root
        recorder = Recorder(store: store)
        pomodoro = PomodoroModel(engine: try PomodoroEngine(fallback: store.root.deletingLastPathComponent()
            .appendingPathComponent("Pomodoro", isDirectory: true)))
        pomodoroOutbox = pomodoro.outbox.root
        workout = WorkoutModel(fallback: store.root.deletingLastPathComponent())
        super.init()
        try store.recoverInterruptedRecordings()
        recorder.onChange = { [weak self] in self?.reload(); self?.sendPending(); self?.publishRecording() }
        recorder.onError = { [weak self] in self?.message = $0.localizedDescription }
        pomodoro.onChange = { [weak self] in self?.syncAlarm() }
        workout.onChange = { [weak self] in self?.syncAlarm() }
        alarm.onRing = { [weak self] in self?.pomodoro.refresh(); self?.workout.refresh() }
        alarm.onDismiss = { [weak self] in
            // Dismissing Workout's buzz is its Stop; a finished pomodoro still
            // waits on the page for Continue, Break or Cancel.
            if self?.workout.timer.isRinging == true { self?.workout.stop() }
        }
        alarm.onError = { [weak self] text in
            guard let self else { return }
            if let rest = self.workout.timer.endsAt, rest <= self.pomodoro.timer.run?.endsAt ?? .distantFuture {
                self.workout.message = text
            } else {
                self.pomodoro.message = text
            }
        }
        syncAlarm()
        reload()
        publishRecording() // clears a status left behind by a crash mid-clip
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    /// Hands the alarm both timers' next end and whichever of them is up.
    func syncAlarm() {
        let ends = [pomodoro.timer.run?.endsAt, workout.timer.endsAt].compactMap { $0 }
        var ringing: Set<String> = []
        if workout.timer.isRinging { ringing.insert(WatchAlarm.rest) }
        if case .finished = pomodoro.timer.state { ringing.insert(WatchAlarm.pomodoro) }
        alarm.update(WatchAlarm.Plan(nextEnd: ends.min(), ringing: ringing))
    }

    /// Starts a clip in `mode`, or stops and saves the one going. One press
    /// does both, from the list, the face or a Control.
    func toggleRecording(_ mode: CaptureMode) async {
        if recorder.activeID != nil { recorder.stop(); return }
        guard !recorder.isStarting else { return }
        recordingMode = mode
        await recorder.start(mode: mode)
    }

    /// A Record or Transcribe Control: `on` is the toggle's new value, so a
    /// press that arrives after the clip already stopped does nothing.
    static func toggleRecording(_ mode: CaptureMode, on: Bool) async throws {
        guard case .success(let model) = startup else { throw RecordingIntentError.needsApp }
        if on == (model.recorder.activeID != nil) { return }
        await model.toggleRecording(mode)
        if on, model.recorder.activeID == nil {
            throw model.message.map { RecorderError.message($0) } ?? RecordingIntentError.needsApp
        }
    }

    /// Tells the complications and Controls whether a clip is going.
    private func publishRecording() {
        let status = recorder.activeID.map { _ in RecordingStatus(mode: recordingMode ?? .record, startedAt: Date()) }
        if let url = WatchComplication.recordingURL {
            // Keep the start time of the clip already going rather than restamping it.
            let current = RecordingStatus.load(from: url)
            if (status == nil) != (current == nil) || status?.mode != current?.mode {
                try? RecordingStatus.save(status, to: url)
            }
        }
        if recorder.activeID == nil { recordingMode = nil; recordingSince = nil }
        else if recordingSince == nil { recordingSince = Date() }
        PomodoroEngine.redraw()
    }

    func reload() {
        do {
            captures = try store.list()
            received = Set(captures.filter {
                FileManager.default.fileExists(atPath: store.root.appendingPathComponent("\($0.id).phone-receipt").path)
            }.map(\.id))
            uploaded = Set(try captures.filter { try WatchReceipts(store: store).serverReceived($0) }.map(\.id))
        } catch { message = error.localizedDescription }
    }

    func sendPending() {
        let session = WCSession.default
        guard session.activationState == .activated else { return }
        let pending = Set(session.outstandingFileTransfers.compactMap { $0.file.metadata?["captureID"] as? String })
        let receiptRequests = Set(session.outstandingUserInfoTransfers.compactMap { $0.userInfo["serverReceiptRequestID"] as? String })
        for capture in captures where received.contains(capture.id) && !uploaded.contains(capture.id) && !receiptRequests.contains(capture.id) {
            do {
                let receipt = try WatchServerReceipt(capture: capture)
                session.transferUserInfo(["serverReceiptRequestID": capture.id,
                                          "serverReceiptRequest": try JSONEncoder().encode(receipt)])
            } catch { message = error.localizedDescription }
        }
        for capture in captures where capture.state == .pending && !received.contains(capture.id) && !uploaded.contains(capture.id) && !pending.contains(capture.id) {
            do {
                let url = try store.audioURL(capture)
                let bytes = Int64(try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
                let envelope = try WatchEnvelope(capture: capture, bytes: bytes, sha256: MediaStore.sha256(url))
                session.transferFile(url, metadata: ["captureID": capture.id, "envelope": try JSONEncoder().encode(envelope)])
            } catch { message = error.localizedDescription }
        }
    }

    func recover(_ capture: Capture) {
        do {
            let player = try AVAudioPlayer(contentsOf: store.audioURL(capture))
            guard player.duration > 0 else { throw CaptureError.missingAudio }
            try store.finishRecording(capture.id)
            reload(); sendPending()
        } catch { message = error.localizedDescription }
    }

    func removeWatchCopy(_ capture: Capture) {
        guard recorder.activeID == nil, !recorder.isStarting else { return }
        do {
            try WatchReceipts(store: store).removeWatchCopy(capture.id)
            reload()
        } catch { message = error.localizedDescription }
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        Task { @MainActor in
            if let error { self.message = error.localizedDescription }
            if state == .activated { self.sendPending(); self.pomodoro.sendPending() }
        }
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        if let id = userInfo["pomodoroStored"] as? String {
            // The phone has it on disk; the Watch's copy can go.
            do { try PomodoroStore(root: pomodoroOutbox).remove(id) }
            catch { Task { @MainActor in self.message = error.localizedDescription } }
            return
        }
        if let bytes = userInfo["serverStored"] as? Data {
            do {
                let receipt = try JSONDecoder().decode(WatchServerReceipt.self, from: bytes)
                try WatchReceipts(store: CaptureStore(root: receiptRoot)).acceptServerReceipt(receipt)
                // Persist before acknowledging, including during a background wake.
                session.transferUserInfo(["serverReceiptStored": bytes])
                Task { @MainActor in self.reload() }
            } catch { Task { @MainActor in self.message = error.localizedDescription } }
            return
        }
        guard let id = userInfo["phoneStored"] as? String, ULID.isValid(id) else { return }
        do {
            // Persist before returning from the connectivity callback. A UI
            // task may not execute before the background refresh is completed.
            guard FileManager.default.fileExists(atPath: receiptRoot.appendingPathComponent(id).appendingPathExtension("json").path) else { return }
            try Data("Stored on phone".utf8).write(to: receiptRoot.appendingPathComponent("\(id).phone-receipt"), options: .atomic)
            Task { @MainActor in self.reload() }
        } catch { Task { @MainActor in self.message = error.localizedDescription } }
    }

    nonisolated func session(_ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: Error?) {
        if let error { Task { @MainActor in self.message = error.localizedDescription } }
        // Transport completion is not durable phone acknowledgement. Keep audio.
    }
}

/// The Watch's screens, side by side: swipe left and right between them, and
/// each complication opens its own.
enum WatchPage: Hashable {
    case focus, timeout, workout, record, transcribe, log

    init(_ choice: TimerChoice) { self = choice == .focus ? .focus : .timeout }
    init(_ choice: RecordingChoice) { self = choice == .record ? .record : .transcribe }
}

private struct WatchCaptureView: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var recorder: Recorder
    @Environment(\.scenePhase) private var phase
    @State private var removing: Capture?
    @State private var page: WatchPage = .focus

    var body: some View {
        TabView(selection: $page) {
            TimerPage(choice: .focus, model: model.pomodoro) { page = WatchPage($0) }
                .tag(WatchPage.focus)
            TimerPage(choice: .timeout, model: model.pomodoro) { page = WatchPage($0) }
                .tag(WatchPage.timeout)
            WorkoutPage(model: model.workout)
                .tag(WatchPage.workout)
            recordingPage(.record).tag(WatchPage.record)
            recordingPage(.transcribe).tag(WatchPage.transcribe)
            log.tag(WatchPage.log)
        }
        .tabViewStyle(.page)
        .onAppear {
            // Open on whatever is already going.
            if model.workout.timer.state != .idle { page = .workout }
            if let owner = model.pomodoro.timer.owner { page = WatchPage(owner) }
            if let kind = PomodoroModel.launchStart, model.pomodoro.timer.run == nil {
                model.pomodoro.start(kind); page = WatchPage(kind == .timeout ? TimerChoice.timeout : .focus)
            }
            #if DEBUG
            // `-WatchLink lunaschal-watch://record,lunaschal-watch://record`:
            // complication taps, three seconds apart, since the watchOS
            // simulator cannot open URLs.
            if let text = UserDefaults.standard.string(forKey: "WatchLink") {
                let links = text.split(separator: ",").compactMap { URL(string: String($0)).flatMap(WatchLink.init(url:)) }
                Task {
                    for (index, link) in links.enumerated() {
                        if index > 0 { try? await Task.sleep(nanoseconds: 3_000_000_000) }
                        open(link)
                    }
                }
            }
            #endif
            if let link = PomodoroEngine.takePendingLink() { open(link) }
        }
        .onChange(of: model.workout.timer.isRinging) { _, ringing in
            if ringing { page = .workout }
        }
        .onChange(of: model.pomodoro.timer.state) { _, state in
            // The alarm brings the app forward; show the timer that is up.
            if case .finished = state, let owner = model.pomodoro.timer.owner { page = WatchPage(owner) }
        }
        .onOpenURL { url in
            if let link = WatchLink(url: url) { open(link) }
        }
        .onChange(of: phase) { _, value in
            if value == .active {
                model.reload(); model.sendPending(); model.pomodoro.refresh(); model.pomodoro.sendPending()
                model.workout.refresh()
                model.syncAlarm() // schedules for a run a Control started while asleep
                if let link = PomodoroEngine.takePendingLink() { open(link) }
            } else if value == .inactive {
                // The face is coming back: draw it from the latest state, in
                // case a reload made mid-change was dropped.
                PomodoroEngine.redraw()
            }
        }
    }

    /// A tap on a complication, or a screen a Control asked for: turns to
    /// that page and, since the face cannot act without opening the app, does
    /// the thing at once. Focus, Timeout and Workout start unless already
    /// going; Record and Transcribe start or stop a clip.
    private func open(_ link: WatchLink) {
        switch link {
        case .timer:
            page = model.pomodoro.timer.owner.map(WatchPage.init) ?? .focus
        case .start(let kind):
            model.pomodoro.press(kind)
            page = model.pomodoro.timer.owner.map(WatchPage.init) ?? .focus
        case .record, .transcribe:
            let choice: RecordingChoice = link == .record ? .record : .transcribe
            page = WatchPage(choice)
            Task { await model.toggleRecording(choice.mode) }
        case .workout:
            page = .workout
            if model.workout.isIdle { model.workout.start() }
        }
    }

    /// Record or Transcribe: one big button, or a big red Stop while that
    /// kind of clip is going.
    @ViewBuilder private func recordingPage(_ choice: RecordingChoice) -> some View {
        Group {
            if recorder.activeID != nil, model.recordingMode == choice.mode {
                Button { recorder.stop() } label: {
                    VStack(spacing: 8) {
                        Image(systemName: "stop.fill").font(.system(size: 44, weight: .semibold))
                        if let since = model.recordingSince {
                            Text(since, style: .timer).font(.title3.weight(.semibold)).monospacedDigit()
                        }
                        Text("Stop and save").font(.footnote)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.roundedRectangle(radius: 24))
                .tint(.red)
            } else {
                BigButton(title: choice.title, symbol: choice.symbol, tint: choice == .record ? .pink : .blue) {
                    Task { await model.toggleRecording(choice.mode) }
                }
                // The other kind of clip is going; stop it from its own page.
                .disabled(recorder.isStarting || recorder.activeID != nil)
            }
        }
        .accessibilityIdentifier("page.\(choice.rawValue)")
    }

    private var log: some View {
        List {
            if let message = model.message { Text(message).font(.footnote) }
            Button("Retry phone transfer") { model.sendPending() }
            ForEach(model.captures) { capture in
                VStack(alignment: .leading) {
                    Text(capture.createdAt, style: .time)
                    Text(model.uploaded.contains(capture.id) ? "Uploaded to server" :
                         model.received.contains(capture.id) ? "Saved on phone" :
                         capture.state == .interrupted ? "Interrupted · audio retained" :
                         capture.state == .recording ? "Recording" : "Saved here · waiting for phone")
                        .font(.caption)
                    if capture.state == .interrupted {
                        Button("Recover playable audio") { model.recover(capture) }
                    }
                    if model.uploaded.contains(capture.id) {
                        Button("Remove Watch copy", role: .destructive) { removing = capture }
                            .disabled(recorder.activeID != nil || recorder.isStarting)
                    }
                }
            }
            Text("Saved on phone does not mean uploaded. Watch copies stay until you remove them after server receipt; transcription may still be processing.")
                .font(.footnote)
        }
        .confirmationDialog("Remove this Watch recording?", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("Remove Watch copy", role: .destructive) {
                if let capture = removing { model.removeWatchCopy(capture) }
                removing = nil
            }
        } message: { Text("The phone and server copies are kept.") }
        .accessibilityIdentifier("page.log")
    }
}

@MainActor
final class WatchLifecycle: NSObject, WKApplicationDelegate, UNUserNotificationCenterDelegate {
    private var pending: Set<WKRefreshBackgroundTask> = []
    private var observer: NSKeyValueObservation?
    private var activationObserver: NSKeyValueObservation?

    func applicationDidFinishLaunching() {
        _ = WatchModel.startup
        PomodoroModel.registerCategories()
        UNUserNotificationCenter.current().delegate = self
        observer = WCSession.default.observe(\.hasContentPending, options: [.initial, .new]) { [weak self] _, _ in
            Task { @MainActor in self?.completeIfIdle() }
        }
        activationObserver = WCSession.default.observe(\.activationState, options: [.new]) { [weak self] _, _ in
            Task { @MainActor in self?.completeIfIdle() }
        }
    }
    func handle(_ backgroundTasks: Set<WKRefreshBackgroundTask>) {
        for task in backgroundTasks {
            if task is WKWatchConnectivityRefreshBackgroundTask { pending.insert(task) }
            else { task.setTaskCompletedWithSnapshot(false) }
        }
        completeIfIdle()
    }
    /// The timers' alarm, when the app was not running at an end.
    func handle(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        guard case .success(let model) = WatchModel.startup else { extendedRuntimeSession.invalidate(); return }
        model.alarm.adopt(extendedRuntimeSession)
    }

    /// A button on the timer's notification: Continue, Break or Cancel.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let action = PomodoroModel.Action(rawValue: response.actionIdentifier)
        await MainActor.run {
            guard case .success(let model) = WatchModel.startup else { return }
            if let action { model.pomodoro.perform(action) } else { model.pomodoro.refresh() }
            model.workout.refresh()
        }
    }

    /// The app is open when the time is up: the screen already shows the
    /// choices, so a haptic is enough and no banner covers them.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        await MainActor.run {
            if case .success(let model) = WatchModel.startup { model.pomodoro.refresh(); model.workout.refresh() }
        }
        return []
    }

    private func completeIfIdle() {
        guard WCSession.default.activationState == .activated, !WCSession.default.hasContentPending else { return }
        pending.forEach { $0.setTaskCompletedWithSnapshot(false) }
        pending.removeAll()
    }
}
