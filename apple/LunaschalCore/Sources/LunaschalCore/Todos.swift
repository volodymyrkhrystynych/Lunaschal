import Foundation

// The Todo tab on the phone: the desktop Lifestyle tab's daily tasks and
// to-do lists (src/components/Tasks/), over the same /api/tasks routes.

/// A habit: one of up to ten things done every day; `done` is today's
/// completion. `history` is the ticked 4am days of the last twelve weeks,
/// which is what the streak, the week dots and the widget are drawn from, so
/// they stay right after the 4am rollover before the server is asked again.
public struct DailyTask: Codable, Equatable, Identifiable {
    public let id: String
    public var title: String
    public var position: Int
    public var done: Bool
    /// Oldest first, as the server sends it.
    public var history: [String]
    /// The server's counts when it was last asked. `streak(today:)` is what
    /// to show; these only cover what `history`'s window cannot.
    public var serverStreak: Int
    public var longestStreak: Int
    /// Days the user told the agent were out of their hands: they neither
    /// count toward a streak nor break it.
    public var excused: [String]
    /// The if-then half of the habit: "after coffee" for "weigh in".
    public var cue: String?

    /// Mirrors MAX_TASKS in backend/routes/tasks.py.
    public static let limit = 10

    public init(id: String, title: String, position: Int, done: Bool, history: [String] = [],
                serverStreak: Int = 0, longestStreak: Int = 0, excused: [String] = [], cue: String? = nil) {
        self.id = id; self.title = title; self.position = position; self.done = done
        self.history = history; self.serverStreak = serverStreak; self.longestStreak = longestStreak
        self.excused = excused; self.cue = cue
    }

    enum CodingKeys: String, CodingKey {
        case id, title, position, done, history, longestStreak, excused, cue
        case serverStreak = "streak"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        position = try c.decode(Int.self, forKey: .position)
        // The route's CASE gives 0/1, not a boolean.
        if let flag = try? c.decode(Bool.self, forKey: .done) { done = flag }
        else { done = try c.decode(Int.self, forKey: .done) != 0 }
        // Absent from a server, or a cache, from before streaks.
        history = try c.decodeIfPresent([String].self, forKey: .history) ?? []
        serverStreak = try c.decodeIfPresent(Int.self, forKey: .serverStreak) ?? 0
        longestStreak = try c.decodeIfPresent(Int.self, forKey: .longestStreak) ?? 0
        excused = try c.decodeIfPresent([String].self, forKey: .excused) ?? []
        cue = try c.decodeIfPresent(String.self, forKey: .cue)
    }

    /// Whether it was ticked on a 4am day.
    public func isDone(on day: String) -> Bool { history.contains(day) }

    /// The running streak as of `today`, alive through an unticked today.
    public func streak(today: String) -> Int {
        let local = HabitStreaks.current(history, today: today, excused: excused)
        // A run reaching the start of the window may go back further than
        // the window shows; the server counted all of it.
        return local >= HabitStreaks.historyDays - 1 ? max(local, serverStreak) : local
    }

    public func best(today: String) -> Int { max(longestStreak, streak(today: today)) }

    /// Yesterday was missed, so today can't be: the run's grace day is spent.
    public func atRisk(today: String) -> Bool { HabitStreaks.atRisk(history, today: today, excused: excused) }

    /// Whether a past day was excused, for the week dots.
    public func isExcused(on day: String) -> Bool { excused.contains(day) }

    /// The last `count` days ending today, oldest first.
    public func week(today: String, count: Int = 7) -> [Bool] {
        HabitStreaks.days(endingAt: today, count: count).map(isDone(on:))
    }

    /// Records a tick or untick for a day, as the server will.
    mutating func set(_ day: String, done flag: Bool, today: String) {
        history.removeAll { $0 == day }
        if flag { history.append(day); history.sort() }
        if day == today { done = flag }
    }
}

