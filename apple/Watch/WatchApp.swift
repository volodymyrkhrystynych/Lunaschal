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
    /// Switches made here, kept until the phone says it has them.
    nonisolated private let activityOutbox: URL
    let recorder: Recorder
    let activity: ActivityStore
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
    /// Work, leisure or mix: the newer of the last switch made here and the
    /// one the phone last told us about. Nil until either has happened.
    @Published private(set) var activityState: ActivityState?
    /// Switches not yet acknowledged by the phone.
    @Published private(set) var pendingSwitches = 0
    /// The warning over the pages after a stray clip was thrown away.
    @Published private(set) var notice: String?
    private var noticeExpiry: Task<Void, Never>?

    private init(store: CaptureStore) throws {
        self.store = store
        receiptRoot = store.root
        recorder = Recorder(store: store)
        activity = try ActivityStore(root: store.root.deletingLastPathComponent()
            .appendingPathComponent("Activity", isDirectory: true))
        activityOutbox = activity.root
        super.init()
        try store.recoverInterruptedRecordings()
        recorder.onChange = { [weak self] in self?.reload(); self?.sendPending(); self?.publishRecording() }
        recorder.onError = { [weak self] in self?.message = $0.localizedDescription }
        recorder.minimumLength = ShortRecording.minimum
        recorder.onDiscarded = { [weak self] in self?.warnDiscarded() }
        reload()
        refreshActivity()
        publishRecording() // clears a status left behind by a crash mid-clip
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    /// Every complication and Control reads one shared entry, so they are
    /// all redrawn together: reloading only the kinds a change "belongs" to
    /// is how the launcher row once went on showing a stale button.
    static func redraw() {
        WidgetCenter.shared.reloadAllTimelines()
        ControlCenter.shared.reloadAllControls()
    }

    /// For the banner a too-short clip leaves when the app is out of sight.
    static func requestNotificationAuthorization() {
        Task { _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) }
    }

    /// Switches to `state` now: written for the complications at once, then
    /// queued for the phone. A tile for the state already on does nothing,
    /// so a second tap is not a second switch.
    func switchActivity(_ state: ActivityState) {
        guard state != activityState else { return }
        let change = ActivitySwitch(state: state, source: .watch)
        do {
            try activity.save(change)
            if let url = WatchComplication.stateURL { try change.save(to: url) }
        } catch { message = error.localizedDescription; return }
        activityState = state
        WKInterfaceDevice.current().play(.click)
        Self.redraw()
        sendPending()
    }

    /// The state to show: this Watch's last switch or the phone's word,
    /// whichever was made later.
    func refreshActivity() {
        let local = WatchComplication.stateURL.flatMap(ActivitySwitch.load(from:))
        let phone = WatchComplication.glanceURL.flatMap(WatchGlance.load(from:))?.state
        activityState = ActivitySwitch.latest([local, phone])?.state
        pendingSwitches = (try? activity.list().count) ?? 0
    }

    /// Starts a clip for `choice`, or stops and saves the one going, unless
    /// it is too short to have been meant. One press does both, from a tile,
    /// the face or a Control.
    func toggleRecording(_ choice: RecordingChoice) async {
        if recorder.activeID != nil { recorder.stop(); return }
        guard !recorder.isStarting else { return }
        Self.requestNotificationAuthorization() // for the too-short warning
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
        Self.redraw()
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
        let switchesInFlight = Set(session.outstandingUserInfoTransfers.compactMap { $0.userInfo["activitySwitchID"] as? String })
        for change in (try? activity.list()) ?? [] where !switchesInFlight.contains(change.id) {
            do {
                session.transferUserInfo(["activitySwitchID": change.id,
                                          "activitySwitch": try JSONEncoder().encode(change)])
            } catch { message = error.localizedDescription }
        }
        pendingSwitches = (try? activity.list().count) ?? 0
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
            if state == .activated {
                // A context the phone sent while this app was not running.
                self.accept(WatchGlance(context: session.receivedApplicationContext))
                self.sendPending()
            }
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        let reachable = session.isReachable
        Task { @MainActor in self.phoneReachable = reachable }
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        if let id = userInfo["activityStored"] as? String {
            // The phone has it on disk; the Watch's copy can go.
            do { try ActivityStore(root: activityOutbox).remove(id) }
            catch { Task { @MainActor in self.message = error.localizedDescription } }
            Task { @MainActor in self.refreshActivity() }
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

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        let glance = WatchGlance(context: context)
        Task { @MainActor in self.accept(glance) }
    }

    /// Keeps the phone's latest glance for the complications, which read it
    /// from the App Group, and shows its state if it is the newer one.
    private func accept(_ glance: WatchGlance?) {
        guard let glance, let url = WatchComplication.glanceURL else { return }
        if WatchGlance.load(from: url) == glance { return }
        do { try glance.save(to: url) } catch { message = error.localizedDescription; return }
        refreshActivity()
        Self.redraw()
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

private struct WatchCaptureView: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var recorder: Recorder
    @Environment(\.scenePhase) private var phase
    @State private var page: WatchPage = .grid

    var body: some View {
        TabView(selection: $page) {
            WatchGrid(model: model, recorder: recorder)
                .tag(WatchPage.grid)
            WatchStatus(model: model, recorder: recorder)
                .tag(WatchPage.status)
        }
        .tabViewStyle(.page)
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
        }
        .onOpenURL { url in
            if let link = WatchLink(url: url) { open(link) }
        }
        .onChange(of: phase) { _, value in
            if value == .active {
                model.reload(); model.sendPending(); model.refreshActivity()
            } else if value == .inactive {
                // The face is coming back: draw it from the latest state, in
                // case a reload made mid-change was dropped.
                WatchModel.redraw()
            }
        }
    }

    /// A tap on a complication: shows the grid and, since the face cannot act
    /// without opening the app, does the thing at once. Record, Transcribe and
    /// To-do start or stop a clip, whose tile shows it; Habits and State only
    /// open the grid, where the state tiles are.
    private func open(_ link: WatchLink) {
        page = .grid
        switch link {
        case .home:
            break
        case .record, .transcribe, .todo:
            let choice: RecordingChoice = link == .record ? .record : link == .todo ? .todo : .transcribe
            Task { await model.toggleRecording(choice) }
        }
    }
}

