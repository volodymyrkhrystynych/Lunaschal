import XCTest
@testable import LunaschalCore

final class CalendarDayTests: XCTestCase {
    private func timed(_ id: String, _ date: String, _ time: String?, _ end: String? = nil,
                       allDay: Bool = false) -> CalendarEvent {
        CalendarEvent(id: id, title: id, date: date, time: time, endTime: end, allDay: allDay)
    }

    func testTimelineRunsFromFourAmToFourAm() {
        XCTAssertEqual(CalendarTimeline.offset(fromWall: 4 * 60), 0)
        XCTAssertEqual(CalendarTimeline.offset(fromWall: 0), 20 * 60)
        XCTAssertEqual(CalendarTimeline.offset(fromWall: 3 * 60 + 59), 1439)
        XCTAssertEqual(CalendarTimeline.wall(fromOffset: 1440), 4 * 60)
        XCTAssertTrue(CalendarTimeline.isAfterMidnight(20 * 60))
        XCTAssertFalse(CalendarTimeline.isAfterMidnight(20 * 60 - 1))
        XCTAssertEqual(CalendarTimeline.displayHours.first, 4)
        XCTAssertEqual(CalendarTimeline.displayHours.last, 3)
        XCTAssertEqual(CalendarTimeline.hourLabel(0), "12am")
        XCTAssertEqual(CalendarTimeline.hourLabel(13), "1pm")
    }

    func testDurationWrapsPastMidnightAndDefaultsWithoutAnEnd() {
        XCTAssertEqual(CalendarTimeline.duration(from: "21:20", to: "00:30"), 190)
        XCTAssertEqual(CalendarTimeline.duration(from: "09:00", to: "10:15"), 75)
        XCTAssertEqual(CalendarTimeline.duration(from: "09:00", to: nil), 30)
    }

    func testPlanTakesTheNightFromTheNextDateAndLeavesItsMorning() {
        let events = [
            timed("morning", "2026-10-05", "09:00", "10:00"),
            timed("too-early", "2026-10-05", "02:00", "03:00"),   // the previous night's
            timed("late", "2026-10-06", "01:30", "02:00"),        // tonight's
            timed("tomorrow", "2026-10-06", "09:00"),
            timed("all-day", "2026-10-05", nil, allDay: true),
            timed("untimed", "2026-10-05", nil),
            timed("next-all-day", "2026-10-06", nil, allDay: true),
        ]
        let plan = CalendarTimeline.plan(day: "2026-10-05", events: events, exceptions: [])
        XCTAssertEqual(plan.timed.map(\.occurrence.event.id), ["morning", "late"])
        XCTAssertEqual(plan.timed.map(\.startMinutes), [300, 1290])
        XCTAssertEqual(plan.timed.map(\.endMinutes), [360, 1320])
        XCTAssertEqual(Set(plan.allDay.map(\.event.id)), ["all-day", "untimed"])
    }

    func testOverlapsGetSideBySideLanesLongestLeftmost() {
        let events = [
            timed("short", "2026-10-05", "09:30", "10:00"),
            timed("long", "2026-10-05", "09:00", "12:00"),
            timed("after", "2026-10-05", "12:00", "13:00"),
            timed("also", "2026-10-05", "09:45", "10:30"),
        ]
        let lanes = Dictionary(uniqueKeysWithValues: CalendarTimeline.plan(day: "2026-10-05", events: events, exceptions: [])
            .timed.map { ($0.occurrence.event.id, $0.lane) })
        XCTAssertEqual(lanes, ["long": 0, "also": 1, "short": 2, "after": 0])
    }

