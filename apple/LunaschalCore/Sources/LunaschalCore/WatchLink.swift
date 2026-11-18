import Foundation

/// Where a Watch complication sends the app. Each complication, and each
/// button on the launcher, opens the app on one of these.
public enum WatchLink: Equatable {
    /// The app's grid and nothing else: the Habits and State complications,
    /// which sit between the microphones on the launcher, so a tap meant for a
    /// neighbour that lands on one of them costs nothing.
    case home
    case record
    case transcribe
    /// A Transcribe clip that also becomes a to-do.
    case todo

    public static let scheme = "lunaschal-watch"

    public var url: URL {
        switch self {
        case .home: return URL(string: "\(Self.scheme)://home")!
        case .record: return URL(string: "\(Self.scheme)://record")!
        case .transcribe: return URL(string: "\(Self.scheme)://transcribe")!
        case .todo: return URL(string: "\(Self.scheme)://todo")!
        }
    }

    public init?(url: URL) {
        guard url.scheme == Self.scheme, url.pathComponents.filter({ $0 != "/" }).isEmpty else { return nil }
        switch url.host {
        case "home": self = .home
        case "record": self = .record
        case "transcribe": self = .transcribe
        case "todo": self = .todo
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
    public static let recordKind = "record"
    public static let transcribeKind = "transcribe"
    public static let todoKind = "todo"
    public static let launcherKind = "launcher"
    public static let stateKind = "state"
    public static let habitsKind = "habits"
    /// Control kinds, for `ControlCenter.reloadControls(ofKind:)`.
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
    /// What the Watch is recording, if anything. Written by the app.
    public static var recordingURL: URL? { container?.appendingPathComponent("recording.json") }
    /// The last switch made on the Watch. Written by the app, read by the
    /// complications beside the phone's glance; the newer of the two wins.
    public static var stateURL: URL? { container?.appendingPathComponent("state.json") }
    /// The phone's latest `WatchGlance`: the state as it knows it, and habits.
    public static var glanceURL: URL? { container?.appendingPathComponent("glance.json") }
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