/// Every action on one screen: work, leisure and mix over the three kinds of
/// clip. A tile shows its own state, so the grid is also the glance at what
/// is going.
private struct WatchGrid: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var recorder: Recorder

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 6), count: 3)

    var body: some View {
        GeometryReader { box in
            let height = max(44, (box.size.height - 6) / 2)
            LazyVGrid(columns: columns, spacing: 6) {
                ForEach(ActivityState.allCases) { state in
                    activity(state).frame(height: height)
                }
                recording(.record).frame(height: height)
                recording(.transcribe).frame(height: height)
                recording(.todo).frame(height: height)
            }
        }
        .accessibilityIdentifier("page.grid")
    }

    /// The state that is on is filled; tapping another switches to it.
    private func activity(_ state: ActivityState) -> some View {
        let on = model.activityState == state
        return Button { model.switchActivity(state) } label: {
            VStack(spacing: 2) {
                ActivityGlyph(state: state, active: on).frame(maxHeight: 30)
                Text(state.title)
                    .font(.footnote.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.white.opacity(on ? 0.18 : 0.06), in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(state.title)
        .accessibilityAddTraits(on ? .isSelected : [])
        .accessibilityIdentifier("tile.\(state.rawValue)")
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
/// and switches still on their way, and the recordings themselves.
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
            Section("State") {
                LabeledContent("Now", value: model.activityState?.title ?? "Not set")
                LabeledContent("Waiting", value: model.pendingSwitches == 0 ? "None" :
                               "\(model.pendingSwitches) switch\(model.pendingSwitches == 1 ? "" : "es")")
            }
            Section("Recording") {
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
    /// The app is open when a notification arrives (a discarded clip's): the
    /// banner over the pages already says it, so nothing more is shown.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        []
    }

    private func completeIfIdle() {
        guard WCSession.default.activationState == .activated, !WCSession.default.hasContentPending else { return }
        pending.forEach { $0.setTaskCompletedWithSnapshot(false) }
        pending.removeAll()
    }
}
