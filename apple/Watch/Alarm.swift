import Foundation
import WatchKit
import UserNotifications

/// The buzz that goes on until it is answered, for every timer on the Watch:
/// Focus, Timeout and Workout.
///
/// A suspended app cannot buzz, and a notification buzzes once. A smart-alarm
/// `WKExtendedRuntimeSession` is the one thing watchOS offers that wakes at a
/// set time and repeats a haptic until it is told to stop. An app gets one
/// such session at a time, so this keeps one, always scheduled for the
/// earliest end, and lets it buzz while any timer that rang is unanswered.
/// It can only be scheduled from the foreground; a run started by a Control
/// with the app asleep gets its session the next time the app comes forward,
/// and until then has its notification.
@MainActor
final class WatchAlarm: NSObject, WKExtendedRuntimeSessionDelegate {
    /// What the timers want: the next end to wake for, and which of them are
    /// ringing now (`WatchAlarm.rest`, `WatchAlarm.pomodoro`).
    struct Plan: Equatable {
        var nextEnd: Date?
        var ringing: Set<String> = []
    }

    static let rest = "rest"
    static let pomodoro = "pomodoro"
    /// Seconds between buzzes while ringing.
    static let buzzEvery: TimeInterval = 1.5

    /// The session has started: the timers catch up with the clock.
    var onRing: (() -> Void)?
    /// Dismissed from the system's alarm screen.
    var onDismiss: (() -> Void)?
    var onError: ((String) -> Void)?

    private var plan = Plan()
    private var session: WKExtendedRuntimeSession?
    private var scheduledFor: Date?
    private var buzzing = false
    /// Ringing timers the buzz has been dismissed for.
    private var dismissed: Set<String> = []
    /// Ringing timers that rang with no session to buzz them: a run that
    /// ended while the app was asleep without one. They are not started
    /// buzzing late, when the app next comes forward.
    private var unbuzzed: Set<String> = []
    /// While the timers catch up at the start, so a plan half caught up
    /// cannot stop the buzz.
    private var holding = false

    func update(_ next: Plan) {
        plan = next
        guard !holding else { return }
        dismissed.formIntersection(next.ringing)
        unbuzzed.formIntersection(next.ringing)
        let fresh = next.ringing.subtracting(dismissed).subtracting(unbuzzed)

        if buzzing {
            if !fresh.isEmpty { return }
            buzzing = false
            clear()
        } else if !fresh.isEmpty {
            // The in-process tick can see the end a moment before the session
            // starts; that session is the one that will buzz.
            if session != nil, let at = scheduledFor, at.timeIntervalSinceNow < 2 { return }
            unbuzzed.formUnion(fresh)
        }

        guard let end = next.nextEnd, end > Date() else { clear(); return }
        if session != nil, scheduledFor == end { return }
        clear()
        guard WKApplication.shared().applicationState != .background else { return }
        let alarm = WKExtendedRuntimeSession()
        alarm.delegate = self
        alarm.start(at: end)
        session = alarm
        scheduledFor = end
    }

    /// A session the system relaunched the app for, at an end.
    func adopt(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        clear()
        session = extendedRuntimeSession
        scheduledFor = Date()
        extendedRuntimeSession.delegate = self
    }

    private func clear() {
        let old = session
        session = nil
        scheduledFor = nil
        old?.invalidate()
    }

    /// The fallback notifications for the end that just came: the session
    /// is buzzing, so they would only add a banner. Later ends keep theirs.
    private static func removeDueNotifications() async {
        let center = UNUserNotificationCenter.current()
        let soon = Date().addingTimeInterval(10)
        let due = await center.pendingNotificationRequests().filter {
            ($0.trigger as? UNTimeIntervalNotificationTrigger)?.nextTriggerDate().map { $0 <= soon } ?? false
        }
        center.removePendingNotificationRequests(withIdentifiers: due.map(\.identifier))
    }

    // MARK: WKExtendedRuntimeSessionDelegate

    nonisolated func extendedRuntimeSessionDidStart(_ extendedRuntimeSession: WKExtendedRuntimeSession) {
        Task { @MainActor in
            guard extendedRuntimeSession === self.session else { return }
            await Self.removeDueNotifications()
            guard extendedRuntimeSession === self.session else { return }
            self.buzzing = true
            self.unbuzzed = []
            self.holding = true
            self.onRing?()
            self.holding = false
            self.update(self.plan)
            guard self.buzzing else { return }
            extendedRuntimeSession.notifyUser(hapticType: .notification) { _ in Self.buzzEvery }
        }
    }

    nonisolated func extendedRuntimeSessionWillExpire(_ extendedRuntimeSession: WKExtendedRuntimeSession) {}

    nonisolated func extendedRuntimeSession(_ extendedRuntimeSession: WKExtendedRuntimeSession,
                                            didInvalidateWith reason: WKExtendedRuntimeSessionInvalidationReason,
                                            error: Error?) {
        Task { @MainActor in
            // A reschedule invalidates the old session after replacing it.
            guard extendedRuntimeSession === self.session else { return }
            self.session = nil
            self.scheduledFor = nil
            if self.buzzing {
                self.buzzing = false
                self.dismissed.formUnion(self.plan.ringing)
                self.onDismiss?()
            } else if let error {
                self.onError?("Couldn't set the alarm: \(error.localizedDescription)")
            }
        }
    }
}
