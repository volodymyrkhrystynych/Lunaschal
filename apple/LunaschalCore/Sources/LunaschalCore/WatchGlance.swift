import Foundation

/// What the phone tells the Watch for its complications: the current state
/// and how many habits are left. The Watch has no network of its own, so the
/// phone sends this as WatchConnectivity's application context — only the
/// latest one is kept, which is all a glance needs.
public struct WatchGlance: Codable, Equatable {
    /// The latest switch the phone knows of, from either device or the server.
    public var state: ActivitySwitch?
    /// The 4am day the habit counts were taken on; nil before the phone has
    /// ever fetched its habits.
    public var habitsDay: String?
    public var habitsTotal: Int
    public var habitsLeft: Int

    public init(state: ActivitySwitch? = nil, habitsDay: String? = nil, habitsTotal: Int = 0, habitsLeft: Int = 0) {
        self.state = state; self.habitsDay = habitsDay; self.habitsTotal = habitsTotal; self.habitsLeft = habitsLeft
    }

    /// Counts taken from the habit rows the Home Screen widget shows.
    public static func habits(_ rows: [HabitWidgetRow], day: String, state: ActivitySwitch?) -> WatchGlance {
        WatchGlance(state: state, habitsDay: day, habitsTotal: rows.count, habitsLeft: rows.filter { !$0.done }.count)
    }

    /// Habits left on `day`. Past the 4am the counts were taken before, every
    /// habit is open again, so the complication rolls over on its own without
    /// waiting for the phone. Nil when the phone has never said.
    public func left(on day: String) -> Int? {
        guard let habitsDay else { return nil }
        return habitsDay == day ? habitsLeft : (day > habitsDay ? habitsTotal : habitsLeft)
    }

    // MARK: WatchConnectivity and the App Group file

    static let key = "glance"

    /// The application-context dictionary: the glance as JSON under one key,
    /// so a field added later doesn't need a property-list mapping.
    public var context: [String: Any] {
        guard let data = try? JSONEncoder().encode(self) else { return [:] }
        return [Self.key: data]
    }

    public init?(context: [String: Any]) {
        guard let data = context[Self.key] as? Data,
              let glance = try? JSONDecoder().decode(WatchGlance.self, from: data) else { return nil }
        self = glance
    }

    public static func load(from url: URL) -> WatchGlance? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(WatchGlance.self, from: data)
    }

    public func save(to url: URL) throws {
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }
}
