import SwiftUI
import WatchKit
import WatchConnectivity
import AVFoundation
import UserNotifications
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
    @Published var captures: [Capture] = []
    @Published var received: Set<String> = []
    @Published var uploaded: Set<String> = []
    @Published var message: String?

    private init(store: CaptureStore) throws {
        self.store = store
        receiptRoot = store.root
        recorder = Recorder(store: store)
        pomodoro = try PomodoroModel(root: store.root.deletingLastPathComponent()
            .appendingPathComponent("Pomodoro", isDirectory: true))
        pomodoroOutbox = pomodoro.outbox.root
        super.init()
        try store.recoverInterruptedRecordings()
        recorder.onChange = { [weak self] in self?.reload(); self?.sendPending() }
        recorder.onError = { [weak self] in self?.message = $0.localizedDescription }
        reload()
        WCSession.default.delegate = self
        WCSession.default.activate()
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

private struct WatchCaptureView: View {
    @ObservedObject var model: WatchModel
    @ObservedObject var recorder: Recorder
    @Environment(\.scenePhase) private var phase
    @State private var removing: Capture?
    @State private var showTimer = false

    var body: some View {
        NavigationStack {
            list.navigationDestination(isPresented: $showTimer) { PomodoroView(model: model.pomodoro) }
        }
        .onAppear {
            if let kind = PomodoroModel.launchStart, model.pomodoro.timer.run == nil {
                model.pomodoro.start(kind); showTimer = true
            }
        }
    }

    private var list: some View {
        List {
            Button { showTimer = true } label: { PomodoroRow(model: model.pomodoro) }
            if recorder.activeID != nil {
                Text("Recording…").foregroundStyle(.red)
                Button("Stop and save", role: .destructive) { recorder.stop() }
            } else {
                Button("Transcribe", systemImage: "waveform") { Task { await recorder.start(mode: .transcribe) } }
                    .disabled(recorder.isStarting)
                Button("Record", systemImage: "mic") { Task { await recorder.start(mode: .record) } }
                    .disabled(recorder.isStarting)
            }
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
        .onChange(of: phase) { _, value in
            if value == .active { model.reload(); model.sendPending(); model.pomodoro.refresh(); model.pomodoro.sendPending() }
        }
        .confirmationDialog("Remove this Watch recording?", isPresented: Binding(
            get: { removing != nil }, set: { if !$0 { removing = nil } })) {
            Button("Remove Watch copy", role: .destructive) {
                if let capture = removing { model.removeWatchCopy(capture) }
                removing = nil
            }
        } message: { Text("The phone and server copies are kept.") }
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
    /// A button on the timer's notification: Continue, Break or Cancel.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let action = PomodoroModel.Action(rawValue: response.actionIdentifier)
        await MainActor.run {
            guard case .success(let model) = WatchModel.startup else { return }
            if let action { model.pomodoro.perform(action) } else { model.pomodoro.refresh() }
        }
    }

    /// The app is open when the time is up: the screen already shows the
    /// choices, so a haptic is enough and no banner covers them.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        await MainActor.run {
            if case .success(let model) = WatchModel.startup { model.pomodoro.refresh() }
        }
        return []
    }

    private func completeIfIdle() {
        guard WCSession.default.activationState == .activated, !WCSession.default.hasContentPending else { return }
        pending.forEach { $0.setTaskCompletedWithSnapshot(false) }
        pending.removeAll()
    }
}
