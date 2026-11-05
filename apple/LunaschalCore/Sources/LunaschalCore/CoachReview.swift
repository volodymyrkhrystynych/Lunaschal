import Foundation

// The coach's messages in Chat (backend/coach/): the review of yesterday on
// the morning briefing, the evening check-in, and the "how did last week go"
// message when a goal period closes. All three ride on message metadata, so
// they arrive with `chatToday()` and are drawn from the offline cache too.

/// A day's review. The rating is the server's arithmetic, never the model's;
/// `computedRating` differs from `rating` only when the agent moved it a star.
public struct DayReview: Codable, Equatable {
    public struct Item: Codable, Equatable, Identifiable {
        public var kind: String
        public var id: String?
        public var title: String
        /// Nil when it can't be said — a goal the judge was unsure about.
        public var done: Bool?
        public var excused: Bool
        public var excuse: String?

        public var identity: String { "\(kind)-\(id ?? title)" }
        public var status: String {
            excused ? "excused" : done == nil ? "unknown" : done == true ? "done" : "missed"
        }

        enum CodingKeys: String, CodingKey { case kind, id, title, done, excused, excuse }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            kind = try c.decode(String.self, forKey: .kind)
            id = try c.decodeIfPresent(String.self, forKey: .id)
            title = try c.decode(String.self, forKey: .title)
            done = try c.decodeIfPresent(Bool.self, forKey: .done)
            excused = try c.decodeIfPresent(Bool.self, forKey: .excused) ?? false
            excuse = try c.decodeIfPresent(String.self, forKey: .excuse)
        }

        public init(kind: String, id: String? = nil, title: String, done: Bool?, excused: Bool = false, excuse: String? = nil) {
            self.kind = kind; self.id = id; self.title = title; self.done = done; self.excused = excused; self.excuse = excuse
        }
    }

    public var day: String
    public var rating: Int?
    public var computedRating: Int?
    public var overrideReason: String?
    public var done: Int
    public var missed: Int
    public var excused: Int
    public var items: [Item]
    public var summary: String?

    public init(day: String, rating: Int?, computedRating: Int? = nil, overrideReason: String? = nil,
                done: Int, missed: Int, excused: Int, items: [Item], summary: String? = nil) {
        self.day = day; self.rating = rating; self.computedRating = computedRating ?? rating
        self.overrideReason = overrideReason; self.done = done; self.missed = missed
        self.excused = excused; self.items = items; self.summary = summary
    }

    enum CodingKeys: String, CodingKey {
        case day, rating, computedRating, overrideReason, done, missed, excused, items, summary
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        day = try c.decode(String.self, forKey: .day)
        rating = try c.decodeIfPresent(Int.self, forKey: .rating)
        computedRating = try c.decodeIfPresent(Int.self, forKey: .computedRating) ?? rating
        overrideReason = try c.decodeIfPresent(String.self, forKey: .overrideReason)
        done = try c.decodeIfPresent(Int.self, forKey: .done) ?? 0
        missed = try c.decodeIfPresent(Int.self, forKey: .missed) ?? 0
        excused = try c.decodeIfPresent(Int.self, forKey: .excused) ?? 0
        items = try c.decodeIfPresent([Item].self, forKey: .items) ?? []
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
    }

    /// "★★★★☆", or nothing for a day with nothing to rate.
    public var stars: String {
        guard let rating else { return "" }
        return String(repeating: "★", count: rating) + String(repeating: "☆", count: max(0, 5 - rating))
    }

    /// "4 of 5 done · 1 excused".
    public var tally: String {
        let counted = done + missed
        var text = counted > 0 ? "\(done) of \(counted) done" : excused > 0 ? "Excused" : "Nothing tracked"
        if counted > 0 && excused > 0 { text += " · \(excused) excused" }
        return text
    }

    /// The briefing's `metadata.review`.
    public static func parse(metadata: String?) -> DayReview? {
        guard let value = JSONValue.parse(metadata)?["review"], let data = try? JSONEncoder().encode(value) else { return nil }
        return try? JSONDecoder().decode(DayReview.self, from: data)
    }

    /// The composer text "Explain" starts from: the user finishes the sentence,
    /// and the agent's `record_excuse` does the rest.
    public var explainPrompt: String {
        let missed = items.filter { $0.done == false && !$0.excused }.map(\.title)
        guard !missed.isEmpty else { return "About yesterday: " }
        return "About yesterday — \(missed.joined(separator: ", ")): "
    }
}

