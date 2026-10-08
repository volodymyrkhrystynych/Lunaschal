import SwiftUI
import WatchKit
import WatchConnectivity
import UserNotifications
import LunaschalCore

/// The pomodoro timer on the Watch. The rules are `PomodoroTimer` in
/// LunaschalCore; this owns the clock, the end-of-timer notification and
/// handing finished runs to the phone.
///
/// The notification (scheduled by `PomodoroEngine`) is what tells you the
/// time is up: an app with the wrist down is suspended, so nothing in-process
/// can be relied on to fire. Its buttons are the same Continue / Break /
/// Cancel as the screen.
@MainActor
final class PomodoroModel: ObservableObject {
    enum Action: String { case `continue` = "pomodoro.continue", takeBreak = "pomodoro.break", cancel = "pomodoro.cancel" }

    @Published private(set) var timer: PomodoroTimer
    @Published var message: String?
    /// State, outbox and notification live here, shared with the Controls,
    /// which can change the timer while this app is asleep.
    let engine: PomodoroEngine
    var outbox: PomodoroStore { engine.outbox }
    private let shortenedTo: TimeInterval?
    private var tick: Task<Void, Never>?

    init(engine: PomodoroEngine) {
        self.engine = engine
        #if DEBUG
        // `-PomodoroSeconds 10` as a launch argument, to see the end without waiting.
        let shortened = UserDefaults.standard.double(forKey: "PomodoroSeconds")
        shortenedTo = shortened > 0 ? shortened : nil
        #else
        shortenedTo = nil
        #endif
        timer = engine.load()
        refresh()
    }

    #if DEBUG
    /// `-PomodoroStart work|timeout`: starts a run at launch and opens its
    /// screen, so the simulator can be checked without tapping.
    static let launchStart = UserDefaults.standard.string(forKey: "PomodoroStart").flatMap(PomodoroKind.init(rawValue:))
    #else
    static let launchStart: PomodoroKind? = nil
    #endif

    static func registerCategories() {
        let next = UNNotificationAction(identifier: Action.continue.rawValue, title: "Continue")
        let rest = UNNotificationAction(identifier: Action.takeBreak.rawValue, title: "Break")
        let stop = UNNotificationAction(identifier: Action.cancel.rawValue, title: "Cancel", options: .destructive)
        UNUserNotificationCenter.current().setNotificationCategories([
            UNNotificationCategory(identifier: PomodoroKind.work.rawValue, actions: [next, rest, stop], intentIdentifiers: []),
            UNNotificationCategory(identifier: PomodoroKind.break.rawValue, actions: [next, stop], intentIdentifiers: []),
            UNNotificationCategory(identifier: PomodoroKind.timeout.rawValue, actions: [next, stop], intentIdentifiers: []),
        ])
    }

