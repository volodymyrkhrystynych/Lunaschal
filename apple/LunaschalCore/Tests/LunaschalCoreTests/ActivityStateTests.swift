import XCTest
@testable import LunaschalCore

final class ActivityStateTests: XCTestCase {
    private var root: URL!
    private var store: ActivityStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try ActivityStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func change(_ state: ActivityState, minutes: Double, source: ActivitySwitch.Source = .watch) -> ActivitySwitch {
        ActivitySwitch(state: state, now: Date(timeIntervalSince1970: 1_800_000_000 + minutes * 60), source: source)
    }

    private final class Fake: ActivityTransport {
        var sent: [String] = []
        var fail: [String: Error] = [:]
        func sendActivitySwitch(_ change: ActivitySwitch) async throws {
            if let error = fail[change.id] { throw error }
            sent.append(change.id)
        }
    }

    /// The same JSON goes to the server: unix seconds and the raw state.
    func testEncodesAsTheServerReadsIt() throws {
        let item = ActivitySwitch(id: "01J0000000000000000000000A", state: .mix, at: 1_800_000_000, source: .phone)
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(item)) as! [String: Any]
        XCTAssertEqual(json["state"] as? String, "mix")
        XCTAssertEqual(json["at"] as? Int, 1_800_000_000)
        XCTAssertEqual(json["source"] as? String, "phone")
        let back = try JSONDecoder().decode(ActivitySwitch.self, from: JSONEncoder().encode(item))
        XCTAssertEqual(back, item)
    }

    /// The server answers `null` before anyone has ever switched.
    func testDecodesNoCurrentStateAsNil() throws {
        XCTAssertNil(try JSONDecoder().decode(ActivitySwitch?.self, from: Data("null".utf8)))
    }

    func testTheLatestIsByWhenItWasMadeNotWhenItArrived() {
        let phone = change(.leisure, minutes: 30, source: .phone), watch = change(.work, minutes: 10)
        XCTAssertEqual(ActivitySwitch.latest([phone, nil, watch]), phone)
        XCTAssertEqual(ActivitySwitch.latest([watch]), watch)
        XCTAssertNil(ActivitySwitch.latest([nil, nil]))
    }

    func testStatesNameTheirGlyphsAndColours() {
        XCTAssertEqual(ActivityState.leisure.symbol, "cup.and.saucer.fill")
        XCTAssertEqual(ActivityState.mix.symbol, ActivityState.work.symbol)
        XCTAssertEqual(ActivityState.mix.colors, ActivityState.work.colors + ActivityState.leisure.colors)
    }

    @MainActor
    func testUploadsOldestFirstAndEmptiesTheOutbox() async throws {
        let late = change(.leisure, minutes: 60), early = change(.work, minutes: 0)
        try store.save(late); try store.save(early)
        let fake = Fake()
        let refused = try await ActivitySync(store: store).run(using: fake)
        XCTAssertEqual(refused, 0)
        XCTAssertEqual(fake.sent, [early.id, late.id])
        XCTAssertTrue(try store.list().isEmpty)
    }

    @MainActor
    func testARefusedSwitchIsDroppedAndAnOutageKeepsTheRest() async throws {
        let bad = change(.work, minutes: 0), offline = change(.mix, minutes: 30), later = change(.leisure, minutes: 60)
        for item in [bad, offline, later] { try store.save(item) }
        let fake = Fake()
        fake.fail = [bad.id: HTTPFailure(status: 400), offline.id: URLError(.notConnectedToInternet)]
        do {
            _ = try await ActivitySync(store: store).run(using: fake)
            XCTFail("an outage should stop the pass")
        } catch {}
        XCTAssertEqual(try store.list().map(\.id), [offline.id, later.id])
        fake.fail = [:]
        try await ActivitySync(store: store).run(using: fake)
        XCTAssertEqual(fake.sent, [offline.id, later.id])
    }

    func testTheStoreRefusesABadID() {
        XCTAssertThrowsError(try store.save(ActivitySwitch(id: "../x", state: .work, at: 0, source: .watch)))
    }

    func testTheAcknowledgementMustNameTheSwitch() throws {
        let item = change(.work, minutes: 0)
        XCTAssertNoThrow(try JournalAPI.validateActivityAcknowledgement(Data("{\"id\":\"\(item.id)\"}".utf8), for: item))
        XCTAssertThrowsError(try JournalAPI.validateActivityAcknowledgement(Data("{\"id\":\"other\"}".utf8), for: item))
    }
}

