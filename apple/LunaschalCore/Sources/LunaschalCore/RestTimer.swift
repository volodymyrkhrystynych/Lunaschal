import Foundation

/// The Watch's Workout timer: 90 seconds of rest between sets, then a buzz
/// that keeps going until it is stopped or restarted. Apart from the
/// pomodoro on purpose: it is never logged, and a rest between sets must not
/// be refused because a focus run is going, or replace one.
public struct RestTimer: Codable, Equatable {
    public static let seconds: TimeInterval = 90

    public enum State: Codable, Equatable {
        case idle
        case running(startedAt: Date, endsAt: Date)
        /// Time is up and the Watch is buzzing until Stop or Restart.
        case ringing(since: Date)
    }

    public private(set) var state: State = .idle
    /// Overrides the length, so the simulator can be checked without
    /// waiting. Never set in a release build.
    public var shortenedTo: TimeInterval?

    public init(shortenedTo: TimeInterval? = nil) { self.shortenedTo = shortenedTo }

    public var length: TimeInterval { shortenedTo ?? Self.seconds }

    public var endsAt: Date? {
        if case .running(_, let endsAt) = state { return endsAt }
        return nil
    }

    public var isRinging: Bool {
        if case .ringing = state { return true }
        return false
    }

    /// Starts a fresh 90 seconds from any state: from idle it is Start, from
    /// running or ringing it is Restart.
    public mutating func start(now: Date = Date()) {
        state = .running(startedAt: now, endsAt: now + length)
    }

    public mutating func stop() { state = .idle }

    /// Moves a run whose time is up to ringing. Ringing starts at the run's
    /// end, not when this noticed it.
    public mutating func expire(now: Date = Date()) {
        guard let endsAt, now >= endsAt else { return }
        state = .ringing(since: endsAt)
    }

    /// One state a complication shows, from `date` on.
    public struct Glance: Equatable {
        public let date: Date
        public let state: State
    }

    /// What a complication shows from `now` on, carrying the end itself since
    /// the app is suspended when it comes.
    public func glances(now: Date = Date()) -> [Glance] {
        guard let endsAt else { return [Glance(date: now, state: state)] }
        guard now < endsAt else { return [Glance(date: now, state: .ringing(since: endsAt))] }
        return [Glance(date: now, state: state), Glance(date: endsAt, state: .ringing(since: endsAt))]
    }

    public static func load(from url: URL) -> RestTimer? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(RestTimer.self, from: data)
    }

    public func save(to url: URL) throws {
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }
}