    static func requestAuthorization() {
        Task { _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) }
    }

    func start(_ kind: PomodoroKind) {
        Self.requestAuthorization()
        apply { ((), $0.start(kind)) }
    }

    /// A Focus or Timeout button on the face: starts unless something is
    /// already going, in which case it only shows the timer.
    @discardableResult
    func press(_ kind: PomodoroKind) -> PomodoroTimer.Press {
        Self.requestAuthorization()
        return apply { $0.press(kind) } ?? .alreadyGoing
    }

    func perform(_ action: Action) {
        switch action {
        case .continue: apply { ((), $0.continue()) }
        case .takeBreak: apply { ((), $0.takeBreak()) }
        case .cancel: apply { ((), $0.cancel()) }
        }
    }

    /// Catches up with the clock and with the Controls: on launch, on
    /// returning to the screen.
    func refresh() {
        let wasRunning = engine.load().run != nil || timer.run != nil
        apply { ((), $0.expire()) }
        if wasRunning, timer.run == nil, WKApplication.shared().applicationState == .active {
            WKInterfaceDevice.current().play(.notification)
        }
    }

    @discardableResult
    private func apply<T>(_ change: (inout PomodoroTimer) -> (T, [PomodoroSession])) -> T? {
        do {
            let (next, result, closed) = try engine.apply(change, shortenedTo: shortenedTo)
            timer = next
            wake()
            if !closed.isEmpty { sendPending() }
            return result
        } catch {
            message = error.localizedDescription
            return nil
        }
    }

    /// An in-process wake-up at the end of the run, so an open screen moves
    /// on without a tap. The notification covers the app being asleep.
    private func wake() {
        tick?.cancel()
        guard let run = timer.run else { return }
        let wait = max(1, run.endsAt.timeIntervalSinceNow)
        tick = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.refresh()
        }
    }

    /// Hands every run not yet acknowledged to the phone. transferUserInfo is
    /// queued by the system and survives either side being asleep; the run is
    /// deleted only when the phone says it has stored it.
    func sendPending() {
        let session = WCSession.default
        guard session.activationState == .activated else { return }
        let inFlight = Set(session.outstandingUserInfoTransfers.compactMap { $0.userInfo["pomodoroID"] as? String })
        do {
            for run in try outbox.list() where !inFlight.contains(run.id) {
                session.transferUserInfo(["pomodoroID": run.id, "pomodoroSession": try JSONEncoder().encode(run)])
            }
        } catch { message = error.localizedDescription }
    }
}

struct PomodoroView: View {
    @ObservedObject var model: PomodoroModel
    @Environment(\.scenePhase) private var phase

    var body: some View {
        ScrollView {
            VStack(spacing: 8) {
                switch model.timer.state {
                case .idle:
                    Button("Focus 25 / 5", systemImage: PomodoroKind.work.symbol) { model.start(.work) }
                    Button("Timeout 10", systemImage: PomodoroKind.timeout.symbol) { model.start(.timeout) }
                case .running(let run):
                    Text(run.kind.label).font(.headline)
                    Text(timerInterval: run.startedAt...run.endsAt, countsDown: true)
                        .font(.system(size: 40, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                    ProgressView(timerInterval: run.startedAt...run.endsAt, countsDown: true,
                                 label: { EmptyView() }, currentValueLabel: { EmptyView() })
                    Button("Cancel", role: .destructive) { model.perform(.cancel) }
                case .finished(let kind):
                    Text(kind == .break ? "Break's over" : "\(kind.label) done").font(.headline)
                    ForEach(model.timer.choices, id: \.self) { choice in
                        switch choice {
                        case .continue:
                            Button(continueLabel(kind)) { model.perform(.continue) }
                        case .takeBreak:
                            Button("Break · \(PomodoroKind.break.minutes) min") { model.perform(.takeBreak) }
                        }
                    }
                    Button("Cancel", role: .destructive) { model.perform(.cancel) }
                }
                if let message = model.message { Text(message).font(.footnote) }
            }
        }
        .navigationTitle("Timer")
        .onAppear { model.refresh() }
        .onChange(of: phase) { _, value in if value == .active { model.refresh() } }
    }

    private func continueLabel(_ kind: PomodoroKind) -> String {
        switch kind {
        case .work: return "Continue · \(PomodoroKind.work.minutes) min"
        case .break: return "Back to work · \(PomodoroKind.work.minutes) min"
        case .timeout: return "Another \(PomodoroKind.timeout.minutes) min"
        }
    }
}

/// The row on the Watch's main list: what the timer is doing at a glance.
struct PomodoroRow: View {
    @ObservedObject var model: PomodoroModel

    var body: some View {
        switch model.timer.state {
        case .idle:
            Label("Pomodoro timer", systemImage: "timer")
        case .running(let run):
            HStack {
                Label(run.kind.label, systemImage: "timer")
                Spacer()
                Text(timerInterval: run.startedAt...run.endsAt, countsDown: true).monospacedDigit()
            }
        case .finished(let kind):
            Label(kind == .break ? "Break's over" : "\(kind.label) done", systemImage: "bell")
        }
    }
}
