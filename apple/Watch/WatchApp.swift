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
    /// Which button's clip the recorder is making, so the face, the Controls
    /// and that button's tile can show it.
    @Published private(set) var recordingChoice: RecordingChoice?
    /// When the clip going now started, for its tile's elapsed time.
    @Published private(set) var recordingSince: Date?
    /// Whether the phone can be reached right now, for the status screen.
    /// Transfers queue regardless; this only says whether they can move.
    @Published private(set) var phoneReachable = false
    /// The warning over the pages after a stray clip was thrown away.
    @Published private(set) var notice: String?
    private var noticeExpiry: Task<Void, Never>?

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
        recorder.minimumLength = ShortRecording.minimum
        recorder.onDiscarded = { [weak self] in self?.warnDiscarded() }
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

    /// Starts a clip for `choice`, or stops and saves the one going, unless
    /// it is too short to have been meant. One press does both, from a tile,
    /// the face or a Control.
    func toggleRecording(_ choice: RecordingChoice) async {
        if recorder.activeID != nil { recorder.stop(); return }
        guard !recorder.isStarting else { return }
        PomodoroModel.requestAuthorization() // for the too-short warning
        recordingChoice = choice
        await recorder.start(mode: choice.mode, asTodo: choice.asTodo)
    }

    /// A Record, Transcribe or To-do Control: `on` is the toggle's new value,
    /// so a press that arrives after the clip already stopped does nothing.
    static func toggleRecording(_ choice: RecordingChoice, on: Bool) async throws {
        guard case .success(let model) = startup else { throw RecordingIntentError.needsApp }
        if on == (model.recorder.activeID != nil) { return }
        await model.toggleRecording(choice)
        if on, model.recorder.activeID == nil {
            throw model.message.map { RecorderError.message($0) } ?? RecordingIntentError.needsApp
        }
    }

    /// A haptic and a banner, and a notification when a Control stopped the
    /// clip with the app out of sight. The status screen keeps the line.
    private func warnDiscarded() {
        let text = ShortRecording.warning(recordingChoice?.mode ?? .record)
        message = text
        notice = text
        WKInterfaceDevice.current().play(.failure)
        noticeExpiry?.cancel()
        noticeExpiry = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            if !Task.isCancelled { self?.notice = nil }
        }
        guard WKApplication.shared().applicationState != .active else { return }
        let content = UNMutableNotificationContent()
        content.title = text
        content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "recording.discarded", content: content, trigger: nil))
    }

    /// Tells the complications and Controls whether a clip is going.
    private func publishRecording() {
        let choice = recordingChoice ?? .record
        let status = recorder.activeID.map { _ in
            RecordingStatus(mode: choice.mode, asTodo: choice.asTodo, startedAt: Date())
        }
        if let url = WatchComplication.recordingURL {
            // Keep the start time of the clip already going rather than restamping it.
            let current = RecordingStatus.load(from: url)
            if (status == nil) != (current == nil) || (status != nil && !choice.matches(current)) {
                try? RecordingStatus.save(status, to: url)
            }
        }
        if recorder.activeID == nil { recordingChoice = nil; recordingSince = nil }
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
        let reachable = session.isReachable
        Task { @MainActor in
            if let error { self.message = error.localizedDescription }
            self.phoneReachable = reachable
            if state == .activated { self.sendPending(); self.pomodoro.sendPending() }
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.phoneReachable = reachable }
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

/// The Watch's two screens, side by side: every action on one, and what the
/// Watch is set to and holding on the other.
enum WatchPage: Hashable {
    case grid, status
}

/// What a busy timer tile opens: its own controls, which do not fit in a tile.
enum WatchSheet: String, Identifiable {
    case focus, timeout, workout

    var id: String { rawValue }
    init(_ choice: TimerChoice) { self = choice == .focus ? .focus : .timeout }
}

private struct WatchCaptureView: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var recorder: Recorder
    @Environment(\.scenePhase) private var phase
    @State private var page: WatchPage = .grid
    @State private var sheet: WatchSheet?

    var body: some View {
        TabView(selection: $page) {
            WatchGrid(model: model, pomodoro: model.pomodoro, workout: model.workout, recorder: recorder) {
                sheet = $0
            }
            .tag(WatchPage.grid)
            WatchStatus(model: model, recorder: recorder)
                .tag(WatchPage.status)
        }
        .tabViewStyle(.page)
        .sheet(item: $sheet) { which in
            Group {
                switch which {
                case .focus, .timeout:
                    TimerPage(choice: which == .focus ? .focus : .timeout, model: model.pomodoro) {
                        sheet = WatchSheet($0)
                    }
                case .workout:
                    WorkoutPage(model: model.workout)
                }
            }
            .accessibilityIdentifier("sheet.\(which.rawValue)")
        }
        .overlay(alignment: .top) {
            if let notice = model.notice {
                Text(notice)
                    .font(.footnote.weight(.semibold))
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.black)
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .background(.orange, in: RoundedRectangle(cornerRadius: 12))
                    .allowsHitTesting(false)
                    .accessibilityIdentifier("recording.discarded")
            }
        }
        .onAppear {
            // Open on whatever is waiting for a choice.
            if model.workout.timer.isRinging { sheet = .workout }
            if case .finished = model.pomodoro.timer.state, let owner = model.pomodoro.timer.owner {
                sheet = WatchSheet(owner)
            }
            if let kind = PomodoroModel.launchStart, model.pomodoro.timer.run == nil {
                model.pomodoro.start(kind)
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
            if ringing { page = .grid; sheet = .workout }
        }
        .onChange(of: model.workout.timer.state) { _, state in
            // Stopped from its sheet: nothing left to show there.
            if state == .idle, sheet == .workout { sheet = nil }
        }
        .onChange(of: model.pomodoro.timer.state) { _, state in
            switch state {
            case .finished:
                // The alarm brings the app forward; show the timer's choices.
                if let owner = model.pomodoro.timer.owner { page = .grid; sheet = WatchSheet(owner) }
            case .idle:
                if sheet == .focus || sheet == .timeout { sheet = nil }
            case .running:
                break
            }
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

    /// A tap on a complication, or a screen a Control asked for: shows the
    /// grid and, since the face cannot act without opening the app, does the
    /// thing at once. Focus, Timeout and Workout start unless already going
    /// (and then open their controls); Record, Transcribe and To-do start or
    /// stop a clip, whose tile shows it.
    private func open(_ link: WatchLink) {
        page = .grid
        switch link {
        case .timer:
            sheet = model.pomodoro.timer.owner.map(WatchSheet.init)
        case .start(let kind):
            if model.pomodoro.press(kind) == .alreadyGoing {
                sheet = model.pomodoro.timer.owner.map(WatchSheet.init)
            }
        case .record, .transcribe, .todo:
            let choice: RecordingChoice = link == .record ? .record : link == .todo ? .todo : .transcribe
            sheet = nil
            Task { await model.toggleRecording(choice) }
        case .workout:
            if model.workout.isIdle { model.workout.start() } else { sheet = .workout }
        }
    }
}

/// Every action on one screen: the three timers over the three kinds of clip.
/// A tile shows its own state, so the grid is also the glance at what is going.
private struct WatchGrid: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var pomodoro: PomodoroModel
    @ObservedObject var workout: WorkoutModel
    @ObservedObject var recorder: Recorder
    let show: (WatchSheet) -> Void

    private let columns = [GridItem(.flexible(), spacing: 6), GridItem(.flexible(), spacing: 6)]

    var body: some View {
        GeometryReader { box in
            let height = max(44, (box.size.height - 12) / 3)
            LazyVGrid(columns: columns, spacing: 6) {
                timer(.focus).frame(height: height)
                timer(.timeout).frame(height: height)
                workoutTile.frame(height: height)
                recording(.record).frame(height: height)
                recording(.transcribe).frame(height: height)
                recording(.todo).frame(height: height)
            }
        }
        .accessibilityIdentifier("page.grid")
    }

    @ViewBuilder private func timer(_ choice: TimerChoice) -> some View {
        let tint: Color = choice == .focus ? .indigo : .teal
        switch pomodoro.timer.state {
        case .running(let run) where choice.owns(run):
            Tile(symbol: run.kind.symbol, tint: tint, active: true, id: choice.rawValue) {
                show(WatchSheet(choice))
            } caption: {
                Text(timerInterval: run.startedAt...run.endsAt, countsDown: true)
            }
        case .finished(let kind) where choice.owns(kind):
            Tile(symbol: "bell.fill", tint: tint, active: true, id: choice.rawValue) {
                show(WatchSheet(choice))
            } caption: { Text("Done") }
        default:
            Tile(symbol: choice.kind.symbol, tint: tint, id: choice.rawValue) {
                if pomodoro.press(choice.kind) == .alreadyGoing, let other = pomodoro.timer.owner, other != choice {
                    show(WatchSheet(other))
                }
            } caption: { Text("\(choice.title) \(choice.kind.minutes)") }
        }
    }

    @ViewBuilder private var workoutTile: some View {
        switch workout.timer.state {
        case .idle:
            Tile(symbol: "dumbbell", tint: .orange, id: "workout") { workout.start() } caption: {
                Text("Workout \(Int(RestTimer.seconds))s")
            }
        case .running(let startedAt, let endsAt):
            Tile(symbol: "dumbbell", tint: .orange, active: true, id: "workout") { show(.workout) } caption: {
                Text(timerInterval: startedAt...endsAt, countsDown: true)
            }
        case .ringing:
            Tile(symbol: "bell.fill", tint: .orange, active: true, id: "workout") { show(.workout) } caption: {
                Text("Rest's over")
            }
        }
    }

    /// One tap starts the clip and the next stops and saves it. While one
    /// kind of clip is going, the other two wait.
    @ViewBuilder private func recording(_ choice: RecordingChoice) -> some View {
        let going = recorder.activeID != nil && model.recordingChoice == choice
        Tile(symbol: going ? "stop.fill" : choice.symbol, tint: going ? .red : choice.tint,
             active: going, id: choice.rawValue) {
            Task { await model.toggleRecording(choice) }
        } caption: {
            if going, let since = model.recordingSince { Text(since, style: .timer) } else { Text(choice.title) }
        }
        .disabled(!going && (recorder.isStarting || recorder.activeID != nil))
    }
}

extension RecordingChoice {
    /// Its tile's colour while idle; every clip going is red.
    var tint: Color {
        switch self {
        case .record: return .pink
        case .transcribe: return .blue
        case .todo: return .green
        }
    }
}

/// One square of the grid: an icon over a one-line caption, filled solid
/// while its thing is going so the state reads in a glance.
private struct Tile<Caption: View>: View {
    let symbol: String
    let tint: Color
    var active = false
    let id: String
    let action: () -> Void
    @ViewBuilder let caption: () -> Caption
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Image(systemName: symbol).font(.system(size: 22, weight: .semibold))
                caption()
                    .font(.footnote.weight(.semibold))
                    .monospacedDigit()
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
            }
            .foregroundStyle(active ? Color.white : tint)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(tint.opacity(active ? 1 : 0.22), in: RoundedRectangle(cornerRadius: 14))
            .opacity(enabled ? 1 : 0.4)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("tile.\(id)")
    }
}