/// A coach message that isn't a review: `metadata.coach`.
public enum CoachMessage: Equatable {
    /// The evening check-in, with what was still open when it was sent.
    case checkin(day: String, open: [DayReview.Item])
    /// Goal periods that just closed, and the horizons to set goals for now.
    case rollover(day: String, horizons: [GoalHorizon], graded: [GradedGoal])

    public struct GradedGoal: Equatable {
        public var title: String
        public var rating: Int?
        public var periodLabel: String
    }

    public static func parse(metadata: String?) -> CoachMessage? {
        guard let coach = JSONValue.parse(metadata)?["coach"], let kind = coach["kind"]?.string,
              let day = coach["day"]?.string else { return nil }
        switch kind {
        case "checkin":
            let open = (coach["open"]?.array ?? []).compactMap { item -> DayReview.Item? in
                guard let title = item["title"]?.string else { return nil }
                return DayReview.Item(kind: item["kind"]?.string ?? "habit", id: item["id"]?.string, title: title, done: false)
            }
            return .checkin(day: day, open: open)
        case "rollover":
            let horizons = (coach["horizons"]?.array ?? []).compactMap { $0.string.flatMap(GoalHorizon.init(rawValue:)) }
            let graded = (coach["goals"]?.array ?? []).compactMap { goal -> GradedGoal? in
                guard let title = goal["title"]?.string else { return nil }
                return GradedGoal(title: title, rating: goal["rating"]?.int, periodLabel: goal["periodLabel"]?.string ?? "")
            }
            return .rollover(day: day, horizons: horizons, graded: graded)
        default:
            return nil
        }
    }
}

/// `GET /api/coach/reviews`: today so far, and the reviewed days before it.
public struct CoachReviews: Decodable, Equatable {
    public var today: DayReview
    public var days: [DayReview]

    /// Yesterday's review, if it has been written.
    public func review(of day: String) -> DayReview? { days.first { $0.day == day } }
}

/// `GET/PATCH /api/coach/settings`.
public struct CoachSettings: Codable, Equatable {
    public struct Behaviors: Codable, Equatable {
        public var weight: Bool
        public var calories: Bool
        public var habits: Bool
    }
    public var enabled: Bool
    public var checkinEnabled: Bool
    public var checkinHour: Int
    public var quietStart: Int
    public var quietEnd: Int
    public var dailyCap: Int
    public var noSendRate: Double
    public var behaviors: Behaviors
}

/// One row of `GET /api/coach/stats`: what reminders changed for one behaviour.
public struct CoachStat: Codable, Equatable, Identifiable {
    public struct Belief: Codable, Equatable { public var mean: Double; public var n: Double }
    public var behavior: String
    public var label: String
    public var sent: Int
    public var held: Int
    public var skipped: Int
    public var doneWhenSent: Double?
    public var doneWhenHeld: Double?
    public var arms: [String: Belief]

    public var id: String { behavior }

    /// The framing the coach leans toward — "none" means holding back works
    /// as well — or nil while it has nothing to go on.
    public var leading: (arm: String, belief: Belief)? {
        arms.filter { $0.value.n >= 1 }.max { $0.value.mean < $1.value.mean }.map { ($0.key, $0.value) }
    }

    enum CodingKeys: String, CodingKey { case behavior, label, sent, held, skipped, doneWhenSent, doneWhenHeld, arms }
}
