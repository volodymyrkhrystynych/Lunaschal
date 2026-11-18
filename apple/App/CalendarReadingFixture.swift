import Foundation
import LunaschalCore

#if DEBUG
/// Opt-in offline UI fixture on today's day view, near now so the timeline
/// opens on it, matching NewspaperFixture's pattern: two chapters of one fic
/// read back to back (one session, one label), then a fic with a forum-length
/// title that only fits in full in its popover.
enum CalendarReadingFixture {
    static let argument = "-calendarReadingFixture"
    static let longTitle = "The Long Way Round (Worm/Pact crossover, Taylor-centric, slow burn) [Thread 2]"

    static func spans(day: String) -> [CalendarReadingSpan]? {
        guard ProcessInfo.processInfo.arguments.contains(argument), day == DayKey.of(Date()),
              let dayStart = CalendarSleep.dayStart(day) else { return nil }
        let first = max(dayStart + 60, Int(Date().timeIntervalSince1970) - 3600)
        return [
            CalendarReadingSpan(id: "fixture-1", ficId: "worm", title: "Worm", chapterNumber: 12,
                                startedAt: first, endedAt: first + 20 * 60),
            CalendarReadingSpan(id: "fixture-2", ficId: "worm", title: "Worm", chapterNumber: 13,
                                startedAt: first + 22 * 60, endedAt: first + 40 * 60),
            CalendarReadingSpan(id: "fixture-3", ficId: "long", title: longTitle, chapterNumber: 4,
                                startedAt: first + 45 * 60, endedAt: first + 70 * 60),
        ]
    }

    /// The activity rail beside it: work up to the reading, mix while the
    /// first fic was read (the server's work-during-reading rule), then leisure.
    static func activity(day: String) -> [CalendarActivitySpan]? {
        guard let reading = spans(day: day) else { return nil }
        let start = reading[0].startedAt, mixEnd = reading[1].endedAt
        return [
            CalendarActivitySpan(id: "fixture-work", state: .work, startedAt: start - 90 * 60, endedAt: start),
            CalendarActivitySpan(id: "fixture-mix", state: .mix, startedAt: start, endedAt: mixEnd),
            CalendarActivitySpan(id: "fixture-leisure", state: .leisure, startedAt: mixEnd,
                                 endedAt: reading[2].endedAt + 30 * 60),
        ]
    }
}
#endif
