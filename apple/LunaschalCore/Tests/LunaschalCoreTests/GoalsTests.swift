import Foundation
import XCTest
@testable import LunaschalCore

final class GoalsTests: XCTestCase {
    /// `GET /api/goals/current`, trimmed to what the phone reads.
    let current = """
    {"today":"2026-11-04",
     "overarching":{"id":"o","horizon":"overarching","title":"Get strong.","details":"Get strong.\\nRun a 10k.",
                    "criteria":null,"reward":null,"hasRule":false,"targetDays":null,"periodStart":null,
                    "periodEnd":null,"periodLabel":"Overarching","overdue":false,"status":"active",
                    "currentStreak":0,"bestStreak":0,"endedAt":null,"days":[]},
     "horizons":[
       {"horizon":"week","label":"Week of Nov 2","periodStart":"2026-11-02","periodEnd":"2026-11-08","unset":false,
        "goals":[{"id":"w","horizon":"week","title":"Read","details":null,"criteria":"Read 20 min",
                  "reward":"Theater","hasRule":true,"targetDays":5,"periodStart":"2026-11-02",
                  "periodEnd":"2026-11-08","periodLabel":"Week of Nov 2","overdue":false,"status":"active",
                  "currentStreak":2,"bestStreak":2,"endedAt":null,"days":[]}]},
       {"horizon":"month","label":"November 2026","periodStart":"2026-11-01","periodEnd":"2026-11-30","unset":true,"goals":[]},
       {"horizon":"quarter","label":"Q4 2026","periodStart":"2026-10-01","periodEnd":"2026-12-31","unset":true,"goals":[]},
       {"horizon":"year","label":"2026","periodStart":"2026-01-01","periodEnd":"2026-12-31","unset":true,"goals":[]}
     ]}
    """

    func goals() throws -> CurrentGoals {
        try JSONDecoder().decode(CurrentGoals.self, from: Data(current.utf8))
    }

    func testDecodesTheServersGoals() throws {
        let goals = try goals()
        XCTAssertEqual(goals.overarching?.text, "Get strong.\nRun a 10k.")
        let week = try XCTUnwrap(goals.period(.week))
        XCTAssertEqual(week.goals.first?.streakLine, "2/5 days in a row")
        XCTAssertEqual(goals.unsetHorizons, [.month, .quarter, .year])
        XCTAssertEqual(GoalHorizon.quarter.heading, "This quarter")
    }

    func testACacheFromBeforeGoalsStillLoads() throws {
        let old = #"{"tasks":[],"todos":[]}"#
        let lists = try JSONDecoder().decode(TodoLists.self, from: Data(old.utf8))
        XCTAssertNil(lists.goals)
        // And one written with goals comes back with them.
        let withGoals = TodoLists(goals: try goals())
        let round = try JSONDecoder().decode(TodoLists.self, from: JSONEncoder().encode(withGoals))
        XCTAssertEqual(round.goals, withGoals.goals)
    }

    func testQueuedGoalChangesShowAtOnce() throws {
        let lists = TodoLists(goals: try goals())
        let ops = [
            TodoOp(change: .createGoal(id: "m", horizon: .month, title: "Cook at home", details: nil)),
            TodoOp(change: .createGoal(id: "m", horizon: .month, title: "Cook at home", details: nil)),
            TodoOp(change: .editGoal(id: "w", title: "Read daily", details: "Fiction")),
            TodoOp(change: .setOverarching(id: "o2", text: "Be kind.\nSleep more.")),
        ]
        let shown = try XCTUnwrap(lists.applying(ops, today: "2026-11-04").goals)
        let month = try XCTUnwrap(shown.period(.month))
        XCTAssertEqual(month.goals.map(\.id), ["m"], "a replayed create is not a second goal")
        XCTAssertEqual(month.goals.first?.periodLabel, "November 2026")
        XCTAssertFalse(month.unset)
        XCTAssertEqual(shown.period(.week)?.goals.first?.title, "Read daily")
        XCTAssertEqual(shown.period(.week)?.goals.first?.details, "Fiction")
        XCTAssertEqual(shown.overarching?.id, "o2")
        XCTAssertEqual(shown.overarching?.title, "Be kind.")

        let ended = try XCTUnwrap(lists.applying([TodoOp(change: .setGoalStatus(id: "w", action: "end"))],
                                                 today: "2026-11-04").goals)
        XCTAssertEqual(ended.period(.week)?.goals, [])
        XCTAssertEqual(ended.period(.week)?.unset, true)
    }

    func testAnOverarchingCreateReplacesTheOverarchingGoal() throws {
        let lists = TodoLists(goals: try goals())
        let shown = lists.applying([TodoOp(change: .createGoal(id: "x", horizon: .overarching, title: "T",
                                                               details: "Whole text"))], today: "2026-11-04")
        XCTAssertEqual(shown.goals?.overarching?.text, "Whole text")
    }

    func testGoalChangesWithoutGoalsLoadedAreKeptForTheServer() {
        // Nothing to lay the change over yet; it still waits in the queue.
        let lists = TodoLists().applying([TodoOp(change: .editGoal(id: "w", title: "x", details: nil))],
                                         today: "2026-11-04")
        XCTAssertNil(lists.goals)
    }

    func testGoalBodySendsNullDetailsSoAnEditCanClearThem() throws {
        let body = JournalAPI.GoalBody(title: "Read", details: nil)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(body)) as? [String: Any])
        XCTAssertEqual(Set(json.keys), ["title", "details"])
        XCTAssertTrue(json["details"] is NSNull)
        let create = JournalAPI.GoalBody(id: "g", horizon: "week", title: "Read", details: "x")
        let created = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(create)) as? [String: Any])
        XCTAssertEqual(created["horizon"] as? String, "week")
        XCTAssertEqual(created["id"] as? String, "g")
    }

    func testGoalChangesSurviveTheOutboxAndNameTheirSubject() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let outbox = try TodoOutbox(root: root)
        try outbox.append(.createGoal(id: "g", horizon: .year, title: "Run a 10k", details: nil))
        try outbox.append(.setGoalStatus(id: "g", action: "end"))
        let ops = try TodoOutbox(root: root).list()
        XCTAssertEqual(ops.map(\.change), [.createGoal(id: "g", horizon: .year, title: "Run a 10k", details: nil),
                                           .setGoalStatus(id: "g", action: "end")])
        XCTAssertEqual(ops[0].change.subject, "the goal “Run a 10k”")
    }
}
