import Foundation
import LunaschalCore

#if DEBUG
/// Opt-in offline UI fixture: two read chapters on today's day view, near
/// now so the timeline opens on them, matching NewspaperFixture's pattern.
enum CalendarReadingFixture {
    static let argument = "-calendarReadingFixture"

    static func spans(day: String) -> [CalendarReadingSpan]? {
        guard ProcessInfo.processInfo.arguments.contains(argument), day == DayKey.of(Date()),
              let dayStart = CalendarSleep.dayStart(day) else { return nil }
        let first = max(dayStart + 60, Int(Date().timeIntervalSince1970) - 3600)
        return [
            CalendarReadingSpan(id: "fixture-1", title: "Worm", chapterNumber: 12, startedAt: first, endedAt: first + 20 * 60),
            CalendarReadingSpan(id: "fixture-2", title: "Worm", chapterNumber: 13, startedAt: first + 22 * 60, endedAt: first + 40 * 60),
        ]
    }
}
#endif
