import Foundation

// The Home Screen widgets (Habits, To-Do), in apple/Widgets/. They live in a
// separate process that shares only the `group.com.lunaschal.mobile` App
// Group with the app, so everything they show and do goes through two files
// there: a snapshot of the lists, and a queue of ticks made on the widget.
//
// The app writes the snapshot; the widget may refresh its server half from
// the server itself and folds in what it sends. A tick on the widget is
// queued, shown at once, and sent by the widget if it can be; the app drains
// whatever is left into its own queue the next time it runs.

/// Where the widget's files live inside the App Group container.
public enum TodoWidgetFiles {
    public static func snapshot(in container: URL) -> URL {
        container.appendingPathComponent("todo-widget/snapshot.json")
    }

    /// The widget's own queue, separate from the app's.
    public static func outbox(in container: URL) throws -> TodoOutbox {
        try TodoOutbox(root: container.appendingPathComponent("todo-widget/outbox", isDirectory: true))
    }

    public static let habitsKind = "LunaschalHabits"
    public static let todosKind = "LunaschalTodos"
    /// What a widget opens when tapped outside a check button.
    public static let openURL = URL(string: "lunaschal://todo")!
}

/// What the widgets draw from.
public struct TodoWidgetSnapshot: Codable, Equatable {
    /// The server's lists as last known, by whichever process knew them last.
    public var server: TodoLists
    /// When `server` was true of the server: a fetch, or a send folded in.
    public var serverAsOf: Date
    /// Changes waiting in the app's queue, laid over `server`.
    public var appPending: [TodoOp]
    /// Ops already folded into `server`, so none is applied twice: ticking
    /// is idempotent, but completing a repeating to-do moves its due date
    /// each time. Kept across fetches, since a fetch holds them already.
    public var folded: [String]

    static let foldedLimit = 200

    public init(server: TodoLists = TodoLists(), serverAsOf: Date = .distantPast, appPending: [TodoOp] = [],
                folded: [String] = []) {
        self.server = server; self.serverAsOf = serverAsOf; self.appPending = appPending; self.folded = folded
    }

    /// The app's view written over this one: its last fetch, the changes it
    /// has sent since, and its queue. Its fetch replaces this copy only when
    /// newer — a tick the widget sent is in `server` already, and an app
    /// that hasn't fetched since must not take it back out — but what it
    /// has sent is folded in either way. Its queue replaces the old one.
    public func merging(app fetched: TodoLists, asOf: Date, sent: [TodoOp], pending: [TodoOp],
                        now: Date, calendar: Calendar = .current) -> TodoWidgetSnapshot {
        var merged = self
        if asOf >= serverAsOf {
            merged.server = fetched
            merged.serverAsOf = asOf
            merged.folded.removeAll { id in sent.contains { $0.id == id } }
        }
        merged.folding(sent, now: now, calendar: calendar)
        merged.appPending = pending
        return merged
    }

    /// A fresh fetch by the widget.
    public mutating func fetched(_ lists: TodoLists, at now: Date) {
        server = lists
        serverAsOf = now
    }

    /// Changes just delivered, now part of the server's lists.
    public mutating func folding(_ sent: [TodoOp], now: Date, calendar: Calendar = .current) {
        let new = sent.filter { !folded.contains($0.id) }
        guard !new.isEmpty else { return }
        server = server.applying(new, today: DayKey.of(now, calendar: calendar), now: now)
        serverAsOf = max(serverAsOf, now)
        folded = Array((folded + new.map(\.id)).suffix(Self.foldedLimit))
    }

    /// What to show: the server's lists, the app's queue, then the widget's.
    public func display(widgetPending: [TodoOp], now: Date, calendar: Calendar = .current) -> TodoLists {
        server.applying(appPending + widgetPending, today: DayKey.of(now, calendar: calendar), now: now)
    }

    public static func read(from url: URL) -> TodoWidgetSnapshot? {
        (try? Data(contentsOf: url)).flatMap { try? JSONDecoder().decode(TodoWidgetSnapshot.self, from: $0) }
    }

    public func write(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }

    /// Reads, changes and writes the file under a lock, since the app and
    /// the widget both write it. Returns whether anything changed.
    @discardableResult
    public static func update(at url: URL, _ change: (inout TodoWidgetSnapshot) -> Void) throws -> Bool {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        return try withFileLock(url.appendingPathExtension("lock")) {
            let before = read(from: url) ?? TodoWidgetSnapshot()
            var after = before
            change(&after)
            guard after != before else { return false }
            try after.write(to: url)
            return true
        }
    }
}

/// One habit as a widget row.
public struct HabitWidgetRow: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let done: Bool
    public let streak: Int
    /// The last seven days, oldest first; the last one is today.
    public let week: [Bool]
    /// Which of those days were excused: they neither counted nor broke the streak.
    public var excused: [Bool] = []
    /// Yesterday was missed: today is the one that can't be.
    public var atRisk = false
}

