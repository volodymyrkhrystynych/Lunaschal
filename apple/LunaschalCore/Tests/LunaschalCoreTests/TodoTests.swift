import Foundation
import XCTest
@testable import LunaschalCore

final class TodoTests: XCTestCase {
    private var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        return calendar
    }()

    /// A local time in Toronto.
    private func at(_ y: Int, _ m: Int, _ d: Int, _ h: Int = 12) -> Date {
        calendar.date(from: DateComponents(year: y, month: m, day: d, hour: h))!
    }

    /// Due at local noon of the day, as the server returns it.
    private func todo(_ id: String, due: Date? = nil, done: Bool = false, list: String = "todo", priority: Int = 3,
                      every interval: Int? = nil, _ unit: String? = nil) -> TodoItem {
        TodoItem(id: id, title: id, done: done, list: list,
                 due: due.map { ISO8601DateFormatter().string(from: $0) },
                 repeatInterval: interval, repeatUnit: unit, priority: priority)
    }

    func testDecodesTheServersRows() throws {
        let tasks = try JSONDecoder().decode([DailyTask].self, from: Data("""
        [{"id": "T1", "title": "Stretch", "position": 1, "done": 1, "createdAt": "x", "updatedAt": "x"},
         {"id": "T2", "title": "Read", "position": 2, "done": 0, "createdAt": "x", "updatedAt": "x"}]
        """.utf8))
        XCTAssertEqual(tasks.map(\.done), [true, false])

        let todos = try JSONDecoder().decode([TodoItem].self, from: Data("""
        [{"id": "D1", "title": "Dentist", "done": false, "completedAt": null, "list": "todo", "notes": null,
          "due": "2026-10-05T16:00:00+00:00", "repeatInterval": null, "repeatUnit": null, "priority": 4,
          "createdAt": "2026-10-01T12:00:00+00:00", "updatedAt": "2026-10-01T12:00:00+00:00"}]
        """.utf8))
        XCTAssertEqual(todos[0].dueDate, at(2026, 10, 5))
    }

    func testBadgeCountsOpenToDosDueTodayOrBefore() {
        let now = at(2026, 10, 5, 9)
        let todos = [
            todo("overdue", due: at(2026, 10, 3)),
            todo("today", due: at(2026, 10, 5)),
            todo("tomorrow", due: at(2026, 10, 6)),
            todo("undated"),
            todo("done", due: at(2026, 10, 1), done: true),
            todo("archived", due: at(2026, 10, 1), list: "archive"),
        ]
        XCTAssertEqual(TodoRules.dueCount(todos, now: now, calendar: calendar), 2)
        // At 01:00 it's still the 4th's day, so the 5th isn't due yet.
        XCTAssertEqual(TodoRules.dueCount(todos, now: at(2026, 10, 5, 1), calendar: calendar), 1)
    }

    func testListOrderAndFarOffRepeats() {
        let now = at(2026, 10, 5, 9)
        let todos = [
            todo("undated-low", priority: 2),
            todo("later", due: at(2026, 10, 9)),
            todo("undated-high", priority: 5),
            todo("sooner", due: at(2026, 10, 6)),
            todo("monthly-far", due: at(2026, 10, 20), every: 1, "month"),
            todo("weekly-near", due: at(2026, 10, 6), every: 1, "week"),
            todo("done", done: true),
        ]
        XCTAssertEqual(TodoRules.active(todos, now: now, calendar: calendar).map(\.id),
                       ["sooner", "weekly-near", "later", "undated-high", "undated-low"])
        // A month's tenth is three days: hidden at fifteen days out, shown at three.
        XCTAssertTrue(TodoRules.isFarOffPeriodic(todos[4], now: now, calendar: calendar))
        XCTAssertFalse(TodoRules.isFarOffPeriodic(todos[4], now: at(2026, 10, 17), calendar: calendar))
        XCTAssertEqual(TodoRules.on("archive", [todo("a", list: "archive"), todo("b", list: "chores")]).map(\.id), ["a"])
        XCTAssertEqual(TodoRules.on("todo", [todo("a", list: "archive"), todo("b", list: "chores")]).map(\.id), ["b"])
    }

    func testLabels() {
        let now = at(2026, 10, 5, 9)
        let english = Locale(identifier: "en_US")
        let overdue = TodoRules.dueLabel(todo("x", due: at(2026, 10, 4)), now: now, calendar: calendar, locale: english)
        XCTAssertEqual(overdue?.label, "Oct 4")
        XCTAssertEqual(overdue?.overdue, true)
        XCTAssertEqual(TodoRules.dueLabel(todo("x", due: at(2026, 10, 5)), now: now, calendar: calendar, locale: english)?.overdue, false)
        XCTAssertEqual(TodoRules.dueLabel(todo("x", due: at(2027, 1, 2)), now: now, calendar: calendar, locale: english)?.label, "Jan 2, 2027")
        XCTAssertNil(TodoRules.dueLabel(todo("x"), now: now, calendar: calendar))
        XCTAssertNil(TodoRules.priorityFlag(3))
        XCTAssertEqual(TodoRules.priorityFlag(5)?.label, "P5")
        XCTAssertEqual(TodoRules.priorityFlag(1)?.title, "Very unimportant")
        XCTAssertEqual(TodoRules.repeatLabel(1, "day"), "every day")
        XCTAssertEqual(TodoRules.repeatLabel(2, "week"), "every 2 weeks")
        XCTAssertNil(TodoRules.repeatLabel(nil, "week"))
    }

    func testDraftEncodesEveryFieldSoAnEditCanClearOne() throws {
        func json(_ draft: TodoDraft) throws -> [String: JSONValue] {
            try JSONDecoder().decode([String: JSONValue].self, from: JSONEncoder().encode(draft))
        }
        let bare = try json(TodoDraft(id: "01J", title: "  Call mum ", notes: "  "))
        XCTAssertEqual(bare["id"], .string("01J"))
        XCTAssertEqual(bare["title"], .string("Call mum"))
        XCTAssertEqual(bare["notes"], .null)
        XCTAssertEqual(bare["due"], .null)
        XCTAssertEqual(bare["repeatInterval"], .null)
        XCTAssertEqual(bare["repeatUnit"], .null)
        XCTAssertEqual(bare["priority"], .number(3))
        XCTAssertEqual(bare["list"], .string("todo"))

        let full = try json(TodoDraft(title: "Water plants", due: Date(timeIntervalSince1970: 1_790_000_000),
                                      repeatInterval: 2, repeatUnit: "day", priority: 5, list: "archive"))
        XCTAssertNil(full["id"])
        XCTAssertEqual(full["repeatInterval"], .number(2))
        XCTAssertEqual(full["repeatUnit"], .string("day"))
        XCTAssertEqual(full["due"], .number(Double(TodoPromotion.dueSeconds(Date(timeIntervalSince1970: 1_790_000_000)))))

        // Opening an existing to-do in the form round-trips what it has.
        let item = todo("x", due: at(2026, 10, 5), list: "archive", priority: 4, every: 3, "week")
        let draft = TodoDraft(item)
        XCTAssertEqual(draft.due, at(2026, 10, 5))
        XCTAssertEqual(draft.repeatInterval, 3)
        XCTAssertEqual(draft.list, "archive")
        XCTAssertEqual(draft.priority, 4)
    }
}

