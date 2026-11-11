import Foundation

/// Where a Watch complication sends the app. Each complication, and each
/// button on the launcher, opens the app on one of these.
public enum WatchLink: Equatable {
    /// The timer screen, whatever it is doing.
    case timer
    /// Start a run, unless one is already going (see `PomodoroTimer.startUnlessRunning`).
    case start(PomodoroKind)
    case record
    case transcribe
    /// A Transcribe clip that also becomes a to-do.
    case todo
    /// The Workout page: starts the 90-second rest timer unless it is already going.
    case workout

    public static let scheme = "lunaschal-watch"

    public var url: URL {
        switch self {
        case .timer: return URL(string: "\(Self.scheme)://timer")!
        case .start(let kind): return URL(string: "\(Self.scheme)://timer/\(kind.rawValue)")!
        case .record: return URL(string: "\(Self.scheme)://record")!
        case .transcribe: return URL(string: "\(Self.scheme)://transcribe")!
        case .todo: return URL(string: "\(Self.scheme)://todo")!
        case .workout: return URL(string: "\(Self.scheme)://workout")!
        }
    }

    public init?(url: URL) {
        guard url.scheme == Self.scheme else { return nil }
        let path = url.pathComponents.filter { $0 != "/" }
        switch (url.host, path.count) {
        case ("timer", 0): self = .timer
        case ("timer", 1):
            // A break follows a focus run; it is never started from the face.
            guard let kind = PomodoroKind(rawValue: path[0]), kind != .break else { return nil }
            self = .start(kind)
        case ("record", 0): self = .record
        case ("transcribe", 0): self = .transcribe
        case ("todo", 0): self = .todo
        case ("workout", 0): self = .workout
        default: return nil
        }
    }
}

/// What the Watch app, its complications and its Controls share. The
/// complications and Controls run in their own process, so everything they
/// read or change lives in an App Group container all of them are entitled to.
public enum WatchComplication {
    public static let appGroup = "group.com.lunaschal.mobile.watch"
    /// WidgetKit kinds, so the app can reload the ones that changed.
    public static let pomodoroKind = "pomodoro"
    public static let focusKind = "focus"
    public static let timeoutKind = "timeout"
    public static let recordKind = "record"
    public static let transcribeKind = "transcribe"
    public static let todoKind = "todo"
    public static let launcherKind = "launcher"
    public static let workoutKind = "workout"
    /// Control kinds, for `ControlCenter.reloadControls(ofKind:)`.
    public static let focusControl = "control.focus"
    public static let timeoutControl = "control.timeout"
    public static let recordControl = "control.record"
    public static let transcribeControl = "control.transcribe"
    public static let todoControl = "control.todo"

    /// App Groups exist only on Apple platforms; on Linux (where the core's
    /// tests also run) there is no shared container.
    public static var container: URL? {
        #if canImport(Darwin)
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)
        #else
        nil
        #endif
    }
    /// The timer's state and its outbox, owned by whichever process changed it last.
    public static var pomodoroRoot: URL? { container?.appendingPathComponent("Pomodoro", isDirectory: true) }
    public static var timerURL: URL? { pomodoroRoot?.appendingPathComponent("timer.json") }
    /// The Workout timer's state. Written by the app, read by its complication.
    public static var restTimerURL: URL? { container?.appendingPathComponent("rest-timer.json") }
    /// What the Watch is recording, if anything. Written by the app.
    public static var recordingURL: URL? { container?.appendingPathComponent("recording.json") }
    /// A screen a Control asked the app to open, consumed when it next comes forward.
    public static var pendingLinkURL: URL? { container?.appendingPathComponent("pending-link") }
}

/// A recording in progress, as the complications and Controls see it.
public struct RecordingStatus: Codable, Equatable {
    public let mode: CaptureMode
    /// A To-do clip: Transcribe's mode, but its own button, so the mode alone
    /// cannot say which button is lit.
    public let asTodo: Bool
    public let startedAt: Date

    public init(mode: CaptureMode, asTodo: Bool = false, startedAt: Date) {
        self.mode = mode; self.asTodo = asTodo; self.startedAt = startedAt
    }

    /// Whether this is the clip that button (`mode`, `asTodo`) started.
    public func matches(mode: CaptureMode, asTodo: Bool) -> Bool {
        self.mode == mode && self.asTodo == asTodo
    }

    private enum CodingKeys: String, CodingKey { case mode, asTodo, startedAt }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = try c.decode(CaptureMode.self, forKey: .mode)
        // Written before the To-do button existed.
        asTodo = try c.decodeIfPresent(Bool.self, forKey: .asTodo) ?? false
        startedAt = try c.decode(Date.self, forKey: .startedAt)
    }

    public static func load(from url: URL) -> RecordingStatus? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(RecordingStatus.self, from: data)
    }

    /// Writes `status`, or removes the file when nothing is recording.
    public static func save(_ status: RecordingStatus?, to url: URL) throws {
        guard let status else {
            if FileManager.default.fileExists(atPath: url.path) { try FileManager.default.removeItem(at: url) }
            return
        }
        try JSONEncoder().encode(status).write(to: url, options: .atomic)
    }
}