/// What each widget shows, for a moment that may be after the 4am rollover
/// the snapshot was written before: a habit's `done` is read off its
/// history for that moment's day, never off the stored flag.
public enum TodoWidgetContent {
    public static func habits(_ lists: TodoLists, at now: Date, calendar: Calendar = .current) -> [HabitWidgetRow] {
        let today = DayKey.of(now, calendar: calendar)
        return lists.tasks.map {
            HabitWidgetRow(id: $0.id, title: $0.title, done: $0.isDone(on: today),
                           streak: $0.streak(today: today), week: $0.week(today: today),
                           excused: HabitStreaks.days(endingAt: today, count: 7).map($0.isExcused(on:)),
                           atRisk: $0.atRisk(today: today))
        }
    }

    /// The To-Do list as the Todo tab orders it (dated first, soonest on
    /// top), with how many are due today or overdue.
    public static func todos(_ lists: TodoLists, at now: Date,
                             calendar: Calendar = .current) -> (items: [TodoItem], due: Int) {
        let open = TodoRules.on("todo", lists.todos)
        return (TodoRules.active(open, now: now, calendar: calendar),
                TodoRules.dueCount(open, now: now, calendar: calendar))
    }

    /// The next 4am after `now`: when every habit unticks and a to-do due
    /// tomorrow becomes due today, so the widget needs a new entry then.
    public static func nextRollover(after now: Date, calendar: Calendar = .current) -> Date {
        let shifted = now.addingTimeInterval(-Double(DayKey.rolloverHour) * 3600)
        let dayStart = calendar.startOfDay(for: shifted)
        let tomorrow = calendar.date(byAdding: .day, value: 1, to: dayStart) ?? dayStart.addingTimeInterval(86400)
        return calendar.date(bySettingHour: DayKey.rolloverHour, minute: 0, second: 0, of: tomorrow)
            ?? tomorrow.addingTimeInterval(Double(DayKey.rolloverHour) * 3600)
    }

    /// The change a check button on the widget makes.
    public static func toggle(habit id: String, in lists: TodoLists, at now: Date,
                              calendar: Calendar = .current) -> TodoChange? {
        let today = DayKey.of(now, calendar: calendar)
        guard let task = lists.tasks.first(where: { $0.id == id }) else { return nil }
        return .tickTask(id: id, day: today, done: !task.isDone(on: today))
    }

    public static func complete(todo id: String, in lists: TodoLists) -> TodoChange? {
        guard let todo = lists.todos.first(where: { $0.id == id }) else { return nil }
        return .setTodo(id: id, done: !todo.done)
    }
}

/// Both lists in one call, for the widget's refresh.
public protocol TodoListsFetching {
    func fetchTodoLists() async throws -> TodoLists
}

extension JournalAPI: TodoListsFetching {
    public func fetchTodoLists() async throws -> TodoLists {
        async let tasks = dailyTasks()
        async let todos = todos()
        return try await TodoLists(tasks: tasks, todos: todos)
    }
}

/// The widget extension's side of the two files: what to draw, queueing a
/// tap, and sending the queue and fetching fresh lists when signed in.
public final class TodoWidgetStore {
    public let snapshotURL: URL
    public let outbox: TodoOutbox

    public init(container: URL) throws {
        snapshotURL = TodoWidgetFiles.snapshot(in: container)
        outbox = try TodoWidgetFiles.outbox(in: container)
    }

    public var snapshot: TodoWidgetSnapshot { TodoWidgetSnapshot.read(from: snapshotURL) ?? TodoWidgetSnapshot() }

    public func display(at now: Date, calendar: Calendar = .current) -> TodoLists {
        snapshot.display(widgetPending: (try? outbox.list()) ?? [], now: now, calendar: calendar)
    }

    public func queue(_ change: TodoChange, now: Date = Date()) throws {
        try outbox.append(change, now: now)
    }

    /// Sends what the widget has queued, then fetches fresh lists if
    /// anything was sent or the copy is older than `freshness`. Failure is
    /// quiet: whatever wasn't sent stays queued for the app to take, and the
    /// widget goes on drawing the copy it has. Skipped while the app is
    /// moving the queue. Returns whether the snapshot changed.
    @discardableResult
    public func sync(using api: (TodoTransport & TodoListsFetching)?, now: Date = Date(),
                     freshness: TimeInterval = 60, calendar: Calendar = .current) async -> Bool {
        guard let api else { return false }
        var changed = false
        _ = try? await outbox.whileSending {
            let before = try outbox.list()
            if !before.isEmpty {
                _ = try? await TodoSync(outbox: outbox).run(using: api)
                let left = Set(try outbox.list().map(\.id))
                let sent = before.filter { !left.contains($0.id) }
                if !sent.isEmpty {
                    changed = (try? TodoWidgetSnapshot.update(at: snapshotURL) {
                        $0.folding(sent, now: now, calendar: calendar)
                    }) ?? false
                }
            }
            guard changed || now.timeIntervalSince(snapshot.serverAsOf) >= freshness else { return }
            let started = Date()
            if let lists = try? await api.fetchTodoLists() {
                let updated = (try? TodoWidgetSnapshot.update(at: snapshotURL) {
                    if started >= $0.serverAsOf { $0.fetched(lists, at: started) }
                }) ?? false
                changed = changed || updated
            }
        }
        return changed
    }
}
