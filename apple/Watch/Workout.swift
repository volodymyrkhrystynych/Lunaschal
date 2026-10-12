import SwiftUI
import WatchKit
import UserNotifications
import LunaschalCore

/// The Workout timer: 90 seconds of rest, then a buzz that does not stop
/// until Stop or Restart. The rules are `RestTimer` in LunaschalCore; the buzz
/// is `WatchAlarm`, shared with Focus and Timeout. That alarm can only be
/// scheduled from the foreground, which is why there is no Workout Control:
/// every way in opens the app. A notification just after the end is the
/// fallback for an alarm that never starts.
@MainActor
final class WorkoutModel: ObservableObject {
    static let notificationID = "workout"

    @Published private(set) var timer: RestTimer
    @Published var message: String?
    /// After every change, so the alarm can follow.
    var onChange: (() -> Void)?
    private let stateURL: URL
    private let shortenedTo: TimeInterval?
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
        refresh()
    }

    var isIdle: Bool { timer.state == .idle }

    /// Start, or Restart from running or ringing: a fresh 90 seconds.
    func start() {
        PomodoroModel.requestAuthorization()
        change { $0.shortenedTo = shortenedTo; $0.start() }
        notify()
    }

    func stop() {
        change { $0.stop() }
        notify()
    }

    /// Catches up with the clock: on launch and on returning to the screen.
    func refresh() {
        change { $0.expire() }
        wake()
    }

    private func change(_ body: (inout RestTimer) -> Void) {
        let before = timer
        body(&timer)
        guard timer != before else { return }
        do { try timer.save(to: stateURL) } catch { message = error.localizedDescription }
        PomodoroEngine.redraw()
        wake()
        onChange?()
    }

    /// The fallback notification for the run going now, if any.
    private func notify() {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [Self.notificationID])
        center.removeDeliveredNotifications(withIdentifiers: [Self.notificationID])
        guard let endsAt = timer.endsAt else { return }
        let content = UNMutableNotificationContent()
        content.title = "Rest's over"
        content.body = "Back to it."
        content.sound = .default
        let wait = max(1, endsAt.timeIntervalSinceNow + 3)
        center.add(UNNotificationRequest(identifier: Self.notificationID, content: content,
                                         trigger: UNTimeIntervalNotificationTrigger(timeInterval: wait, repeats: false)))
    }

    /// An in-process wake-up at the end of the run, so an open screen turns
    /// to ringing without a tap. The alarm is what buzzes.
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
