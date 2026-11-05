import SwiftUI
import WatchKit
import WatchConnectivity
import UserNotifications
import LunaschalCore

/// The pomodoro timer on the Watch. The rules are `PomodoroTimer` in
/// LunaschalCore; this owns the clock, the end-of-timer notification and
/// handing finished runs to the phone.
///
/// At the end, `WatchAlarm` buzzes until Continue, Break or Cancel, as the
/// Workout timer does. The notification (scheduled by `PomodoroEngine`, just
/// after the end) is the fallback for a run with no alarm: one started by a
/// Control while the app was asleep. Its buttons are the same Continue /
/// Break / Cancel as the screen.
@MainActor
final class PomodoroModel: ObservableObject {
    enum Action: String { case `continue` = "pomodoro.continue", takeBreak = "pomodoro.break", cancel = "pomodoro.cancel" }

    @Published private(set) var timer: PomodoroTimer
    @Published var message: String?
    /// State, outbox and notification live here, shared with the Controls,
    /// which can change the timer while this app is asleep.
    let engine: PomodoroEngine
    /// After every change, so the alarm can follow.
    var onChange: (() -> Void)?
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
            onChange?()
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

/// The Focus or Timeout page: one big button when nothing is going, the
/// countdown for the run this page owns, or the choices once it is up.
struct TimerPage: View {
    let choice: TimerChoice
    @ObservedObject var model: PomodoroModel
    /// Turns to the page that owns the run, when this page's button was
    /// pressed while the other timer is going.
    let show: (TimerChoice) -> Void

    var body: some View {
        VStack(spacing: 6) {
            switch model.timer.state {
            case .running(let run) where choice.owns(run):
                Label(run.kind.label, systemImage: run.kind.symbol).font(.headline)
                Text(timerInterval: run.startedAt...run.endsAt, countsDown: true)
                    .font(.system(size: 40, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                ProgressView(timerInterval: run.startedAt...run.endsAt, countsDown: true,
                             label: { EmptyView() }, currentValueLabel: { EmptyView() })
                    .tint(tint)
                Button("Cancel", role: .destructive) { model.perform(.cancel) }
            case .finished(let kind) where choice.owns(kind):
                Text(kind == .break ? "Break's over" : "\(kind.label) done").font(.headline)
                ForEach(model.timer.choices, id: \.self) { next in
                    switch next {
                    case .continue:
                        Button(continueLabel(kind)) { model.perform(.continue) }.tint(tint)
                    case .takeBreak:
                        Button("Break · \(PomodoroKind.break.minutes) min") { model.perform(.takeBreak) }
                    }
                }
                Button("Cancel", role: .destructive) { model.perform(.cancel) }
            default:
                BigButton(title: "\(choice.title) \(choice.kind.minutes)", symbol: choice.kind.symbol, tint: tint) {
                    if model.press(choice.kind) == .alreadyGoing, let other = owner, other != choice { show(other) }
                }
            }
            if let message = model.message { Text(message).font(.footnote) }
        }
        .accessibilityIdentifier("page.\(choice.rawValue)")
    }

    private var tint: Color { choice == .focus ? .indigo : .teal }

    /// Which page the timer's current run (or finished run) belongs to.
    private var owner: TimerChoice? { model.timer.owner }

    private func continueLabel(_ kind: PomodoroKind) -> String {
        switch kind {
        case .work: return "Continue · \(PomodoroKind.work.minutes) min"
        case .break: return "Back to work · \(PomodoroKind.work.minutes) min"
        case .timeout: return "Another \(PomodoroKind.timeout.minutes) min"
        }
    }
}

extension PomodoroTimer {
    /// The page whose run is going or waiting for a choice, if any.
    var owner: TimerChoice? {
        switch state {
        case .idle: return nil
        case .running(let run): return run.kind == .timeout ? .timeout : .focus
        case .finished(let kind): return kind == .timeout ? .timeout : .focus
        }
    }
}

/// A page's one action, filling the screen.
struct BigButton: View {
    let title: String
    let symbol: String
    let tint: Color
    /// Icon beside the title rather than above it, for a page that has
    /// another control under its main one.
    var compact = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Group {
                if compact {
                    Label(title, systemImage: symbol).font(.title3.weight(.semibold))
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: symbol).font(.system(size: 44, weight: .semibold))
                        Text(title).font(.title3.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.6)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .buttonBorderShape(.roundedRectangle(radius: 24))
        .tint(tint)
    }
}
