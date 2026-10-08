import Foundation

/// The Watch's two timers: work in 25-minute blocks with a 5-minute break
/// between them, and a single 10-minute timeout.
public enum PomodoroKind: String, Codable, CaseIterable {
    case work, `break`, timeout

    public var minutes: Int {
        switch self {
        case .work: return 25
        case .break: return 5
        case .timeout: return 10
        }
    }

    /// What Continue starts once this one is up: work again after work or a
    /// break, another timeout after a timeout.
    public var next: PomodoroKind { self == .timeout ? .timeout : .work }

    /// SF Symbol, shared by the timer screen and the complications.
    public var symbol: String {
        switch self {
        case .work: return "brain.head.profile"
        case .break: return "figure.walk"
        case .timeout: return "cup.and.saucer"
        }
    }

    public var label: String {
        switch self {
        case .work: return "Focus"
        case .break: return "Break"
        case .timeout: return "Timeout"
        }
    }
}

/// One run of a timer, finished or cancelled: what gets logged to Lifestyle.
public struct PomodoroSession: Codable, Equatable, Identifiable {
    public let id: String
    public let kind: PomodoroKind
    public let startedAt: Date
    public let endedAt: Date
    public let plannedSeconds: Int
    /// False when it was cancelled before the time was up.
    public let completed: Bool

    public init(id: String, kind: PomodoroKind, startedAt: Date, endedAt: Date, plannedSeconds: Int, completed: Bool) {
        self.id = id; self.kind = kind; self.startedAt = startedAt; self.endedAt = endedAt
        self.plannedSeconds = plannedSeconds; self.completed = completed
    }
}

/// The timer itself, as a value so every transition can be tested without a
/// clock. Each transition returns the sessions it closed, for the caller to log.
/// It is persisted whole, so a relaunch, or a launch from a notification's
/// button, picks the run back up from `endsAt` rather than from memory.
public struct PomodoroTimer: Codable, Equatable {
    public struct Run: Codable, Equatable {
        public let id: String
        public let kind: PomodoroKind
        public let startedAt: Date
        public let endsAt: Date
    }

    public enum State: Codable, Equatable {
        case idle
        case running(Run)
        /// Time is up; waiting for Continue, Break or Cancel.
        case finished(PomodoroKind)
    }

    public enum Choice: Equatable { case `continue`, takeBreak }

    /// A cancel this soon after starting was a mis-tap, not a session.
    public static let minimumLogged: TimeInterval = 60

    public private(set) var state: State = .idle
    /// Overrides every length, so the simulator can be checked without
    /// waiting 25 minutes. Never set in a release build.
    public var shortenedTo: TimeInterval?

    public init(shortenedTo: TimeInterval? = nil) { self.shortenedTo = shortenedTo }

    public func length(_ kind: PomodoroKind) -> TimeInterval {
        shortenedTo ?? TimeInterval(kind.minutes * 60)
    }

    public var run: Run? {
        if case .running(let run) = state { return run }
        return nil
    }

    /// What the end-of-timer screen offers besides Cancel. Break is only for
    /// the work timer.
    public var choices: [Choice] {
        guard case .finished(let kind) = state else { return [] }
        return kind == .work ? [.continue, .takeBreak] : [.continue]
    }

    @discardableResult
    public mutating func start(_ kind: PomodoroKind, now: Date = Date()) -> [PomodoroSession] {
        let closed = cancel(now: now)
        state = .running(Run(id: ULID.make(now: now), kind: kind, startedAt: now, endsAt: now + length(kind)))
        return closed
    }

    /// Moves a run whose time is up to the choice screen.
    @discardableResult
    public mutating func expire(now: Date = Date()) -> [PomodoroSession] {
        guard let run, now >= run.endsAt else { return [] }
        state = .finished(run.kind)
        return [session(run, endedAt: run.endsAt, completed: true)]
    }

    /// Starts what follows the finished timer. Works on a run that is still
    /// nominally running too, since a notification button can arrive before
    /// the app has noticed the time is up.
    @discardableResult
    public mutating func `continue`(now: Date = Date()) -> [PomodoroSession] {
        let closed = expire(now: now)
        guard case .finished(let kind) = state else { return closed }
        return closed + start(kind.next, now: now)
    }

    @discardableResult
    public mutating func takeBreak(now: Date = Date()) -> [PomodoroSession] {
        let closed = expire(now: now)
        guard case .finished(.work) = state else { return closed }
        return closed + start(.break, now: now)
    }