/// What the Watch is set to and what it is holding: the phone link, the clips
/// still on their way, the timers' lengths, and the recordings themselves.
private struct WatchStatus: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var recorder: Recorder
    @State private var removing: Capture?

    private var waiting: Int { model.captures.filter { !model.uploaded.contains($0.id) }.count }

    var body: some View {
        List {
            Section("Phone") {
                LabeledContent("Link", value: model.phoneReachable ? "Reachable" : "Not reachable")
                LabeledContent("Waiting", value: waiting == 0 ? "None" : "\(waiting) clip\(waiting == 1 ? "" : "s")")
                Button("Retry phone transfer") { model.sendPending() }
            }
            Section("Timers") {
                LabeledContent("Focus", value: "\(PomodoroKind.work.minutes) min")
                LabeledContent("Break", value: "\(PomodoroKind.break.minutes) min")
                LabeledContent("Timeout", value: "\(PomodoroKind.timeout.minutes) min")
                LabeledContent("Workout", value: "\(Int(RestTimer.seconds)) s")
                LabeledContent("Shortest clip", value: "\(Int(ShortRecording.minimum)) s")
            }
            if let message = model.message {
                Section("Last message") { Text(message).font(.footnote) }
            }
            Section {
                ForEach(model.captures) { capture in
                    VStack(alignment: .leading) {
                        HStack {
                            Text(capture.createdAt, style: .time)
                            if capture.asTodo { Image(systemName: "checklist").foregroundStyle(.green) }
                        }
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
            } header: {
                Text("Recordings")
            } footer: {
                Text("Saved on phone does not mean uploaded. Watch copies stay until you remove them after server receipt; transcription may still be processing.")
            }
        }
        .confirmationDialog("Remove this Watch recording?", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("Remove Watch copy", role: .destructive) {
                if let capture = removing { model.removeWatchCopy(capture) }
                removing = nil
            }
        } message: { Text("The phone and server copies are kept.") }
        .accessibilityIdentifier("page.status")
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
