import Foundation

// Goals on the Today tab: one overarching goal, and any number for this week,
// month, quarter and year (backend/goals/). Every change leaves a journal
// entry on the server, which is the goal's history; the phone only queues the
// change through the to-do outbox and shows it at once.

public enum GoalHorizon: String, Codable, CaseIterable, Identifiable {
    case week, month, quarter, year, overarching

    public var id: String { rawValue }

    /// The four with periods, in the order the tab lists them.
    public static let periods: [GoalHorizon] = [.week, .month, .quarter, .year]

    /// "This week".
    public var heading: String { self == .overarching ? "Overarching goal" : "This \(rawValue)" }
}

public struct Goal: Codable, Equatable, Identifiable {
    public let id: String
    public var horizon: GoalHorizon
    public var title: String
    public var details: String?
    /// A daily rule and its prize are optional; plain goals have neither.
    public var criteria: String?
    public var reward: String?
    public var hasRule: Bool
    public var targetDays: Int?
    public var periodStart: String?
    public var periodEnd: String?
    public var periodLabel: String
    public var overdue: Bool
    public var status: String
    public var currentStreak: Int
    public var bestStreak: Int
    public var endedAt: String?

    public init(id: String, horizon: GoalHorizon, title: String, details: String? = nil,
                criteria: String? = nil, reward: String? = nil, targetDays: Int? = nil,
                periodStart: String? = nil, periodEnd: String? = nil, periodLabel: String = "",
                overdue: Bool = false, status: String = "active", currentStreak: Int = 0,
                bestStreak: Int = 0, endedAt: String? = nil) {
        self.id = id; self.horizon = horizon; self.title = title; self.details = details
        self.criteria = criteria; self.reward = reward; self.hasRule = criteria != nil
        self.targetDays = targetDays; self.periodStart = periodStart; self.periodEnd = periodEnd
        self.periodLabel = periodLabel; self.overdue = overdue; self.status = status
        self.currentStreak = currentStreak; self.bestStreak = bestStreak; self.endedAt = endedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, horizon, title, details, criteria, reward, hasRule, targetDays, periodStart, periodEnd
        case periodLabel, overdue, status, currentStreak, bestStreak, endedAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        horizon = try c.decode(GoalHorizon.self, forKey: .horizon)
        title = try c.decode(String.self, forKey: .title)
        details = try c.decodeIfPresent(String.self, forKey: .details)
        criteria = try c.decodeIfPresent(String.self, forKey: .criteria)
        reward = try c.decodeIfPresent(String.self, forKey: .reward)
        hasRule = try c.decodeIfPresent(Bool.self, forKey: .hasRule) ?? (criteria != nil)
        targetDays = try c.decodeIfPresent(Int.self, forKey: .targetDays)
        periodStart = try c.decodeIfPresent(String.self, forKey: .periodStart)
        periodEnd = try c.decodeIfPresent(String.self, forKey: .periodEnd)
        periodLabel = try c.decodeIfPresent(String.self, forKey: .periodLabel) ?? ""
        overdue = try c.decodeIfPresent(Bool.self, forKey: .overdue) ?? false
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "active"
        currentStreak = try c.decodeIfPresent(Int.self, forKey: .currentStreak) ?? 0
        bestStreak = try c.decodeIfPresent(Int.self, forKey: .bestStreak) ?? 0
        endedAt = try c.decodeIfPresent(String.self, forKey: .endedAt)
    }

    /// The overarching goal's whole text; a period goal's title.
    public var text: String { horizon == .overarching ? (details ?? title) : title }

    /// "3/7 days in a row", for a goal with a daily rule.
    public var streakLine: String? {
        guard hasRule, let targetDays else { return nil }
        return "\(currentStreak)/\(targetDays) days in a row"
    }
}

/// One horizon's period and the goals open in it.
public struct GoalPeriod: Codable, Equatable, Identifiable {
    public var horizon: GoalHorizon
    public var label: String
    public var periodStart: String
    public var periodEnd: String
    public var goals: [Goal]
    /// Nothing open for the period today is in: the prompt to set one.
    public var unset: Bool

    public var id: String { horizon.rawValue }

    public init(horizon: GoalHorizon, label: String, periodStart: String, periodEnd: String,
                goals: [Goal] = [], unset: Bool? = nil) {
        self.horizon = horizon; self.label = label; self.periodStart = periodStart
        self.periodEnd = periodEnd; self.goals = goals
        self.unset = unset ?? goals.isEmpty
    }

    /// Recomputes `unset` the way the server does: is any open goal's period
    /// still running on `today`?
    mutating func settle(today: String) {
        unset = !goals.contains { ($0.periodEnd ?? periodEnd) >= today }
    }
}

/// `GET /api/goals/current`.
public struct CurrentGoals: Codable, Equatable {
    public var today: String
    public var overarching: Goal?
    public var horizons: [GoalPeriod]

    public init(today: String, overarching: Goal? = nil, horizons: [GoalPeriod] = []) {
        self.today = today; self.overarching = overarching; self.horizons = horizons
    }

    public func period(_ horizon: GoalHorizon) -> GoalPeriod? { horizons.first { $0.horizon == horizon } }

    /// The horizons with nothing set, for the "set this week's goals" prompt.
    public var unsetHorizons: [GoalHorizon] { horizons.filter(\.unset).map(\.horizon) }

    mutating func apply(_ change: TodoChange) {
        switch change {
        case let .createGoal(id, horizon, title, details):
            if horizon == .overarching {
                setOverarching(id: id, text: details ?? title)
                return
            }
            guard let index = horizons.firstIndex(where: { $0.horizon == horizon }),
                  !horizons.contains(where: { $0.goals.contains { $0.id == id } }) else { return }
            let period = horizons[index]
            horizons[index].goals.append(Goal(id: id, horizon: horizon, title: title, details: details,
                                              periodStart: period.periodStart, periodEnd: period.periodEnd,
                                              periodLabel: period.label))
            horizons[index].settle(today: today)
        case let .editGoal(id, title, details):
            for p in horizons.indices {
                for g in horizons[p].goals.indices where horizons[p].goals[g].id == id {
                    horizons[p].goals[g].title = title
                    horizons[p].goals[g].details = details
                }
            }
        case let .setOverarching(id, text):
            setOverarching(id: id, text: text)
        case let .setGoalStatus(id, _):
            // Ended, dropped or redeemed: in every case it leaves the open list.
            for p in horizons.indices where horizons[p].goals.contains(where: { $0.id == id }) {
                horizons[p].goals.removeAll { $0.id == id }
                horizons[p].settle(today: today)
            }
        default:
            return
        }
    }

    private mutating func setOverarching(id: String, text: String) {
        guard overarching?.id != id, overarching?.text != text else { return }
        let firstLine = text.split(separator: "\n", omittingEmptySubsequences: false).first.map(String.init) ?? text
        overarching = Goal(id: id, horizon: .overarching, title: String(firstLine.prefix(200)), details: text,
                           periodLabel: "Overarching")
    }
}