final class CalendarActivityTests: XCTestCase {
    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// 2026-10-05 04:00 UTC, the start of that day's window.
    private let dayStart = 1_791_172_800

    private func span(_ id: String, _ state: ActivityState, _ from: Int, _ to: Int) -> CalendarActivitySpan {
        CalendarActivitySpan(id: id, state: state, startedAt: dayStart + from * 60, endedAt: dayStart + to * 60)
    }

    func testDecodesTheServersAnswer() throws {
        let json = #"[{"id":"a@5","state":"mix","startedAt":1791180000,"endedAt":1791181800}]"#
        XCTAssertEqual(try JSONDecoder().decode([CalendarActivitySpan].self, from: Data(json.utf8)),
                       [CalendarActivitySpan(id: "a@5", state: .mix, startedAt: 1_791_180_000, endedAt: 1_791_181_800)])
    }

    func testTheRailIsBoundedByWakeAndSleepLikeTheReadingRail() throws {
        let sleep = SleepDay(date: "2026-10-05", wakeAt: dayStart + 240 * 60, sleepAt: dayStart + 1200 * 60)
        let rail = try XCTUnwrap(CalendarActivity.rail(
            day: "2026-10-05", sleep: sleep,
            spans: [span("a", .leisure, 0, 300), span("b", .work, 300, 600), span("c", .mix, 600, 1440)],
            calendar: utc))
        XCTAssertEqual([rail.startMinutes, rail.endMinutes], [240, 1200])
        XCTAssertEqual(rail.segments.map(\.state), [.leisure, .work, .mix])
        XCTAssertEqual(rail.segments.map(\.minutes), [240...300, 300...600, 600...1200])
    }

    func testNothingInsideWakingHoursDrawsNoRail() {
        let sleep = SleepDay(date: "2026-10-05", wakeAt: dayStart + 600 * 60)
        XCTAssertNil(CalendarActivity.rail(day: "2026-10-05", sleep: sleep,
                                           spans: [span("a", .work, 0, 300)], calendar: utc))
        XCTAssertNil(CalendarActivity.rail(day: "2026-10-05", sleep: nil, spans: [], calendar: utc))
    }
}

final class WatchGlanceTests: XCTestCase {
    private func row(_ id: String, done: Bool) -> HabitWidgetRow {
        HabitWidgetRow(id: id, title: id, done: done, streak: 0, week: [])
    }

    func testCountsTheHabitsLeftAndCarriesTheState() {
        let state = ActivitySwitch(id: ULID.make(), state: .work, at: 5, source: .phone)
        let glance = WatchGlance.habits([row("a", done: true), row("b", done: false), row("c", done: false)],
                                        day: "2026-11-17", state: state)
        XCTAssertEqual([glance.habitsTotal, glance.habitsLeft], [3, 2])
        XCTAssertEqual(glance.state, state)
    }

    /// After 4am every habit is open again, without waiting for the phone.
    func testRollsOverOnItsOwnAndKnowsWhenItWasNeverTold() {
        let glance = WatchGlance(habitsDay: "2026-11-17", habitsTotal: 3, habitsLeft: 1)
        XCTAssertEqual(glance.left(on: "2026-11-17"), 1)
        XCTAssertEqual(glance.left(on: "2026-11-18"), 3)
        XCTAssertNil(WatchGlance().left(on: "2026-11-17"))
    }

    func testRoundTripsThroughTheApplicationContextAndAFile() throws {
        let glance = WatchGlance(state: ActivitySwitch(id: ULID.make(), state: .mix, at: 9, source: .watch),
                                 habitsDay: "2026-11-17", habitsTotal: 4, habitsLeft: 2)
        XCTAssertEqual(WatchGlance(context: glance.context), glance)
        XCTAssertNil(WatchGlance(context: ["glance": Data("{}".utf8)]))
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        defer { try? FileManager.default.removeItem(at: url) }
        try glance.save(to: url)
        XCTAssertEqual(WatchGlance.load(from: url), glance)
    }
}