/// `backend/habit_streaks.py`, for streaks that move the moment a tick is
/// queued rather than when the server is next asked. Both run the cases in
/// `backend/tests/fixtures/habit_streaks.json`. Never miss twice: one missed
/// day is grace, two in a row end the run; an excused day neither counts
/// nor breaks it.
public enum HabitStreaks {
    /// The server's history window, in days.
    public static let historyDays = 84
    /// Missed days in a row a run survives.
    public static let grace = 1

    private static var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    static func date(_ key: String) -> Date? {
        let parts = key.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return utc.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    static func key(_ date: Date) -> String {
        let parts = utc.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// A day key `offset` days from `day`.
    public static func adding(_ offset: Int, to day: String) -> String {
        guard let date = date(day), let moved = utc.date(byAdding: .day, value: offset, to: date) else { return day }
        return key(moved)
    }

    /// `count` day keys ending at `day`, oldest first.
    public static func days(endingAt day: String, count: Int) -> [String] {
        (0..<max(count, 0)).map { adding($0 - (count - 1), to: day) }
    }

    /// Done days in the run reaching today, or yesterday while today is open.
    public static func current(_ done: [String], today: String, excused: [String] = []) -> Int {
        let days = Set(done.filter { $0 <= today })
        guard let floor = days.min() else { return 0 }
        let off = Set(excused)
        var day = days.contains(today) ? today : adding(-1, to: today)
        var count = 0, misses = 0
        while day >= floor {
            if days.contains(day) {
                count += 1; misses = 0
            } else if !off.contains(day) {
                misses += 1
                if misses > grace { break }
            }
            day = adding(-1, to: day)
        }
        return count
    }

    /// A live run whose grace day is spent.
    public static func atRisk(_ done: [String], today: String, excused: [String] = []) -> Bool {
        let yesterday = adding(-1, to: today)
        if done.contains(today) || done.contains(yesterday) || excused.contains(yesterday) { return false }
        return current(done, today: today, excused: excused) > 0
    }

    public static func longest(_ done: [String], excused: [String] = []) -> Int {
        let days = Set(done), off = Set(excused)
        guard var day = days.min(), let last = days.max() else { return 0 }
        var best = 0, run = 0, misses = 0
        while day <= last {
            if days.contains(day) {
                run += 1; misses = 0; best = max(best, run)
            } else if !off.contains(day) {
                misses += 1
                if misses > grace { run = 0 }
            }
            day = adding(1, to: day)
        }
        return best
    }
}

/// A one-off to-do on the To-Do or Archive list. A repeating one is never
/// done: completing it moves `due` to the next occurrence.
public struct TodoItem: Codable, Equatable, Identifiable {
    public let id: String
    public var title: String
    public var done: Bool
    public var completedAt: String?
    public var list: String
    public var notes: String?
    /// ISO time, local noon of the chosen day.
    public var due: String?
    public var repeatInterval: Int?
    public var repeatUnit: String?
    public var priority: Int?
    public var createdAt: String?

    public init(id: String, title: String, done: Bool = false, completedAt: String? = nil, list: String = "todo",
                notes: String? = nil, due: String? = nil, repeatInterval: Int? = nil, repeatUnit: String? = nil,
                priority: Int? = 3, createdAt: String? = nil) {
        self.id = id; self.title = title; self.done = done; self.completedAt = completedAt; self.list = list
        self.notes = notes; self.due = due; self.repeatInterval = repeatInterval; self.repeatUnit = repeatUnit
        self.priority = priority; self.createdAt = createdAt
    }

    public var isArchived: Bool { list == "archive" }
    public var dueDate: Date? { due.flatMap(TodoRules.date(iso:)) }
}

/// Everything the to-do form sets. Encoded whole, with nulls, so the same
/// body creates a to-do and, sent as a PATCH, clears a due date or a repeat.
/// The outbox stores it in that same form.
public struct TodoDraft: Codable, Equatable {
    public var id: String?
    public var title: String
    public var notes: String
    public var due: Date?
    public var repeatInterval: Int?
    public var repeatUnit: String
    public var priority: Int
    public var list: String

    public static let units = ["day", "week", "month"]

    public init(id: String? = nil, title: String = "", notes: String = "", due: Date? = nil,
                repeatInterval: Int? = nil, repeatUnit: String = "week", priority: Int = 3, list: String = "todo") {
        self.id = id; self.title = title; self.notes = notes; self.due = due; self.repeatInterval = repeatInterval
        self.repeatUnit = repeatUnit; self.priority = priority; self.list = list
    }

    public init(_ todo: TodoItem) {
        self.init(title: todo.title, notes: todo.notes ?? "", due: todo.dueDate, repeatInterval: todo.repeatInterval,
                  repeatUnit: todo.repeatUnit ?? "week", priority: todo.priority ?? 3, list: todo.isArchived ? "archive" : "todo")
    }

    var trimmedTitle: String { title.trimmingCharacters(in: .whitespacesAndNewlines) }

    enum CodingKeys: String, CodingKey { case id, title, notes, due, repeatInterval, repeatUnit, priority, list }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(id: try c.decodeIfPresent(String.self, forKey: .id),
                  title: try c.decode(String.self, forKey: .title),
                  notes: try c.decodeIfPresent(String.self, forKey: .notes) ?? "",
                  due: try c.decodeIfPresent(Int.self, forKey: .due).map { Date(timeIntervalSince1970: TimeInterval($0)) },
                  repeatInterval: try c.decodeIfPresent(Int.self, forKey: .repeatInterval),
                  repeatUnit: try c.decodeIfPresent(String.self, forKey: .repeatUnit) ?? "week",
                  priority: try c.decodeIfPresent(Int.self, forKey: .priority) ?? 3,
                  list: try c.decodeIfPresent(String.self, forKey: .list) ?? "todo")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(id, forKey: .id)
        try c.encode(trimmedTitle, forKey: .title)
        let notes = notes.trimmingCharacters(in: .whitespacesAndNewlines)
        if notes.isEmpty { try c.encodeNil(forKey: .notes) } else { try c.encode(notes, forKey: .notes) }
        if let due { try c.encode(TodoPromotion.dueSeconds(due), forKey: .due) } else { try c.encodeNil(forKey: .due) }
        // The server wants both or neither.
        if let repeatInterval, repeatInterval >= 1 {
            try c.encode(repeatInterval, forKey: .repeatInterval)
            try c.encode(repeatUnit, forKey: .repeatUnit)
        } else {
            try c.encodeNil(forKey: .repeatInterval)
            try c.encodeNil(forKey: .repeatUnit)
        }
        try c.encode(priority, forKey: .priority)
        try c.encode(list, forKey: .list)
    }
}

/// `src/lib/todos.ts`: what the list shows, in what order, and what counts as due.
public enum TodoRules {
    static let daysPerUnit = ["day": 1, "week": 7, "month": 30]

