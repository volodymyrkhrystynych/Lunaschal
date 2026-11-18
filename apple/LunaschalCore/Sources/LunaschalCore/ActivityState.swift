import Foundation

/// Work, leisure, or both at once: what the user says they are doing, switched
/// on the Watch or from the phone's Capture tab. A switch is an instant and
/// the state lasts until the next one; the server derives the day's spans
/// (`backend/activity_state.py`), so nothing here keeps a timeline.
public enum ActivityState: String, Codable, CaseIterable, Identifiable {
    case work, leisure, mix

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .work: return "Work"
        case .leisure: return "Leisure"
        case .mix: return "Mix"
        }
    }

    /// Mix shares work's briefcase and is told apart by its two-colour fill
    /// (and, where a face drops colour, by a half-filled disc behind it).
    public var symbol: String {
        switch self {
        case .work, .mix: return "briefcase.fill"
        case .leisure: return "cup.and.saucer.fill"
        }
    }

    /// The calendar categories it is drawn in: work and leisure their own,
    /// mix both, left half work.
    public var colors: [UInt32] {
        let work = CalendarCategory.colors["work"]!, leisure = CalendarCategory.colors["leisure"]!
        switch self {
        case .work: return [work]
        case .leisure: return [leisure]
        case .mix: return [work, leisure]
        }
    }
}

/// One switch, under the id the device that made it minted, so a replay from
/// any outbox is a no-op on the server. `at` is unix seconds, as the server
/// stores it, so the same JSON serves the outbox, WatchConnectivity and the
/// upload.
public struct ActivitySwitch: Codable, Equatable, Identifiable {
    public enum Source: String, Codable { case watch, phone }

    public let id: String
    public let state: ActivityState
    public let at: Int
    public let source: Source

    public init(id: String = ULID.make(), state: ActivityState, at: Int, source: Source) {
        self.id = id; self.state = state; self.at = at; self.source = source
    }

    public init(state: ActivityState, now: Date = Date(), source: Source) {
        self.init(id: ULID.make(now: now), state: state, at: Int(now.timeIntervalSince1970), source: source)
    }

    /// The newest of any number of candidates — this device's last switch,
    /// another device's, the server's — by when it was made. The Watch's
    /// queue can deliver an older switch after a newer one, so arrival order
    /// means nothing.
    public static func latest(_ candidates: [ActivitySwitch?]) -> ActivitySwitch? {
        candidates.compactMap { $0 }.max { ($0.at, $0.id) < ($1.at, $1.id) }
    }

    public static func load(from url: URL) -> ActivitySwitch? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(ActivitySwitch.self, from: data)
    }

    public func save(to url: URL) throws {
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }
}

/// Switches waiting to move on, one JSON file each: on the Watch until the
/// phone says it has them, on the phone until the server does.
public final class ActivityStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [ActivitySwitch] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(ActivitySwitch.self, from: Data(contentsOf: $0)) }
            .sorted { ($0.at, $0.id) < ($1.at, $1.id) }
    }

    public func save(_ change: ActivitySwitch) throws {
        guard ULID.isValid(change.id) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(change).write(to: manifest(change.id), options: .atomic)
    }

    public func remove(_ id: String) throws {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        let url = manifest(id)
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }
}

public protocol ActivityTransport {
    func sendActivitySwitch(_ change: ActivitySwitch) async throws
}

/// Uploads the phone's switch outbox. A switch the server refuses can never
/// succeed, so it is dropped and counted; anything else stops the pass.
@MainActor
public final class ActivitySync {
    private let store: ActivityStore

    public init(store: ActivityStore) { self.store = store }

    /// How many switches the server refused.
    @discardableResult
    public func run(using transport: ActivityTransport) async throws -> Int {
        var refused = 0
        for change in try store.list() {
            try Task.checkCancellation()
            do {
                try await transport.sendActivitySwitch(change)
            } catch let http as HTTPFailure where ![401, 403].contains(http.status) && !http.retryAutomatically {
                refused += 1
            }
            try store.remove(change.id)
        }
        return refused
    }
}

extension JournalAPI: ActivityTransport {
    public func sendActivitySwitch(_ change: ActivitySwitch) async throws {
        guard ULID.isValid(change.id) else { throw CaptureError.invalidID }
        let data = try await postJSON("api/activity-state/switches", change)
        try Self.validateActivityAcknowledgement(data, for: change)
    }

    /// The reply is the stored row, under the id the device minted.
    public static func validateActivityAcknowledgement(_ data: Data, for change: ActivitySwitch) throws {
        struct Reply: Decodable { let id: String }
        guard try JSONDecoder().decode(Reply.self, from: data).id == change.id else { throw CaptureError.invalidResponse }
    }

    /// The latest switch the server has, from any device; nil before the first.
    public func currentActivity() async throws -> ActivitySwitch? {
        let (data, response) = try await session.data(for: request("api/activity-state/current"))
        try check(data, response)
        return try JSONDecoder().decode(ActivitySwitch?.self, from: data)
    }

    /// A day's work / leisure / mix spans for the calendar's activity rail,
    /// already folded with reading by the server.
    public func activity(day: String) async throws -> [CalendarActivitySpan] {
        let (data, response) = try await session.data(for: request("api/calendar/activity/\(try Self.calendarPath(day))"))
        try check(data, response)
        return try JSONDecoder().decode([CalendarActivitySpan].self, from: data)
    }
}
