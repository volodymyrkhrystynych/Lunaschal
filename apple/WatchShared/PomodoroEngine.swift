import Foundation
import UserNotifications
import WidgetKit
import LunaschalCore

/// The timer as files: its state, the runs waiting for the phone, and the
/// end-of-timer notification. Compiled into the Watch app and its widget
/// extension, because a Control starts a timer from the extension's process
/// with the app asleep; whichever process changes it, the other reads the
/// change from the shared container.
struct PomodoroEngine {
    static let notificationID = "pomodoro"

    let stateURL: URL
    let outbox: PomodoroStore

    /// `fallback` is used only without the App Group (an unsigned simulator
    /// build), where the app keeps the timer to itself as it did before.
    init(fallback: URL) throws {
        let root = WatchComplication.pomodoroRoot ?? fallback
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        if root != fallback { try Self.adopt(from: fallback, into: root) }
        stateURL = root.appendingPathComponent("timer.json")
        outbox = try PomodoroStore(root: root.appendingPathComponent("outbox", isDirectory: true))
    }

    func load() -> PomodoroTimer { PomodoroTimer.load(from: stateURL) ?? PomodoroTimer() }

    /// Applies one transition and writes it down: the state, the runs it
    /// closed (for the phone), the notification for whatever now runs, and
    /// a redraw of everything on the face and in Control Center.
    @discardableResult
    func apply<T>(_ change: (inout PomodoroTimer) -> (T, [PomodoroSession]),
                  shortenedTo: TimeInterval? = nil) throws -> (PomodoroTimer, T, [PomodoroSession]) {
        let before = load()
        var timer = before
        if let shortenedTo { timer.shortenedTo = shortenedTo }
        let (result, closed) = change(&timer)
        try timer.save(to: stateURL)
        for run in closed { try outbox.save(run) }
        if timer.state != before.state {
            Self.schedule(timer)
            Self.redraw()
        }
        return (timer, result, closed)
    }

    /// One pending notification at most, for the run going now.
    static func schedule(_ timer: PomodoroTimer) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [notificationID])
        center.removeDeliveredNotifications(withIdentifiers: [notificationID])
        guard let run = timer.run else { return }
        let content = UNMutableNotificationContent()
        content.title = run.kind == .break ? "Break's over" : "\(run.kind.label) done"
        content.body = run.kind == .work ? "Continue, take a break, or stop." : "Continue or stop."
        content.sound = .default
        content.categoryIdentifier = run.kind.rawValue
        let wait = max(1, run.endsAt.timeIntervalSinceNow)
        center.add(UNNotificationRequest(identifier: notificationID, content: content,
                                         trigger: UNTimeIntervalNotificationTrigger(timeInterval: wait, repeats: false)))
    }

    static func redraw() {
        for kind in WatchComplication.timerKinds { WidgetCenter.shared.reloadTimelines(ofKind: kind) }
        ControlCenter.shared.reloadControls(ofKind: WatchComplication.focusControl)
        ControlCenter.shared.reloadControls(ofKind: WatchComplication.timeoutControl)
    }

    /// Moves a timer kept in the app's own container, from a build before the
    /// App Group, into the shared one. Only when the shared one has none yet.
    private static func adopt(from old: URL, into root: URL) throws {
        let fm = FileManager.default
        let oldState = old.appendingPathComponent("timer.json")
        guard fm.fileExists(atPath: oldState.path),
              !fm.fileExists(atPath: root.appendingPathComponent("timer.json").path) else { return }
        let oldOutbox = old.appendingPathComponent("outbox", isDirectory: true)
        let newOutbox = root.appendingPathComponent("outbox", isDirectory: true)
        try fm.createDirectory(at: newOutbox, withIntermediateDirectories: true)
        for file in (try? fm.contentsOfDirectory(at: oldOutbox, includingPropertiesForKeys: nil)) ?? [] {
            try fm.moveItem(at: file, to: newOutbox.appendingPathComponent(file.lastPathComponent))
        }
        try fm.moveItem(at: oldState, to: root.appendingPathComponent("timer.json"))
    }
}

extension PomodoroEngine {
    /// The app's own fallback root. The same path in the extension is the
    /// extension's container, which only matters without the App Group.
    static func standard() throws -> PomodoroEngine {
        let support = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                  appropriateFor: nil, create: true)
        return try PomodoroEngine(fallback: support.appendingPathComponent("Pomodoro", isDirectory: true))
    }

    /// Asks the app to show `link` next time it comes forward. A Control's
    /// intent may run in the extension, which cannot navigate the app itself.
    static func requestOpen(_ link: WatchLink) {
        guard let url = WatchComplication.pendingLinkURL else { return }
        try? Data(link.url.absoluteString.utf8).write(to: url, options: .atomic)
    }

    /// The link a Control left for the app, removed as it is read.
    static func takePendingLink() -> WatchLink? {
        guard let url = WatchComplication.pendingLinkURL,
              let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        try? FileManager.default.removeItem(at: url)
        return URL(string: text).flatMap(WatchLink.init(url:))
    }
}
