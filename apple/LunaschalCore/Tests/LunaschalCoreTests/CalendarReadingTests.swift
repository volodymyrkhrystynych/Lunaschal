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

    private func span(_ id: String, _ from: Int, _ to: Int, chapter: Int = 1, fic: String = "f1",
                      title: String = "Fic") -> CalendarReadingSpan {
        CalendarReadingSpan(id: id, ficId: fic, title: title, chapterNumber: chapter,
                            startedAt: dayStart + from * 60, endedAt: dayStart + to * 60)
    }

    func testDecodesTheServersAnswer() throws {
        let json = #"[{"id":"s1","ficId":"w","title":"Worm","chapterNumber":3,"startedAt":1791180000,"endedAt":1791181800}]"#
        let spans = try JSONDecoder().decode([CalendarReadingSpan].self, from: Data(json.utf8))
        XCTAssertEqual(spans, [CalendarReadingSpan(id: "s1", ficId: "w", title: "Worm", chapterNumber: 3,
                                                   startedAt: 1_791_180_000, endedAt: 1_791_181_800)])
        // An older server, or a day cached before ficId, still decodes.
        let old = #"[{"id":"s1","title":"Worm","chapterNumber":3,"startedAt":1,"endedAt":2}]"#
        XCTAssertNil(try JSONDecoder().decode([CalendarReadingSpan].self, from: Data(old.utf8))[0].ficId)
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
            spans: [span("c", 300, 310, fic: "f3"), span("a", 100, 105, fic: "f1"), span("b", 104, 110, fic: "f2")],
            labelMinutes: 15, calendar: utc))
        XCTAssertEqual(rail.spans.map(\.id), ["a", "b", "c"])
        XCTAssertEqual(rail.spans.map(\.labelMinutes), [100, 115, 300])
    }

    func testNoRailWhenSleepComesBeforeWaking() {
        let sleep = SleepDay(date: "2026-10-05", wakeAt: dayStart + 600 * 60, sleepAt: dayStart + 500 * 60)
        XCTAssertNil(CalendarReading.rail(day: "2026-10-05", sleep: sleep, spans: [span("a", 1, 2)],
                                          labelMinutes: 15, calendar: utc))
    }

    // MARK: Sessions

    func testABingeOfOneFicIsOneSessionWithOneLabel() throws {
        let rail = try XCTUnwrap(CalendarReading.rail(
            day: "2026-10-05", sleep: nil,
            spans: [span("a", 100, 120, chapter: 12), span("b", 122, 140, chapter: 13), span("c", 150, 175, chapter: 14)],
            labelMinutes: 15, calendar: utc))
        XCTAssertEqual(rail.spans.count, 1)
        let session = rail.spans[0]
        XCTAssertEqual([session.startMinutes, session.endMinutes, session.labelMinutes], [100, 175, 100])
        XCTAssertEqual(session.segments, [100...120, 122...140, 150...175], "the breaks still show on the rail")
        XCTAssertEqual(session.session.chapterLabel, "Ch. 12–14")
        XCTAssertEqual(session.session.startedAt, dayStart + 100 * 60)
        XCTAssertEqual(session.session.endedAt, dayStart + 175 * 60)
    }

    func testAnotherFicInBetweenOrALongBreakSplitsSessions() {
        let sessions = CalendarReading.sessions([
            span("a", 100, 120, chapter: 1), span("b", 121, 130, chapter: 1, fic: "f2"),
            span("c", 131, 140, chapter: 2), span("d", 140 + 30, 180, chapter: 3),
            span("e", 180 + 31, 220, chapter: 4),
        ])
        XCTAssertEqual(sessions.map { $0.spans.map(\.id) }, [["a"], ["b"], ["c", "d"], ["e"]],
                       "30 minutes apart still joins; 31 does not")
    }

    func testWithoutAFicIdTheTitleDecidesWhatMerges() {
        let spans = [
            CalendarReadingSpan(id: "a", title: "Worm", chapterNumber: 1, startedAt: 0, endedAt: 60),
            CalendarReadingSpan(id: "b", title: "Worm", chapterNumber: 2, startedAt: 60, endedAt: 120),
            CalendarReadingSpan(id: "c", title: "Ward", chapterNumber: 1, startedAt: 120, endedAt: 180),
        ]
        XCTAssertEqual(CalendarReading.sessions(spans).map { $0.spans.map(\.id) }, [["a", "b"], ["c"]])
    }

    func testChapterLabels() {
        func label(_ chapters: [Int]) -> String {
            ReadingSession(spans: chapters.enumerated().map { span("s\($0.offset)", $0.offset, $0.offset + 1, chapter: $0.element) })
                .chapterLabel
        }
        XCTAssertEqual(label([7]), "Ch. 7")
        XCTAssertEqual(label([7, 7]), "Ch. 7", "a chapter reopened counts once")
        XCTAssertEqual(label([9, 8, 10]), "Ch. 8–10")
        XCTAssertEqual(label([3, 7]), "Ch. 3, 7")
        XCTAssertEqual(label([1, 3, 5, 9]), "4 chapters")
    }

    func testSpansAfterBedtimeAreDroppedBeforeMerging() throws {
        // Bedtime at 1000: spans after it are dropped, never merged into the day's.
        let sleep = SleepDay(date: "2026-10-05", wakeAt: nil, sleepAt: dayStart + 1000 * 60)
        let rail = try XCTUnwrap(CalendarReading.rail(
            day: "2026-10-05", sleep: sleep,
            spans: [span("a", 100, 120), span("b", 1001, 1010), span("c", 1005, 1020)],
            labelMinutes: 15, calendar: utc))
        XCTAssertEqual(rail.spans.map(\.id), ["a"])
    }

    // MARK: Label widths

    private typealias Fit = CalendarLabelFit

    func testLabelsOnDifferentRowsEachTakeWhatTheyWant() {
        let fit = Fit.widths(
            events: [.init(box: .init(top: 0, bottom: 40, preferred: 250), left: 90)],
            readings: [.init(top: 100, bottom: 140, preferred: 260)],
            obstacles: [.init(top: 0, bottom: 40, right: 80)], right: 360, readingFloor: 52)
        XCTAssertEqual(fit.events, [250])
        XCTAssertEqual(fit.readings, [260])
    }

    func testAnEventLabelNeverRunsIntoTheRail() {
        let fit = Fit.widths(events: [.init(box: .init(top: 0, bottom: 40, preferred: 500), left: 90)],
                             readings: [], obstacles: [], right: 360, readingFloor: 52)
        XCTAssertEqual(fit.events, [360.0 - 6 - 90])
    }

    func testAReadingLabelStopsShortOfAnEventLineBesideIt() {
        let fit = Fit.widths(events: [], readings: [.init(top: 0, bottom: 40, preferred: 500)],
                             obstacles: [.init(top: 20, bottom: 300, right: 80), .init(top: 50, bottom: 60, right: 200)],
                             right: 360, readingFloor: 52)
        XCTAssertEqual(fit.readings, [360.0 - 86])
    }

    func testLabelsOnOneRowShareItWithoutOverlapping() {
        func pair(_ event: Double, _ reading: Double) -> (Double, Double) {
            let fit = Fit.widths(events: [.init(box: .init(top: 0, bottom: 40, preferred: event), left: 90)],
                                 readings: [.init(top: 10, bottom: 50, preferred: reading)],
                                 obstacles: [], right: 360, readingFloor: 52)
            return (fit.events[0], fit.readings[0])
        }
        let room = 360.0 - 90 - 6
        XCTAssertTrue(pair(100, 120) == (100, 120), "both fit")
        XCTAssertTrue(pair(60, 400) == (60, room - 60), "the short one keeps its width, the long one takes the rest")
        XCTAssertTrue(pair(400, 80) == (room - 80, 80))
        XCTAssertTrue(pair(400, 400) == (room / 2, room / 2))
    }

    func testEveryLabelKeepsTheNarrowestShareItIsLeft() {
        // One reading label level with two events: it must fit beside both.
        let events: [Fit.EventLabel] = [.init(box: .init(top: 0, bottom: 40, preferred: 300), left: 90),
                                        .init(box: .init(top: 30, bottom: 70, preferred: 50), left: 124)]
        let fit = Fit.widths(events: events, readings: [.init(top: 20, bottom: 60, preferred: 300)],
                             obstacles: [], right: 360, readingFloor: 52)
        for (event, width) in zip(events, fit.events) {
            XCTAssertLessThanOrEqual(event.left + width + Fit.gap, 360 - fit.readings[0] + 0.001)
        }
    }
}
