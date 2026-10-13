import XCTest
@testable import LunaschalCore

final class CalendarReadingTests: XCTestCase {
    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    /// 2026-10-05 04:00 UTC, the start of that day's window.
    private let dayStart = 1_791_172_800

    private func span(_ id: String, _ from: Int, _ to: Int, chapter: Int = 1) -> CalendarReadingSpan {
        CalendarReadingSpan(id: id, title: "Fic", chapterNumber: chapter,
                            startedAt: dayStart + from * 60, endedAt: dayStart + to * 60)
    }

    func testDecodesTheServersAnswer() throws {
        let json = #"[{"id":"s1","title":"Worm","chapterNumber":3,"startedAt":1791180000,"endedAt":1791181800}]"#
        let spans = try JSONDecoder().decode([CalendarReadingSpan].self, from: Data(json.utf8))
        XCTAssertEqual(spans, [CalendarReadingSpan(id: "s1", title: "Worm", chapterNumber: 3,
                                                   startedAt: 1_791_180_000, endedAt: 1_791_181_800)])
    }

    func testUnknownWakeAndSleepRunTheRailEdgeToEdge() throws {
        XCTAssertEqual(CalendarSleep.dayStart("2026-10-05", calendar: utc), dayStart)
        let rail = try XCTUnwrap(CalendarReading.rail(day: "2026-10-05", sleep: nil, spans: [span("a", 120, 150)],
                                                     labelMinutes: 15, calendar: utc))
        XCTAssertEqual(rail.startMinutes, 0)
        XCTAssertEqual(rail.endMinutes, CalendarTimeline.minutesPerDay)
        XCTAssertEqual(rail.spans.map { [$0.startMinutes, $0.endMinutes, $0.labelMinutes] }, [[120, 150, 120]])
    }

    func testKnownWakeAndSleepTrimTheRailAndItsSpans() throws {
        let sleep = SleepDay(date: "2026-10-05", wakeAt: dayStart + 180 * 60, sleepAt: dayStart + 1200 * 60)
        let rail = try XCTUnwrap(CalendarReading.rail(
            day: "2026-10-05", sleep: sleep,
            spans: [span("early", 100, 200), span("asleep", 1210, 1230), span("late", 1190, 1250)],
            labelMinutes: 15, calendar: utc))
        XCTAssertEqual([rail.startMinutes, rail.endMinutes], [180, 1200])
        XCTAssertEqual(rail.spans.map(\.id), ["early", "late"], "a span wholly after bedtime is dropped")
        XCTAssertEqual(rail.spans.map { [$0.startMinutes, $0.endMinutes] }, [[180, 200], [1190, 1200]])
    }

    func testCloseSpansPushTheirLabelsApartInStartOrder() throws {
        let rail = try XCTUnwrap(CalendarReading.rail(
            day: "2026-10-05", sleep: nil,
            spans: [span("c", 300, 310), span("a", 100, 105), span("b", 104, 110)],
            labelMinutes: 15, calendar: utc))
        XCTAssertEqual(rail.spans.map(\.id), ["a", "b", "c"])
        XCTAssertEqual(rail.spans.map(\.labelMinutes), [100, 115, 300])
    }

    func testNoRailWhenSleepComesBeforeWaking() {
        let sleep = SleepDay(date: "2026-10-05", wakeAt: dayStart + 600 * 60, sleepAt: dayStart + 500 * 60)
        XCTAssertNil(CalendarReading.rail(day: "2026-10-05", sleep: sleep, spans: [span("a", 1, 2)],
                                          labelMinutes: 15, calendar: utc))
    }
}