    func testPlusUsesNowOnTodayAndEightOtherwise() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        func at(_ text: String) -> Date {
            let format = DateFormatter()
            format.calendar = calendar; format.timeZone = calendar.timeZone
            format.dateFormat = "yyyy-MM-dd HH:mm"
            return format.date(from: text)!
        }
        let morning = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-05 14:10"), calendar: calendar)
        XCTAssertEqual(morning.date, "2026-10-05"); XCTAssertEqual(morning.time, "14:00"); XCTAssertEqual(morning.endTime, "14:30")
        // 01:20 is still the 5th's day, and lands on the 6th's date.
        let night = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-06 01:20"), calendar: calendar)
        XCTAssertEqual(night.date, "2026-10-06"); XCTAssertEqual(night.time, "01:30")
        // Never past the bottom of the timeline.
        let late = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-06 03:55"), calendar: calendar)
        XCTAssertEqual(late.time, "03:30"); XCTAssertEqual(late.endTime, "04:00")
        let other = CalendarTimeline.newEventSlot(day: "2026-10-09", now: at("2026-10-05 14:10"), calendar: calendar)
        XCTAssertEqual(other.date, "2026-10-09"); XCTAssertEqual(other.time, "08:00")
    }

    func testDraftEncodesAsTheRoutesReadItWithExplicitNulls() throws {
        let draft = CalendarDraft(id: "01J00000000000000000000000", title: "  Gym ", date: "2026-10-05",
                                  time: "07:00", endTime: "08:00", description: " ", tags: CalendarDraft.tags(from: "health, , gym"),
                                  repeatFreq: "weekly", repeatInterval: 0, repeatByweekday: [3, 1])
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(draft)) as! [String: Any]
        XCTAssertEqual(body["title"] as? String, "Gym")
        XCTAssertEqual(body["tags"] as? [String], ["health", "gym"])
        XCTAssertEqual(body["repeatByweekday"] as? [Int], [1, 3])
        XCTAssertEqual(body["repeatInterval"] as? Int, 1)
        // An edit must be able to clear a field, so absent is null, not missing.
        XCTAssertTrue(body["description"] is NSNull)
        XCTAssertTrue(body["repeatUntil"] is NSNull)
        XCTAssertEqual(draft.event.repeatByweekday, "1,3")
        XCTAssertEqual(try JSONDecoder().decode(CalendarDraft.self, from: JSONEncoder().encode(draft)), draft)

        let allDay = CalendarDraft(title: "Trip", date: "2026-10-05", time: "07:00", allDay: true, repeatFreq: "daily",
                                   repeatByweekday: [1])
        XCTAssertNil(allDay.time)
        XCTAssertNil(allDay.repeatByweekday)
        XCTAssertNotNil(CalendarDraft(title: " ", date: "2026-10-05").problem)
        XCTAssertNotNil(CalendarDraft(title: "x", date: "2026-10-05", repeatFreq: "daily", repeatUntil: "2026-10-01").problem)
        XCTAssertNil(CalendarDraft(title: "x", date: "2026-10-05").problem)
    }

    func testEditingADraftRoundTripsTheEvent() {
        let event = CalendarEvent(id: "e1", title: "Gym", description: "legs", date: "2026-10-05", time: "07:00",
                                  endTime: "08:00", tags: ["health"], categoryTags: ["exercise"], repeatFreq: "weekly",
                                  repeatInterval: 2, repeatByweekday: "1,3", repeatUntil: "2026-12-01")
        XCTAssertEqual(CalendarDraft(editing: event).event, event)
    }

    private let gym = CalendarEvent(id: "gym", title: "Gym", date: "2026-10-01", time: "07:00", endTime: "08:00",
                                    categoryTags: ["exercise"], repeatFreq: "daily")

    private func overlay(_ changes: [CalendarChange], events: [CalendarEvent]? = nil,
                         exceptions: [CalendarException] = []) -> CalendarOverlay {
        CalendarOverlay(events: events ?? [gym], exceptions: exceptions, pending: changes.map { CalendarOp(change: $0) })
    }

    private func days(_ o: CalendarOverlay, _ start: String, _ end: String) -> [String] {
        CalendarExpansion.expand(o.events, exceptions: o.exceptions, start: start, end: end).map { "\($0.date) \($0.event.title)" }
    }

    func testPendingCreatesShowUntilTheReplicaHasThem() {
        let draft = CalendarDraft(title: "Dentist", date: "2026-10-05", time: "10:00")
        XCTAssertEqual(overlay([.create(draft)], events: []).events.map(\.title), ["Dentist"])
        let server = CalendarEvent(id: draft.id, title: "Dentist (server)", date: "2026-10-05", time: "10:00")
        let both = overlay([.create(draft)], events: [server])
        XCTAssertEqual(both.events.map(\.title), ["Dentist (server)"])
        XCTAssertEqual(both.pendingIDs, [draft.id])
    }

    func testEditAllRewritesEveryOccurrenceAndKeepsCategories() {
        var draft = CalendarDraft(editing: gym)
        draft.title = "Run"
        let o = overlay([.update(draft)])
        XCTAssertEqual(days(o, "2026-10-01", "2026-10-02"), ["2026-10-01 Run", "2026-10-02 Run"])
        XCTAssertEqual(o.events.first?.categoryTags, ["exercise"])
    }

    func testEditFromADateSplitsTheSeriesLikeTheServer() {
        var draft = CalendarDraft(editing: gym)
        draft.title = "Run"
        let skip = CalendarException(eventID: "gym", date: "2026-10-06", action: "skip")
        let o = overlay([.updateFrom(date: "2026-10-04", newID: "new", draft)], exceptions: [skip])
        XCTAssertEqual(days(o, "2026-10-02", "2026-10-07"),
                       ["2026-10-02 Gym", "2026-10-03 Gym", "2026-10-04 Run", "2026-10-05 Run", "2026-10-07 Run"])
        XCTAssertEqual(o.pendingIDs, ["gym", "new"])
        // From the very first day there's nothing earlier to keep: edited in place.
        let inPlace = overlay([.updateFrom(date: "2026-10-01", newID: "new", draft)])
        XCTAssertEqual(inPlace.events.map(\.id), ["gym"])
        XCTAssertEqual(days(inPlace, "2026-10-01", "2026-10-01"), ["2026-10-01 Run"])
        // A replay after the split landed doesn't split again.
        let landed = overlay([.updateFrom(date: "2026-10-04", newID: "new", draft)],
                             events: [gym, CalendarEvent(id: "new", title: "Run", date: "2026-10-04")])
        XCTAssertEqual(landed.events.count, 2)
        XCTAssertNil(landed.events.first?.repeatUntil)
    }

    func testDeleteSkipAndEndSeries() {
        XCTAssertEqual(days(overlay([.skip(id: "gym", date: "2026-10-02")]), "2026-10-01", "2026-10-03"),
                       ["2026-10-01 Gym", "2026-10-03 Gym"])
        XCTAssertEqual(days(overlay([.endSeries(id: "gym", date: "2026-10-03")]), "2026-10-01", "2026-10-05"),
                       ["2026-10-01 Gym", "2026-10-02 Gym"])
        XCTAssertEqual(overlay([.endSeries(id: "gym", date: "2026-10-01")]).events, [])
        XCTAssertEqual(overlay([.delete(id: "gym")], exceptions: [CalendarException(eventID: "gym", date: "2026-10-02", action: "skip")]),
                       CalendarOverlay(events: [], exceptions: [], pending: []).with(pending: ["gym"]))
    }

    func testSyncerKeepsQueueOnFailureAndDropsARefusal() async throws {
        final class Transport: CalendarTransport {
            var outcomes: [Error?]
            init(_ outcomes: [Error?]) { self.outcomes = outcomes }
            func send(_ change: CalendarChange) async throws {
                if let error = outcomes.removeFirst() { throw error }
            }
        }
        let outbox = try CalendarOutbox(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        for title in ["A", "B", "C"] { try outbox.append(.create(CalendarDraft(title: title, date: "2026-10-05"))) }
        let syncer = CalendarSyncer(outbox: outbox)
        let first = Transport([nil, TodoRefusal(status: 400, message: "bad"), URLError(.notConnectedToInternet)])
        do { _ = try await syncer.run(using: first); XCTFail("offline should stop the pass") } catch {}
        XCTAssertEqual(try outbox.list().map(\.change.subject), ["“C”"])
        let none = try await syncer.run(using: Transport([nil]))
        XCTAssertEqual(none, [])
        XCTAssertEqual(try outbox.list(), [])
        try outbox.append(.create(CalendarDraft(title: "D", date: "2026-10-05")))
        try outbox.append(.delete(id: "gone"))
        let refused = try await syncer.run(using: Transport([TodoRefusal(status: 400, message: "bad"),
                                                            TodoRefusal(status: 404, message: "Not found")]))
        // A delete of something already gone is not worth reporting.
        XCTAssertEqual(refused, ["The server didn't take “D”: bad"])
    }

    func testAnOutboxOfBareDraftsReadsAsCreates() throws {
        let outbox = try CalendarOutbox(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        let draft = CalendarDraft(title: "Dentist", date: "2026-10-05", time: "10:00")
        try JSONEncoder().encode([draft]).write(to: outbox.root.appendingPathComponent("outbox.json"))
        XCTAssertEqual(try outbox.list().map(\.change), [.create(draft)])
        try outbox.append(.delete(id: draft.id))
        XCTAssertEqual(try outbox.list().map(\.change), [.create(draft), .delete(id: draft.id)])
    }

    func testPathPartsAreOnlyIdsAndDates() {
        XCTAssertEqual(try JournalAPI.calendarPath("2026-10-05"), "2026-10-05")
        XCTAssertThrowsError(try JournalAPI.calendarPath("../settings"))
        XCTAssertThrowsError(try JournalAPI.calendarPath(""))
    }
}

private extension CalendarOverlay {
    func with(pending: Set<String>) -> CalendarOverlay { var copy = self; copy.pendingIDs = pending; return copy }
}
