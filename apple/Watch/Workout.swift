import SwiftUI
import WatchKit
import UserNotifications
import WidgetKit
import LunaschalCore

/// The Workout timer: 90 seconds of rest, then a buzz that does not stop
/// until Stop or Restart. The rules are `RestTimer` in LunaschalCore; this
/// owns the buzz.
///
/// A suspended app cannot buzz, and a notification buzzes once. A smart-alarm
/// `WKExtendedRuntimeSession` is the one thing watchOS offers that wakes at a
/// set time and repeats a haptic until it is told to stop, so each run
/// schedules one for its end. It can only be scheduled from the foreground,
/// which is why there is no Workout Control: every way in opens the app.
/// A notification just after the end is the fallback for a session that
/// never starts.
@MainActor
final class WorkoutModel: NSObject, ObservableObject, WKExtendedRuntimeSessionDelegate {
    static let notificationID = "workout"
    /// Seconds between buzzes while ringing.
    static let buzzEvery: TimeInterval = 1.5

    @Published private(set) var timer: RestTimer
    @Published var message: String?
    private let stateURL: URL
    private let shortenedTo: TimeInterval?
    private var session: WKExtendedRuntimeSession?
    private var tick: Task<Void, Never>?

    /// `fallback` is used only without the App Group, as for the pomodoro.
    init(fallback: URL) {
        stateURL = WatchComplication.restTimerURL ?? fallback.appendingPathComponent("rest-timer.json")
        #if DEBUG
        // `-RestSeconds 5` as a launch argument, to see the end without waiting.
        let shortened = UserDefaults.standard.double(forKey: "RestSeconds")
        shortenedTo = shortened > 0 ? shortened : nil
        #else
        shortenedTo = nil
        #endif
        timer = RestTimer.load(from: stateURL) ?? RestTimer()
        super.init()
        refresh()
    }

    var isIdle: Bool { timer.state == .idle }

    /// Start, or Restart from running or ringing: a fresh 90 seconds.
    func start() {
        PomodoroModel.requestAuthorization()
        change { $0.shortenedTo = shortenedTo; $0.start() }
        schedule()
    }

    func stop() {
        change { $0.stop() }
        schedule()
    }

    /// Catches up with the clock: on launch and on returning to the screen.
    func refresh() {
        change { $0.expire() }
        // A run left from before a relaunch has lost its session.
        if timer.endsAt != nil, session == nil, WKApplication.shared().applicationState == .active { schedule() }
        wake()
    }

    /// A session the system relaunched the app for, at the end of a run.
    func adopt(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        session?.invalidate()
        session = extendedRuntimeSession
        extendedRuntimeSession.delegate = self
    }

    private func change(_ body: (inout RestTimer) -> Void) {
        let before = timer
        body(&timer)
        guard timer != before else { return }
        do { try timer.save(to: stateURL) } catch { message = error.localizedDescription }
        PomodoroEngine.redraw()
        wake()
    }

    /// One alarm session and one fallback notification at most, for the run
    /// going now. Restart and Stop both end the buzz by invalidating.
    private func schedule() {
        session?.invalidate()
        session = nil
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [Self.notificationID])
        center.removeDeliveredNotifications(withIdentifiers: [Self.notificationID])
        guard let endsAt = timer.endsAt else { return }

        let next = WKExtendedRuntimeSession()
        next.delegate = self
        next.start(at: endsAt)
        session = next

        let content = UNMutableNotificationContent()
        content.title = "Rest's over"
        content.body = "Back to it."
        content.sound = .default
        let wait = max(1, endsAt.timeIntervalSinceNow + 3)
        center.add(UNNotificationRequest(identifier: Self.notificationID, content: content,
                                         trigger: UNTimeIntervalNotificationTrigger(timeInterval: wait, repeats: false)))
    }

    /// An in-process wake-up at the end of the run, so an open screen turns
    /// to ringing without a tap. The session is what buzzes.
    private func wake() {
        tick?.cancel()
        guard let endsAt = timer.endsAt else { return }
        let wait = max(0.2, endsAt.timeIntervalSinceNow)
        tick = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.change { $0.expire() }
        }
    }

    // MARK: WKExtendedRuntimeSessionDelegate

    nonisolated func extendedRuntimeSessionDidStart(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        Task { @MainActor in
            guard extendedRuntimeSession === self.session else { return }
            // The session is buzzing; the fallback would only add a banner.
            UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: [Self.notificationID])
            self.change { $0.expire() }
            extendedRuntimeSession.notifyUser(hapticType: .notification) { _ in Self.buzzEvery }
        }
    }

    nonisolated func extendedRuntimeSessionWillExpire(_ extendedRuntimeSession: WKExtendedRuntimeSession) {}

    nonisolated func extendedRuntimeSession(_ extendedRuntimeSession: WKExtendedRuntimeSession,
                                            didInvalidateWith reason: WKExtendedRuntimeSessionInvalidationReason,
                                            error: Error?) {
        Task { @MainActor in
            // Restart and Stop invalidate the old session after replacing it.
            guard extendedRuntimeSession === self.session else { return }
            self.session = nil
            if let error {
                self.message = "Couldn't set the alarm: \(error.localizedDescription)"
            } else if self.timer.isRinging {
                // Dismissed from the system's alarm screen: that is a Stop.
                self.change { $0.stop() }
            }
        }
    }
}

/// The Workout page: one big button, the countdown, or the ringing screen.
struct WorkoutPage: View {
    @ObservedObject var model: WorkoutModel

    var body: some View {
        VStack(spacing: 6) {
            switch model.timer.state {
            case .idle:
                BigButton(title: "Workout 90s", symbol: "dumbbell", tint: .orange) { model.start() }
            case .running(let startedAt, let endsAt):
                Label("Rest", systemImage: "dumbbell").font(.headline)
                Text(timerInterval: startedAt...endsAt, countsDown: true)
                    .font(.system(size: 44, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                ProgressView(timerInterval: startedAt...endsAt, countsDown: true,
                             label: { EmptyView() }, currentValueLabel: { EmptyView() })
                    .tint(.orange)
                HStack {
                    Button("Restart", systemImage: "arrow.counterclockwise") { model.start() }
                    Button("Stop", systemImage: "stop.fill", role: .destructive) { model.stop() }
                }
                .labelStyle(.iconOnly)
            case .ringing:
                Text("Rest's over").font(.headline)
                BigButton(title: "Restart", symbol: "arrow.counterclockwise", tint: .orange, compact: true) { model.start() }
                Button("Stop", systemImage: "stop.fill", role: .destructive) { model.stop() }
            }
            if let message = model.message { Text(message).font(.footnote) }
        }
        .accessibilityIdentifier("page.workout")
    }
}
