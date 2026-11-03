import Foundation
import XCTest
@testable import LunaschalCore

final class HabitWidgetTests: XCTestCase {
    private var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        return calendar
    }()

    private func at(_ y: Int, _ m: Int, _ d: Int, _ h: Int = 12) -> Date {
        calendar.date(from: DateComponents(year: y, month: m, day: d, hour: h))!
    }

    private func temp() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    // MARK: Streaks (backend/habit_streaks.py)

    func testStreaksMatchTheServersRules() {
        XCTAssertEqual(HabitStreaks.current([], today: "2026-11-02"), 0)
        XCTAssertEqual(HabitStreaks.current(["2026-10-31", "2026-11-01", "2026-11-02"], today: "2026-11-02"), 3)
        // Today unticked keeps yesterday's run; a whole missed day ends it.
        XCTAssertEqual(HabitStreaks.current(["2026-10-31", "2026-11-01"], today: "2026-11-02"), 2)
        XCTAssertEqual(HabitStreaks.current(["2026-10-30", "2026-10-31"], today: "2026-11-02"), 0)
        XCTAssertEqual(HabitStreaks.current(["2026-02-28", "2026-03-01"], today: "2026-03-01"), 2)
        XCTAssertEqual(HabitStreaks.longest(["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-05", "2026-01-01"]), 3)
    }

    func testDayArithmeticCrossesMonthsAndYears() {
        XCTAssertEqual(HabitStreaks.days(endingAt: "2026-01-02", count: 4),
                       ["2025-12-30", "2025-12-31", "2026-01-01", "2026-01-02"])
        XCTAssertEqual(HabitStreaks.adding(1, to: "2028-02-28"), "2028-02-29")
    }

    func testDecodesStreaksAndToleratesTheirAbsence() throws {
        let tasks = try JSONDecoder().decode([DailyTask].self, from: Data("""
        [{"id": "T1", "title": "Stretch", "position": 1, "done": 1, "streak": 120, "longestStreak": 150,
          "history": ["2026-11-01", "2026-11-02"]},
         {"id": "T2", "title": "Read", "position": 2, "done": 0}]
        """.utf8))
        XCTAssertEqual(tasks[0].history, ["2026-11-01", "2026-11-02"])
        XCTAssertEqual(tasks[0].longestStreak, 150)
        XCTAssertEqual(tasks[1].history, [])
        // A cached copy round-trips with the server's key names.
        let again = try JSONDecoder().decode([DailyTask].self, from: JSONEncoder().encode(tasks))
        XCTAssertEqual(again, tasks)
    }

    func testAStreakLongerThanTheWindowTrustsTheServer() {
        let today = "2026-11-02"
        let all = HabitStreaks.days(endingAt: today, count: HabitStreaks.historyDays)
        let long = DailyTask(id: "T", title: "T", position: 1, done: true, history: all, serverStreak: 200, longestStreak: 200)
        XCTAssertEqual(long.streak(today: today), 200)
        let short = DailyTask(id: "T", title: "T", position: 1, done: true, history: [today], serverStreak: 200)
        XCTAssertEqual(short.streak(today: today), 1, "a short local run is newer than the server's count")
    }

    func testATickMovesTheStreakAndHistoryAtOnce() {
        let lists = TodoLists(tasks: [DailyTask(id: "T", title: "T", position: 1, done: false,
                                                history: ["2026-10-31", "2026-11-01"], longestStreak: 2)])
        let ticked = lists.applying([TodoOp(change: .tickTask(id: "T", day: "2026-11-02", done: true))],
                                    today: "2026-11-02")
        XCTAssertTrue(ticked.tasks[0].done)
        XCTAssertEqual(ticked.tasks[0].streak(today: "2026-11-02"), 3)
        XCTAssertEqual(ticked.tasks[0].best(today: "2026-11-02"), 3)

        // An untick for an earlier day breaks the run but leaves today alone.
        let past = ticked.applying([TodoOp(change: .tickTask(id: "T", day: "2026-11-01", done: false))],
                                   today: "2026-11-02")
        XCTAssertTrue(past.tasks[0].done)
        XCTAssertEqual(past.tasks[0].streak(today: "2026-11-02"), 1)
        XCTAssertEqual(past.tasks[0].week(today: "2026-11-02", count: 3), [true, false, true])
    }

    // MARK: Widget content

    func testHabitsUntickAtTheRolloverWithoutANewSnapshot() {
        let lists = TodoLists(tasks: [DailyTask(id: "T", title: "Stretch", position: 1, done: true,
                                                history: ["2026-11-01", "2026-11-02"])])
        let evening = TodoWidgetContent.habits(lists, at: at(2026, 11, 2, 23), calendar: calendar)
        XCTAssertEqual(evening[0].done, true)
        XCTAssertEqual(evening[0].streak, 2)
        // 2am still belongs to the 2nd.
        XCTAssertEqual(TodoWidgetContent.habits(lists, at: at(2026, 11, 3, 2), calendar: calendar)[0].done, true)
        let morning = TodoWidgetContent.habits(lists, at: at(2026, 11, 3, 5), calendar: calendar)[0]
        XCTAssertEqual(morning.done, false)
        XCTAssertEqual(morning.streak, 2, "an open today doesn't break the streak")
        XCTAssertEqual(morning.week, [false, false, false, false, true, true, false])
    }

    func testNextRolloverIsTheComing4am() {
        XCTAssertEqual(TodoWidgetContent.nextRollover(after: at(2026, 11, 2, 10), calendar: calendar), at(2026, 11, 3, 4))
        XCTAssertEqual(TodoWidgetContent.nextRollover(after: at(2026, 11, 3, 2), calendar: calendar), at(2026, 11, 3, 4))
        XCTAssertEqual(TodoWidgetContent.nextRollover(after: at(2026, 11, 3, 4), calendar: calendar), at(2026, 11, 4, 4))
    }

    func testToDosWidgetShowsTheToDoListDueFirst() {
        let iso = { (d: Date) in ISO8601DateFormatter().string(from: d) }
        let lists = TodoLists(todos: [
            TodoItem(id: "later", title: "later"),
            TodoItem(id: "over", title: "over", due: iso(at(2026, 10, 30))),
            TodoItem(id: "arch", title: "arch", list: "archive"),
            TodoItem(id: "done", title: "done", done: true),
            TodoItem(id: "today", title: "today", due: iso(at(2026, 11, 2))),
        ])
        let content = TodoWidgetContent.todos(lists, at: at(2026, 11, 2, 9), calendar: calendar)
        XCTAssertEqual(content.items.map(\.id), ["over", "today", "later"])
        XCTAssertEqual(content.due, 2)
    }

    func testWidgetTogglesFlipWhatIsShown() {
        let lists = TodoLists(tasks: [DailyTask(id: "T", title: "T", position: 1, done: false, history: ["2026-11-02"])],
                              todos: [TodoItem(id: "D", title: "D")])
        // Ticked today per history, though the flag is stale: the button unticks.
        XCTAssertEqual(TodoWidgetContent.toggle(habit: "T", in: lists, at: at(2026, 11, 2), calendar: calendar),
                       .tickTask(id: "T", day: "2026-11-02", done: false))
        XCTAssertEqual(TodoWidgetContent.toggle(habit: "T", in: lists, at: at(2026, 11, 3, 9), calendar: calendar),
                       .tickTask(id: "T", day: "2026-11-03", done: true))
        XCTAssertEqual(TodoWidgetContent.complete(todo: "D", in: lists), .setTodo(id: "D", done: true))
        XCTAssertNil(TodoWidgetContent.toggle(habit: "gone", in: lists, at: at(2026, 11, 2), calendar: calendar))
    }

    // MARK: Snapshot

    func testAStaleAppCopyDoesNotUndoWhatTheWidgetSent() {
        let task = DailyTask(id: "T", title: "T", position: 1, done: false)
        var snapshot = TodoWidgetSnapshot(server: TodoLists(tasks: [task]), serverAsOf: at(2026, 11, 2, 8))
        snapshot.folding([TodoOp(change: .tickTask(id: "T", day: "2026-11-02", done: true))],
                         now: at(2026, 11, 2, 9), calendar: calendar)
        XCTAssertTrue(snapshot.server.tasks[0].isDone(on: "2026-11-02"))

        // The app, last fetched at 8, writes its copy and a queued rename.
        let rename = TodoOp(change: .renameTask(id: "T", title: "Stretch"))
        let merged = snapshot.merging(app: TodoLists(tasks: [task]), asOf: at(2026, 11, 2, 8), sent: [],
                                      pending: [rename], now: at(2026, 11, 2, 9), calendar: calendar)
        XCTAssertTrue(merged.server.tasks[0].isDone(on: "2026-11-02"))
        XCTAssertEqual(merged.display(widgetPending: [], now: at(2026, 11, 2, 9), calendar: calendar).tasks[0].title, "Stretch")

        // A fetch after the send replaces it.
        let fresh = merged.merging(app: TodoLists(tasks: [task]), asOf: at(2026, 11, 2, 10), sent: [], pending: [],
                                   now: at(2026, 11, 2, 10), calendar: calendar)
        XCTAssertFalse(fresh.server.tasks[0].isDone(on: "2026-11-02"))
    }

    func testWhatTheAppSentIsFoldedInOnceEvenOverANewerWidgetFetch() {
        let iso = { (d: Date) in ISO8601DateFormatter().string(from: d) }
        let weekly = TodoItem(id: "D", title: "Water plants", due: iso(at(2026, 11, 2)), repeatInterval: 1, repeatUnit: "week")
        // The widget fetched at 9; the app fetched at 8 and completed it at 9:30.
        let snapshot = TodoWidgetSnapshot(server: TodoLists(todos: [weekly]), serverAsOf: at(2026, 11, 2, 9))
        let sent = [TodoOp(change: .setTodo(id: "D", done: true))]
        let once = snapshot.merging(app: TodoLists(todos: [weekly]), asOf: at(2026, 11, 2, 8), sent: sent, pending: [],
                                    now: at(2026, 11, 2, 9), calendar: calendar)
        let moved = once.server.todos[0].dueDate
        XCTAssertGreaterThan(moved!, at(2026, 11, 2))
        // Written again before the app's next fetch: not moved a second week.
        let twice = once.merging(app: TodoLists(todos: [weekly]), asOf: at(2026, 11, 2, 8), sent: sent, pending: [],
                                 now: at(2026, 11, 2, 9), calendar: calendar)
        XCTAssertEqual(twice.server.todos[0].dueDate, moved)
        // And a newer app fetch, with that send still recent, applies it to the fetch.
        let newer = twice.merging(app: TodoLists(todos: [weekly]), asOf: at(2026, 11, 2, 11), sent: sent, pending: [],
                                  now: at(2026, 11, 2, 11), calendar: calendar)
        XCTAssertEqual(newer.server.todos[0].dueDate, moved)
    }

    func testSnapshotRoundTripsThroughItsFile() throws {
        let url = try temp().appendingPathComponent("todo-widget/snapshot.json")
        XCTAssertNil(TodoWidgetSnapshot.read(from: url))
        let snapshot = TodoWidgetSnapshot(server: TodoLists(tasks: [DailyTask(id: "T", title: "T", position: 1, done: true,
                                                                              history: ["2026-11-02"])]),
                                          serverAsOf: at(2026, 11, 2), folded: ["x"])
        try snapshot.write(to: url)
        XCTAssertEqual(TodoWidgetSnapshot.read(from: url), snapshot)

        XCTAssertFalse(try TodoWidgetSnapshot.update(at: url) { _ in })
        XCTAssertTrue(try TodoWidgetSnapshot.update(at: url) { $0.appPending = [TodoOp(change: .deleteTodo(id: "D"))] })
        XCTAssertEqual(TodoWidgetSnapshot.read(from: url)?.appPending.count, 1)
    }

    func testDisplayLaysTheWidgetsQueueOverTheApps() {
        let snapshot = TodoWidgetSnapshot(server: TodoLists(tasks: [DailyTask(id: "T", title: "T", position: 1, done: false)]),
                                          appPending: [TodoOp(change: .renameTask(id: "T", title: "Read"))])
        let shown = snapshot.display(widgetPending: [TodoOp(change: .tickTask(id: "T", day: "2026-11-02", done: true))],
                                     now: at(2026, 11, 2), calendar: calendar)
        XCTAssertEqual(shown.tasks[0].title, "Read")
        XCTAssertTrue(shown.tasks[0].done)
    }

    // MARK: Shared queue

    func testDrainEmptiesTheQueueAndAppendKeepsIdentity() throws {
        let root = try temp()
        let widget = try TodoOutbox(root: root.appendingPathComponent("w"))
        let app = try TodoOutbox(root: root.appendingPathComponent("a"))
        let first = try widget.append(.tickTask(id: "T", day: "2026-11-02", done: true), now: at(2026, 11, 2, 8))
        try widget.append(.setTodo(id: "D", done: true))

        let drained = try widget.drain()
        XCTAssertEqual(drained.count, 2)
        XCTAssertEqual(try widget.list(), [])
        for op in drained { try app.append(op) }
        try app.append(first)  // a second drain of the same op is a no-op
        XCTAssertEqual(try app.list().map(\.id), drained.map(\.id))
        XCTAssertEqual(try app.list().first?.createdAt, at(2026, 11, 2, 8))
    }

    func testOnlyOneSenderAtATime() async throws {
        let outbox = try TodoOutbox(root: try temp())
        var inner: Bool?
        let outer = try await outbox.whileSending {
            // A second open of the lock file conflicts, as another process's would.
            inner = try await outbox.whileSending {}
        }
        XCTAssertTrue(outer)
        XCTAssertEqual(inner, false)
        let again = try await outbox.whileSending {}
        XCTAssertTrue(again, "released when the first sender finished")
    }
}

