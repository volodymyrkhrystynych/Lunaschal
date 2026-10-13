import Foundation

/// One stretch of in-app reading on a day, as `GET /api/calendar/reading/<date>`
/// answers: already clipped to the 4am day by the server. Unix seconds.
public struct CalendarReadingSpan: Codable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let chapterNumber: Int
    public let startedAt: Int
    public let endedAt: Int

    public init(id: String, title: String, chapterNumber: Int, startedAt: Int, endedAt: Int) {
        self.id = id; self.title = title; self.chapterNumber = chapterNumber
        self.startedAt = startedAt; self.endedAt = endedAt
    }
}

/// A span placed on the day view, in offset minutes. `labelMinutes` is where
/// its label sits, pushed down past the one before it when two short
/// chapters are closer together than a label is tall.
public struct PlacedReading: Equatable, Identifiable {
    public let span: CalendarReadingSpan
    public let startMinutes: Int
    public let endMinutes: Int
    public let labelMinutes: Int
    public var id: String { span.id }
}

/// The reading rail: it runs from waking to sleep (the day's edges when
/// either is unknown) with the read stretches filled in.
public struct ReadingRail: Equatable {
    public let startMinutes: Int
    public let endMinutes: Int
    public let spans: [PlacedReading]
}

/// `src/lib/calendarReading.ts`'s `readingLayout`, natively; keep the two in step.
public enum CalendarReading {
    /// `labelMinutes` is one label's height in timeline minutes.
    public static func rail(day: String, sleep: SleepDay?, spans: [CalendarReadingSpan],
                            labelMinutes: Int, calendar: Calendar = .current) -> ReadingRail? {
        guard let dayStart = CalendarSleep.dayStart(day, calendar: calendar) else { return nil }
        let minutes = { (ts: Int) in (ts - dayStart) / 60 }
        let clamp = { (m: Int) in max(0, min(CalendarTimeline.minutesPerDay, m)) }
        let start = clamp(sleep?.wakeAt.map(minutes) ?? 0)
        let end = clamp(sleep?.sleepAt.map(minutes) ?? CalendarTimeline.minutesPerDay)
        guard end > start else { return nil }
        var nextLabel = 0
        let placed = spans.sorted { ($0.startedAt, $0.id) < ($1.startedAt, $1.id) }.compactMap { span -> PlacedReading? in
            let from = max(start, minutes(span.startedAt)), to = min(end, minutes(span.endedAt))
            guard to > from else { return nil }
            let label = max(from, nextLabel)
            nextLabel = label + labelMinutes
            return PlacedReading(span: span, startMinutes: from, endMinutes: to, labelMinutes: label)
        }
        return ReadingRail(startMinutes: start, endMinutes: end, spans: placed)
    }
}
