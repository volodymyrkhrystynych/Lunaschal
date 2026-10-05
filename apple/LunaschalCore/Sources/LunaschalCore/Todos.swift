import Foundation

// The Todo tab on the phone: the desktop Lifestyle tab's daily tasks and
// to-do lists (src/components/Tasks/), over the same /api/tasks routes.

/// One of up to four things done every day; `done` is today's completion.
public struct DailyTask: Codable, Equatable, Identifiable {
    public let id: String
    public var title: String
    public var position: Int
    public var done: Bool

    public static let limit = 4

    public init(id: String, title: String, position: Int, done: Bool) {
        self.id = id; self.title = title; self.position = position; self.done = done
    }

    enum CodingKeys: String, CodingKey { case id, title, position, done }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decode(String.self, forKey: .title)
        position = try c.decode(Int.self, forKey: .position)
        // The route's CASE gives 0/1, not a boolean.
        if let flag = try? c.decode(Bool.self, forKey: .done) { done = flag }
        else { done = try c.decode(Int.self, forKey: .done) != 0 }
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
public struct TodoDraft: Encodable, Equatable {
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

    enum CodingKeys: String, CodingKey { case id, title, notes, due, repeatInterval, repeatUnit, priority, list }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(id, forKey: .id)
        try c.encode(title.trimmingCharacters(in: .whitespacesAndNewlines), forKey: .title)
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