private final class FakeTodoServer: TodoTransport, TodoListsFetching {
    var lists: TodoLists
    var reachable = true
    var received: [TodoChange] = []
    var fetches = 0

    init(_ lists: TodoLists) { self.lists = lists }

    func send(_ change: TodoChange, capturedAt: Date) async throws {
        guard reachable else { throw URLError(.notConnectedToInternet) }
        received.append(change)
        lists = lists.applying([TodoOp(change: change)], today: DayKey.of(capturedAt))
    }

    func fetchTodoLists() async throws -> TodoLists {
        guard reachable else { throw URLError(.notConnectedToInternet) }
        fetches += 1
        return lists
    }
}

final class TodoWidgetStoreTests: XCTestCase {
    private func store() throws -> TodoWidgetStore {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return try TodoWidgetStore(container: url)
    }

    private let habit = DailyTask(id: "T", title: "Stretch", position: 1, done: false)

    func testATapIsShownAtOnceSentAndThenFetched() async throws {
        let store = try store()
        let server = FakeTodoServer(TodoLists(tasks: [habit]))
        let today = DayKey.of(Date())
        try store.queue(.tickTask(id: "T", day: today, done: true))
        try TodoWidgetSnapshot.update(at: store.snapshotURL) { $0.fetched(TodoLists(tasks: [self.habit]), at: Date()) }
        XCTAssertTrue(store.display(at: Date()).tasks[0].isDone(on: today), "shown before it is sent")

        let changed = await store.sync(using: server)
        XCTAssertTrue(changed)
        XCTAssertEqual(server.received, [.tickTask(id: "T", day: today, done: true)])
        XCTAssertEqual(server.fetches, 1)
        XCTAssertEqual(try store.outbox.list(), [])
        XCTAssertTrue(store.snapshot.server.tasks[0].isDone(on: today))
    }

    func testOfflineTheTapWaitsForTheApp() async throws {
        let store = try store()
        let server = FakeTodoServer(TodoLists(tasks: [habit]))
        server.reachable = false
        try store.queue(.tickTask(id: "T", day: DayKey.of(Date()), done: true))
        let changed = await store.sync(using: server)
        XCTAssertFalse(changed)
        XCTAssertEqual(try store.outbox.list().count, 1)
        let none = await store.sync(using: nil)
        XCTAssertFalse(none, "signed out: nothing is tried")
    }

    func testAFreshCopyIsNotFetchedAgain() async throws {
        let store = try store()
        let server = FakeTodoServer(TodoLists(tasks: [habit]))
        _ = await store.sync(using: server)
        _ = await store.sync(using: server)
        XCTAssertEqual(server.fetches, 1)
        _ = await store.sync(using: server, now: Date().addingTimeInterval(120))
        XCTAssertEqual(server.fetches, 2)
    }
}
