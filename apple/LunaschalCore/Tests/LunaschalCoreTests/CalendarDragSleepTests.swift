import XCTest
@testable import LunaschalCore

final class CalendarDragSleepTests: XCTestCase {
    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    private func item(_ event: CalendarEvent, day: String = "2026-10-05") -> TimedOccurrence {
        CalendarTimeline.plan(day: day, events: [event], exceptions: []).timed[0]
    }

    // MARK: categories

    func testCategoriesAreSentOnlyWhenTickedAndKeepTheirOrder() throws {
        let untouched = try JSONSerialization.jsonObject(with: JSONEncoder().encode(
            CalendarDraft(title: "Walk", date: "2026-10-05"))) as! [String: Any]
        XCTAssertNil(untouched["categoryTags"], "left to the classifier")
        let picked = CalendarDraft(title: "Walk", date: "2026-10-05", categoryTags: ["outside", "nonsense", "leisure"])
        XCTAssertEqual(picked.categoryTags, ["leisure", "outside"])
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(picked)) as! [String: Any]
        XCTAssertEqual(body["categoryTags"] as? [String], ["leisure", "outside"])
        let cleared = try JSONSerialization.jsonObject(with: JSONEncoder().encode(
            CalendarDraft(title: "Walk", date: "2026-10-05", categoryTags: []))) as! [String: Any]
        XCTAssertEqual(cleared["categoryTags"] as? [String], [])
    }

    func testAnEditShowsItsCategoriesAndOneWithoutKeepsTheClassifiers() {
        let gym = CalendarEvent(id: "gym", title: "Gym", date: "2026-10-01", time: "07:00", categoryTags: ["exercise"])
        var draft = CalendarDraft(editing: gym)
        draft.categoryTags = ["exercise", "outside"]
        XCTAssertEqual(CalendarOverlay(events: [gym], exceptions: [], pending: [CalendarOp(change: .update(draft))])
            .events.first?.categoryTags, ["exercise", "outside"])
        draft.categoryTags = nil
        XCTAssertEqual(CalendarOverlay(events: [gym], exceptions: [], pending: [CalendarOp(change: .update(draft))])
            .events.first?.categoryTags, ["exercise"])
    }

    func testCategoriesTickedOnTheEventPageApplyAtOnceAndPatchOnlyThem() throws {
        let gym = CalendarEvent(id: "gym", title: "Gym", date: "2026-10-01", time: "07:00", categoryTags: ["exercise"],
                                repeatFreq: "daily")
        let change = CalendarChange.categories(id: "gym", tags: ["outside", "exercise"])
        let shown = CalendarOverlay(events: [gym], exceptions: [], pending: [CalendarOp(change: change)])
        XCTAssertEqual(shown.events.first?.categoryTags, ["exercise", "outside"])
        XCTAssertEqual(shown.pendingIDs, ["gym"])
        let route = try JournalAPI.calendarRoute(change)
        XCTAssertEqual("\(route.method) \(route.path)", "PATCH api/calendar/gym")
        let body = try JSONSerialization.jsonObject(with: route.body!) as! [String: Any]
        XCTAssertEqual(body.keys.sorted(), ["categoryTags"], "nothing else about the event is touched")
        XCTAssertEqual(body["categoryTags"] as? [String], ["exercise", "outside"])
        let cleared = CalendarOverlay(events: [gym], exceptions: [],
                                      pending: [CalendarOp(change: .categories(id: "gym", tags: []))])
        XCTAssertEqual(cleared.events.first?.categoryTags, [])
    }

    // MARK: overlaps

    func testOverlappingEventsShareTheirHoursInSeparateLanes() {
        let events = [
            CalendarEvent(id: "long", title: "Long", date: "2026-10-05", time: "17:00", endTime: "19:00"),
            CalendarEvent(id: "short", title: "Short", date: "2026-10-05", time: "18:00", endTime: "19:00"),
        ]
        let plan = CalendarTimeline.plan(day: "2026-10-05", events: events, exceptions: [])
        let byID = Dictionary(uniqueKeysWithValues: plan.timed.map { ($0.occurrence.event.id, $0) })
        XCTAssertEqual(byID["long"]?.startMinutes, 13 * 60)
        XCTAssertEqual(byID["short"]?.startMinutes, 14 * 60)
        XCTAssertEqual(byID["long"]?.endMinutes, byID["short"]?.endMinutes)
        XCTAssertEqual(byID["long"]?.lane, 0)
        XCTAssertEqual(byID["short"]?.lane, 1)
        // Both labels clear of both lines, and not on top of each other.
        XCTAssertEqual(byID["long"]?.labelLane, 1)
        XCTAssertEqual(byID["short"]?.labelLane, 1)
        // Read top to bottom, the labels follow the lines left to right.
        XCTAssertEqual(byID["long"]?.labelRow, 1)
        XCTAssertEqual(byID["short"]?.labelRow, 0)
    }

    func testBackToBackEventsKeepTheirOwnLabels() {
        let events = (0..<3).map { i in
            CalendarEvent(id: "e\(i)", title: "", date: "2026-10-05", time: CalendarTimeline.time(17 * 60 + 30 * i),
                          endTime: CalendarTimeline.time(17 * 60 + 30 * (i + 1)))
        }
        let plan = CalendarTimeline.plan(day: "2026-10-05", events: events, exceptions: [])
        XCTAssertEqual(plan.timed.map(\.lane), [0, 0, 0])
        XCTAssertEqual(plan.timed.map(\.labelLane), [0, 0, 0])
        XCTAssertEqual(plan.timed.map(\.labelRow), [0, 0, 0])
    }

    // MARK: dragging

    func testMoveKeepsTheLengthAndStaysInsideTheDay() {
        XCTAssertTrue(CalendarTimeline.moved(start: 60, end: 120, by: 30) == (90, 150))
        XCTAssertTrue(CalendarTimeline.moved(start: 60, end: 120, by: -500) == (0, 60))
        XCTAssertTrue(CalendarTimeline.moved(start: 1380, end: 1420, by: 100) == (1400, 1440))
        XCTAssertEqual(CalendarTimeline.snapped(12.4), 10)
        XCTAssertEqual(CalendarTimeline.snapped(-13), -15)
    }

    func testResizeMovesOnlyTheEndAndKeepsAMinimum() {
        XCTAssertTrue(CalendarTimeline.resized(start: 60, end: 120, by: 45) == (60, 165))
        XCTAssertTrue(CalendarTimeline.resized(start: 60, end: 120, by: -200) == (60, 75))
        XCTAssertTrue(CalendarTimeline.resized(start: 1400, end: 1430, by: 60) == (1400, 1440))
    }

    func testAOneOffDraggedPastMidnightMovesToTheNextDate() {
        let late = item(CalendarEvent(id: "e", title: "Film", date: "2026-10-05", time: "22:00", endTime: "23:30"))
        XCTAssertNil(CalendarTimeline.reschedule(late, day: "2026-10-05", start: late.startMinutes, end: late.endMinutes))
        let (start, end) = CalendarTimeline.moved(start: late.startMinutes, end: late.endMinutes, by: 150)
        let change = CalendarTimeline.reschedule(late, day: "2026-10-05", start: start, end: end)
        XCTAssertEqual(change, .reschedule(id: "e", occurrence: nil, date: "2026-10-06", time: "00:30", endTime: "02:00"))
        let shown = CalendarOverlay(events: [late.occurrence.event], exceptions: [], pending: [CalendarOp(change: change!)])
        let plan = CalendarTimeline.plan(day: "2026-10-05", events: shown.events, exceptions: [])
        XCTAssertEqual(plan.timed.map(\.startMinutes), [20 * 60 + 30])
        XCTAssertEqual(shown.pendingIDs, ["e"])
    }

    func testDraggingOneOccurrenceMovesOnlyThatOne() {
        let gym = CalendarEvent(id: "gym", title: "Gym", date: "2026-10-01", time: "07:00", endTime: "08:00",
                                repeatFreq: "daily")
        let today = item(gym)
        let (start, end) = CalendarTimeline.resized(start: today.startMinutes, end: today.endMinutes, by: 30)
        let change = CalendarTimeline.reschedule(today, day: "2026-10-05", start: start, end: end)!
        XCTAssertEqual(change, .reschedule(id: "gym", occurrence: "2026-10-05", date: "2026-10-05",
                                           time: "07:00", endTime: "08:30"))
        let shown = CalendarOverlay(events: [gym], exceptions: [], pending: [CalendarOp(change: change)])
        let all = CalendarExpansion.expand(shown.events, exceptions: shown.exceptions, start: "2026-10-04", end: "2026-10-06")
        XCTAssertEqual(all.map { "\($0.date) \($0.endTime ?? "")" }, ["2026-10-04 08:00", "2026-10-05 08:30", "2026-10-06 08:00"])
    }

    func testRescheduleAndSleepRouteAsTheWebSendsThem() throws {
        func route(_ change: CalendarChange) throws -> (String, [String: Any]) {
            let r = try JournalAPI.calendarRoute(change)
            let body = try r.body.map { try JSONSerialization.jsonObject(with: $0) as! [String: Any] } ?? [:]
            return ("\(r.method) \(r.path)", body)
        }
        let same = try route(.reschedule(id: "gym", occurrence: "2026-10-05", date: "2026-10-05", time: "07:00", endTime: "08:30"))
        XCTAssertEqual(same.0, "PATCH api/calendar/gym/occurrence/2026-10-05")
        XCTAssertNil(same.1["newDate"])
        XCTAssertEqual(same.1["newEndTime"] as? String, "08:30")
        let crossed = try route(.reschedule(id: "gym", occurrence: "2026-10-05", date: "2026-10-06", time: "00:30", endTime: "01:00"))
        XCTAssertEqual(crossed.1["newDate"] as? String, "2026-10-06")
        let oneOff = try route(.reschedule(id: "e", occurrence: nil, date: "2026-10-06", time: "00:30", endTime: "02:00"))
        XCTAssertEqual(oneOff.0, "PATCH api/calendar/e")
        XCTAssertEqual(oneOff.1 as? [String: String], ["date": "2026-10-06", "time": "00:30", "endTime": "02:00"])
        let sleep = try route(.sleep(date: "2026-10-05", wake: "07:15", sleep: nil))
        XCTAssertEqual(sleep.0, "PUT api/calendar/sleep/2026-10-05")
        XCTAssertEqual(sleep.1["wake"] as? String, "07:15")
        XCTAssertTrue(sleep.1["sleep"] is NSNull)
        XCTAssertThrowsError(try JournalAPI.calendarRoute(.sleep(date: "../x", wake: nil, sleep: nil)))
    }

    // MARK: sleep

    func testBandsShadeTheMorningAndTheNightLikeTheWeb() throws {
        let start = try XCTUnwrap(CalendarSleep.dayStart("2026-10-05", calendar: utc))
        let day = SleepDay(date: "2026-10-05", wakeAt: start + 3 * 3600 + 20 * 60, sleepAt: start + 21 * 3600 + 30 * 60,
                           previousSleepAt: start - 3 * 3600, nextWakeAt: nil)
        XCTAssertEqual(CalendarSleep.bands(day, calendar: utc), [
            SleepBand(kind: .morning, startMinutes: 0, endMinutes: 200, label: "asleep · woke 07:20"),
            SleepBand(kind: .evening, startMinutes: 1290, endMinutes: 1440, label: "asleep from 01:30"),
        ])
        // No wake and no bedtime known: nothing is claimed.
        XCTAssertEqual(CalendarSleep.bands(SleepDay(date: "2026-10-05"), calendar: utc), [])
        XCTAssertEqual(CalendarSleep.timestamp(day: "2026-10-05", clock: "01:30", calendar: utc), start + 1290 * 60)
        XCTAssertEqual(CalendarSleep.timestamp(day: "2026-10-05", clock: "07:20", calendar: utc), start + 200 * 60)
    }

    func testQueuedSleepTimesShowAtOnceAndReachTheNeighbours() throws {
        let start = try XCTUnwrap(CalendarSleep.dayStart("2026-10-05", calendar: utc))
        let cache = [
            "2026-10-05": SleepDay(date: "2026-10-05", wakeAt: start + 3600, sleepAt: start + 18 * 3600,
                                   wakeSource: "auto", sleepSource: "auto"),
            "2026-10-06": SleepDay(date: "2026-10-06"),
            "2026-10-04": SleepDay(date: "2026-10-04"),
        ]
        let shown = CalendarSleep.overlay(cache, pending: [CalendarOp(change: .sleep(date: "2026-10-05", wake: "07:00", sleep: nil))],
                                          calendar: utc)
        XCTAssertEqual(shown["2026-10-05"]?.wakeAt, start + 3 * 3600)
        XCTAssertEqual(shown["2026-10-05"]?.wakeSource, "manual")
        XCTAssertEqual(shown["2026-10-05"]?.sleepAt, start + 18 * 3600, "an end handed back keeps the derived value")
        XCTAssertEqual(shown["2026-10-04"]?.nextWakeAt, start + 3 * 3600)
        XCTAssertEqual(shown["2026-10-06"]?.previousSleepAt, start + 18 * 3600)
    }
}