final class TodoOutboxTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    private let server = TodoLists(
        tasks: [DailyTask(id: "T1", title: "Stretch", position: 1, done: false),
                DailyTask(id: "T2", title: "Read", position: 2, done: false)],
        todos: [TodoItem(id: "D1", title: "Dentist"),
                TodoItem(id: "R1", title: "Rent", due: "2026-10-05T16:00:00Z", repeatInterval: 1, repeatUnit: "month")])

    func testQueuedChangesShowOverTheServersLists() {
        let now = ISO8601DateFormatter().date(from: "2026-10-05T13:00:00Z")!
        let ops = [
            TodoChange.addTask(id: "T3", title: "Walk"),
            .tickTask(id: "T1", day: "2026-10-05", done: true),
            .tickTask(id: "T2", day: "2026-10-04", done: true),
            .reorderTasks(["T3", "T1", "T2"]),
            .renameTask(id: "T2", title: "Read 20 pages"),
            .createTodo(TodoDraft(id: "N1", title: " Stamps ", priority: 2)),
            .setTodo(id: "D1", done: true),
            .setTodo(id: "R1", done: true),
            .moveTodo(id: "N1", list: "archive"),
            .deleteTask(id: "T1"),
        ].map { TodoOp(change: $0) }
        let shown = server.applying(ops, today: "2026-10-05", now: now)
        XCTAssertEqual(shown.tasks.map(\.id), ["T3", "T2"])
        XCTAssertEqual(shown.tasks.map(\.position), [1, 2])
        XCTAssertEqual(shown.tasks[1].title, "Read 20 pages")
        XCTAssertFalse(shown.tasks[1].done, "Yesterday's tick isn't today's")
        XCTAssertEqual(shown.todos.first { $0.id == "D1" }?.done, true)
        let rent = shown.todos.first { $0.id == "R1" }!
        XCTAssertFalse(rent.done, "A repeating to-do moves on instead")
        XCTAssertEqual(rent.due, "2026-11-05T16:00:00Z")
        let stamps = shown.todos.first { $0.id == "N1" }!
        XCTAssertEqual(stamps.title, "Stamps")
        XCTAssertEqual(stamps.priority, 2)
        XCTAssertTrue(stamps.isArchived)
        // Replaying a create the server already has adds nothing.
        XCTAssertEqual(shown.applying([TodoOp(change: .addTask(id: "T3", title: "Walk"))], today: "2026-10-05").tasks.count, 2)
    }

    func testNextDueMatchesTheServer() {
        let iso = ISO8601DateFormatter()
        let now = iso.date(from: "2026-10-05T13:00:00Z")!
        // Long overdue: rolls past now, anchored on the original day.
        XCTAssertEqual(iso.string(from: TodoRules.nextDue(iso.date(from: "2026-07-31T16:00:00Z"), interval: 1, unit: "month", now: now)),
                       "2026-10-30T16:00:00Z")
        XCTAssertEqual(iso.string(from: TodoRules.nextDue(iso.date(from: "2026-10-01T16:00:00Z"), interval: 2, unit: "week", now: now)),
                       "2026-10-15T16:00:00Z")
        XCTAssertEqual(TodoRules.nextDue(nil, interval: 3, unit: "day", now: now), now.addingTimeInterval(3 * 86400))
    }

    func testTheOutboxSurvivesAndKeepsItsOrder() throws {
        let outbox = try TodoOutbox(root: root)
        try outbox.append(.addTask(id: "T3", title: "Walk"))
        try outbox.append(.createTodo(TodoDraft(id: "N1", title: "Stamps", due: Date(timeIntervalSince1970: 1_790_000_000),
                                                repeatInterval: 2, repeatUnit: "day")))
        let reopened = try TodoOutbox(root: root).list()
        XCTAssertEqual(reopened.map(\.change), try outbox.list().map(\.change))
        guard case let .createTodo(draft) = reopened[1].change else { return XCTFail("Order kept") }
        XCTAssertEqual(draft.repeatInterval, 2)
        XCTAssertEqual(draft.repeatUnit, "day")
        XCTAssertEqual(draft.due.map { TodoPromotion.dueSeconds($0) },
                       TodoPromotion.dueSeconds(Date(timeIntervalSince1970: 1_790_000_000)))
    }

    private final class Transport: TodoTransport {
        var sent: [TodoChange] = []
        var answers: [Error?] = []
        func send(_ change: TodoChange) async throws {
            sent.append(change)
            if !answers.isEmpty, let error = answers.removeFirst() { throw error }
        }
    }

    func testARefusalIsDroppedAndTheRestGoOn() async throws {
        let outbox = try TodoOutbox(root: root)
        try outbox.append(.addTask(id: "T5", title: "Fifth"))
        try outbox.append(.deleteTodo(id: "gone"))
        try outbox.append(.tickTask(id: "T1", day: "2026-10-05", done: true))
        let transport = Transport()
        transport.answers = [TodoRefusal(status: 400, message: "max 4 tasks allowed"),
                             TodoRefusal(status: 404, message: "Not found"), nil]
        let refused = try await TodoSync(outbox: outbox).run(using: transport)
        XCTAssertEqual(transport.sent.count, 3)
        XCTAssertEqual(refused, ["The server didn't take “Fifth”: max 4 tasks allowed"])
        XCTAssertEqual(try outbox.list(), [])
    }

    func testAnUnreachableServerKeepsTheQueue() async throws {
        let outbox = try TodoOutbox(root: root)
        try outbox.append(.tickTask(id: "T1", day: "2026-10-05", done: true))
        try outbox.append(.setTodo(id: "D1", done: true))
        let transport = Transport()
        transport.answers = [nil, URLError(.notConnectedToInternet)]
        do {
            _ = try await TodoSync(outbox: outbox).run(using: transport)
            XCTFail("Offline stops the pass")
        } catch {}
        XCTAssertEqual(try outbox.list().map(\.change), [.setTodo(id: "D1", done: true)])
        transport.answers = [HTTPFailure(status: 401)]
        do { _ = try await TodoSync(outbox: outbox).run(using: transport); XCTFail() } catch {}
        XCTAssertEqual(try outbox.list().count, 1, "Signed out keeps it too")
    }
}