    /// Stops whatever is happening. A run cut short is logged as incomplete,
    /// unless it was too short to have been meant.
    @discardableResult
    public mutating func cancel(now: Date = Date()) -> [PomodoroSession] {
        let closed = expire(now: now)
        if !closed.isEmpty { state = .idle; return closed }
        defer { state = .idle }
        guard let run, now.timeIntervalSince(run.startedAt) >= Self.minimumLogged else { return [] }
        return [session(run, endedAt: now, completed: false)]
    }

    private func session(_ run: Run, endedAt: Date, completed: Bool) -> PomodoroSession {
        PomodoroSession(id: run.id, kind: run.kind, startedAt: run.startedAt, endedAt: endedAt,
                        plannedSeconds: Int(run.endsAt.timeIntervalSince(run.startedAt)), completed: completed)
    }

    /// What pressing a Focus or Timeout button did.
    public enum Press: Equatable {
        /// Nothing was going, so `kind` started.
        case started
        /// A run is going, or one has ended and is waiting for a choice: the
        /// press opens the timer instead. A button on the face or in Control
        /// Center is easy to brush, so a press never replaces a run.
        case alreadyGoing
    }

    @discardableResult
    public mutating func press(_ kind: PomodoroKind, now: Date = Date()) -> (Press, [PomodoroSession]) {
        let closed = expire(now: now)
        guard state == .idle else { return (.alreadyGoing, closed) }
        return (.started, closed + start(kind, now: now))
    }

    /// One state a complication shows, from `date` on.
    public struct Glance: Equatable {
        public let date: Date
        public let state: State
    }

    /// What a complication shows from `now` on. The Watch app is suspended
    /// when a run ends, so the timeline has to carry the end itself rather
    /// than wait for the app to say so.
    public func glances(now: Date = Date()) -> [Glance] {
        guard let run else { return [Glance(date: now, state: state)] }
        guard now < run.endsAt else { return [Glance(date: now, state: .finished(run.kind))] }
        return [Glance(date: now, state: state), Glance(date: run.endsAt, state: .finished(run.kind))]
    }

    public static func load(from url: URL) -> PomodoroTimer? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(PomodoroTimer.self, from: data)
    }

    public func save(to url: URL) throws {
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }
}

/// Sessions waiting to move on, one JSON file each: on the Watch until the
/// phone says it has them, on the phone until the server does.
public final class PomodoroStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [PomodoroSession] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(PomodoroSession.self, from: Data(contentsOf: $0)) }
            .sorted { $0.startedAt < $1.startedAt }
    }

    public func save(_ session: PomodoroSession) throws {
        guard ULID.isValid(session.id) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(session).write(to: manifest(session.id), options: .atomic)
    }

    public func remove(_ id: String) throws {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        let url = manifest(id)
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }
}

public protocol PomodoroTransport {
    func sendPomodoro(_ session: PomodoroSession) async throws
}

/// Uploads the phone's pomodoro outbox. A session the server refuses can never
/// succeed, so it is dropped and counted; anything else stops the pass.
@MainActor
public final class PomodoroSync {
    private let store: PomodoroStore

    public init(store: PomodoroStore) { self.store = store }

    /// How many sessions the server refused.
    @discardableResult
    public func run(using transport: PomodoroTransport) async throws -> Int {
        var refused = 0
        for session in try store.list() {
            try Task.checkCancellation()
            do {
                try await transport.sendPomodoro(session)
            } catch let http as HTTPFailure where ![401, 403].contains(http.status) && !http.retryAutomatically {
                refused += 1
            }
            try store.remove(session.id)
        }
        return refused
    }
}

extension JournalAPI: PomodoroTransport {
    public func sendPomodoro(_ session: PomodoroSession) async throws {
        guard ULID.isValid(session.id) else { throw CaptureError.invalidID }
        struct Body: Encodable {
            let id: String; let kind: String; let startedAt: Int; let endedAt: Int
            let plannedSeconds: Int; let completed: Bool
        }
        let data = try await postJSON("api/lifestyle/pomodoro/sessions", Body(
            id: session.id, kind: session.kind.rawValue,
            startedAt: Int(session.startedAt.timeIntervalSince1970), endedAt: Int(session.endedAt.timeIntervalSince1970),
            plannedSeconds: session.plannedSeconds, completed: session.completed))
        try Self.validatePomodoroAcknowledgement(data, for: session)
    }

    /// The reply is the stored row, under the id the Watch minted.
    public static func validatePomodoroAcknowledgement(_ data: Data, for session: PomodoroSession) throws {
        struct Reply: Decodable { let id: String }
        guard try JSONDecoder().decode(Reply.self, from: data).id == session.id else { throw CaptureError.invalidResponse }
    }
}
