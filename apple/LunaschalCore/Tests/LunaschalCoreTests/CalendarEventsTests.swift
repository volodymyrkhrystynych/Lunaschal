import XCTest
@testable import LunaschalCore

final class CalendarEventsTests: XCTestCase {
    private func event(_ date: String, freq: String? = nil, interval: Int = 1, byweekday: String? = nil,
                       until: String? = nil, time: String? = nil) -> CalendarEvent {
        CalendarEvent(id: "01J0000000000000000000000A", title: "Event", date: date, time: time,
                      repeatFreq: freq, repeatInterval: interval, repeatByweekday: byweekday, repeatUntil: until)
    }

    func testCivilDateRoundTripsAndNumbersWeekdaysFromSunday() {
        XCTAssertEqual(CivilDate("2026-10-05")?.iso, "2026-10-05")
        XCTAssertEqual(CivilDate("2026-10-04")?.weekday, 0) // Sunday
        XCTAssertEqual(CivilDate("2026-10-05")?.weekday, 1)
        XCTAssertEqual(CivilDate("1969-12-31")?.weekday, 3)
        XCTAssertNil(CivilDate("2026-02-30"))
        XCTAssertNil(CivilDate("garbage"))
        XCTAssertEqual(CivilDate("2024-02-29")?.adding(days: 1).iso, "2024-03-01")
    }

    func testOneOffOnlyInsideTheWindow() {
        XCTAssertEqual(CalendarExpansion.occurrenceDates(event("2026-10-05"), start: "2026-10-01", end: "2026-10-31"), ["2026-10-05"])
        XCTAssertEqual(CalendarExpansion.occurrenceDates(event("2026-09-05"), start: "2026-10-01", end: "2026-10-31"), [])
    }

    func testDailyWithIntervalStaysOnCycleFromTheAnchor() {
        let dates = CalendarExpansion.occurrenceDates(event("2026-10-01", freq: "daily", interval: 3),
                                                      start: "2026-10-05", end: "2026-10-12")
        XCTAssertEqual(dates, ["2026-10-07", "2026-10-10"])
    }

    func testWeeklyUsesWeekdaysAndUntil() {
        let dates = CalendarExpansion.occurrenceDates(
            event("2026-10-05", freq: "weekly", byweekday: "1,3", until: "2026-10-14"),
            start: "2026-10-01", end: "2026-10-31")
        XCTAssertEqual(dates, ["2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14"])
    }

    func testWeeklyWithoutWeekdaysFallsBackToTheAnchorsDay() {
        let dates = CalendarExpansion.occurrenceDates(event("2026-10-06", freq: "weekly", interval: 2),
                                                      start: "2026-10-01", end: "2026-11-05")
        XCTAssertEqual(dates, ["2026-10-06", "2026-10-20", "2026-11-03"])
    }

    func testMonthlyAndYearlyClampRatherThanSkip() {
        XCTAssertEqual(CalendarExpansion.occurrenceDates(event("2026-01-31", freq: "monthly"),
                                                         start: "2026-02-01", end: "2026-04-30"),
                       ["2026-02-28", "2026-03-31", "2026-04-30"])
        XCTAssertEqual(CalendarExpansion.occurrenceDates(event("2024-02-29", freq: "yearly"),
                                                         start: "2025-01-01", end: "2028-12-31"),
                       ["2025-02-28", "2026-02-28", "2027-02-28", "2028-02-29"])
    }

    func testExpandAppliesSkipsAndMovesIncludingOneMovedIntoTheWindow() {
        let series = event("2026-10-05", freq: "daily", time: "09:00")
        let exceptions = [
            CalendarException(eventID: series.id, date: "2026-10-06", action: "skip"),
            CalendarException(eventID: series.id, date: "2026-10-04", action: "move", newDate: "2026-10-07", newTime: "18:00"),
            CalendarException(eventID: series.id, date: "2026-10-05", action: "move", newDate: "2026-10-09"),
        ]
        let day = CalendarExpansion.expand([series], exceptions: exceptions, start: "2026-10-07", end: "2026-10-07")
        XCTAssertEqual(day.map(\.time), ["09:00"])
        XCTAssertEqual(day.map(\.occurrenceDate), ["2026-10-07"])
        let all = CalendarExpansion.expand([series], exceptions: exceptions, start: "2026-10-05", end: "2026-10-07")
        // The 4th is before the anchor, so its move never fires; the 5th moved out; the 6th is skipped.
        XCTAssertEqual(all.map(\.date), ["2026-10-07"])
    }

    func testExpandSortsByDateThenTimeWithUntimedFirst() {
        let a = CalendarEvent(id: "a", title: "Late", date: "2026-10-05", time: "20:00")
        let b = CalendarEvent(id: "b", title: "All day", date: "2026-10-05", allDay: true)
        let c = CalendarEvent(id: "c", title: "Early", date: "2026-10-05", time: "07:30")
        let out = CalendarExpansion.expand([a, b, c], exceptions: [], start: "2026-10-05", end: "2026-10-05")
        XCTAssertEqual(out.map(\.event.title), ["All day", "Early", "Late"])
    }

    func testDecodesReplicatedRows() {
        let record = SyncChange(revision: 4, collection: "calendar_events", id: "e1", deleted: false, data: [
            "id": .string("e1"), "title": .string("Gym"), "date": .string("2026-10-05"),
            "time": .string("07:00"), "endTime": .string("08:00"), "allDay": .number(0),
            "tags": .string("[\"health\"]"), "categoryTags": .null, "repeatFreq": .string("weekly"),
            "repeatInterval": .number(2), "repeatByweekday": .string("1,3"), "repeatUntil": .null,
        ])
        let decoded = CalendarEvent(record: record)
        XCTAssertEqual(decoded?.tags, ["health"])
        XCTAssertEqual(decoded?.categoryTags, [])
        XCTAssertEqual(decoded?.repeatInterval, 2)
        XCTAssertEqual(decoded?.isSeries, true)
        let skip = SyncChange(revision: 5, collection: "calendar_event_exceptions", id: "x1", deleted: false, data: [
            "id": .string("x1"), "eventId": .string("e1"), "date": .string("2026-10-07"), "action": .string("skip"),
        ])
        XCTAssertEqual(CalendarException(record: skip)?.eventID, "e1")
        XCTAssertNil(CalendarEvent(record: skip))
    }

    func testCalendarSyncsOnlyAgainstAServerThatListsBothTables() {
        XCTAssertFalse(CalendarSync.supported(by: ["journal_entries", "calendar_events"]))
        XCTAssertTrue(CalendarSync.supported(by: ["journal_entries", "calendar_events", "calendar_event_exceptions"]))
    }
}