    public static func date(iso: String) -> Date? {
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: iso) { return date }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: iso)
    }

    /// How the server writes a time.
    public static func iso(_ date: Date) -> String { ISO8601DateFormatter().string(from: date) }

    /// `next_due` in backend/todo_recurrence.py: the next occurrence after
    /// completing a repeating to-do, anchored on its due date but always
    /// after `now`. Months clamp the day and are counted in UTC, as there.
    public static func nextDue(_ due: Date?, interval: Int, unit: String, now: Date) -> Date {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC")!
        func step(_ date: Date) -> Date {
            switch unit {
            case "day": return date.addingTimeInterval(Double(interval) * 86400)
            case "week": return date.addingTimeInterval(Double(interval) * 7 * 86400)
            default: return utc.date(byAdding: .month, value: interval, to: date) ?? date.addingTimeInterval(Double(interval) * 30 * 86400)
            }
        }
        var candidate = step(due ?? now)
        while candidate <= now { candidate = step(candidate) }
        return candidate
    }

    /// Local midnight of `now`'s 4am day.
    static func rolledToday(_ now: Date, calendar: Calendar) -> Date {
        calendar.startOfDay(for: now.addingTimeInterval(-Double(DayKey.rolloverHour) * 3600))
    }

    /// Whole days from today (4am day) to the due date's calendar day; negative when overdue.
    public static func daysUntilDue(_ todo: TodoItem, now: Date = Date(), calendar: Calendar = .current) -> Int? {
        guard let due = todo.dueDate else { return nil }
        return calendar.dateComponents([.day], from: rolledToday(now, calendar: calendar),
                                       to: calendar.startOfDay(for: due)).day
    }

    /// A repeating to-do stays out of the list until it's within a tenth of
    /// its interval (at least a day) of being due.
    public static func isFarOffPeriodic(_ todo: TodoItem, now: Date = Date(), calendar: Calendar = .current) -> Bool {
        guard let interval = todo.repeatInterval, interval > 0, let unit = todo.repeatUnit,
              let days = daysUntilDue(todo, now: now, calendar: calendar) else { return false }
        let threshold = max(1, Int((Double(interval * (daysPerUnit[unit] ?? 1)) * 0.1).rounded(.up)))
        return days > threshold
    }

    /// The open to-dos the list shows: dated first, soonest on top, then by
    /// priority; undated ones keep their creation order.
    public static func active(_ todos: [TodoItem], now: Date = Date(), calendar: Calendar = .current) -> [TodoItem] {
        let open = todos.filter { !$0.done && !isFarOffPeriodic($0, now: now, calendar: calendar) }
        return open.enumerated().sorted { lhs, rhs in
            let (a, b) = (lhs.element, rhs.element)
            switch (a.dueDate, b.dueDate) {
            case let (x?, y?) where x != y: return x < y
            case (.some, nil): return true
            case (nil, .some): return false
            default:
                let (pa, pb) = (a.priority ?? 3, b.priority ?? 3)
                return pa != pb ? pa > pb : lhs.offset < rhs.offset
            }
        }.map(\.element)
    }

    /// Open to-dos on a list; an unknown list name lands on To-Do, as `groupTodosByList` has it.
    public static func on(_ list: String, _ todos: [TodoItem]) -> [TodoItem] {
        todos.filter { list == "archive" ? $0.isArchived : !$0.isArchived }
    }

    /// The tab's badge: open to-dos on the To-Do list due today or before.
    /// Archived ones are set aside on purpose, and daily tasks are due every day.
    public static func dueCount(_ todos: [TodoItem], now: Date = Date(), calendar: Calendar = .current) -> Int {
        todos.filter { !$0.done && !$0.isArchived && (daysUntilDue($0, now: now, calendar: calendar) ?? 1) <= 0 }.count
    }

    /// "Oct 5", with the year outside this one; overdue is before today's 4am day.
    public static func dueLabel(_ todo: TodoItem, now: Date = Date(), calendar: Calendar = .current,
                                locale: Locale = .current) -> (label: String, overdue: Bool)? {
        guard let due = todo.dueDate, let days = daysUntilDue(todo, now: now, calendar: calendar) else { return nil }
        let format = DateFormatter()
        format.calendar = calendar
        format.timeZone = calendar.timeZone
        format.locale = locale
        let sameYear = calendar.component(.year, from: due) == calendar.component(.year, from: now)
        format.setLocalizedDateFormatFromTemplate(sameYear ? "MMMd" : "MMMdyyyy")
        return (format.string(from: due), days < 0)
    }

    /// "P5" and its meaning, or nil for the neutral 3.
    public static func priorityFlag(_ priority: Int?) -> (label: String, title: String)? {
        let p = priority ?? 3
        guard p != 3 else { return nil }
        return ("P\(p)", ChatTodo.priorities[p] ?? "Priority \(p)")
    }

    /// "every day", "every 2 weeks".
    public static func repeatLabel(_ interval: Int?, _ unit: String?) -> String? {
        guard let interval, interval > 0, let unit else { return nil }
        return interval == 1 ? "every \(unit)" : "every \(interval) \(unit)s"
    }
}
