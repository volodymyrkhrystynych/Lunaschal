import Foundation

/// One stretch of `GET /api/calendar/activity/<date>`: work, leisure or mix,
/// clipped to the 4am day and to waking hours, with work that overlapped
/// reading already turned to mix by the server. Unix seconds.
public struct CalendarActivitySpan: Codable, Equatable, Identifiable {
    public let id: String
    public let state: ActivityState
    public let startedAt: Int
    public let endedAt: Int

    public init(id: String, state: ActivityState, startedAt: Int, endedAt: Int) {
        self.id = id; self.state = state; self.startedAt = startedAt; self.endedAt = endedAt
    }
}

/// The activity rail, in offset minutes from 4am: the same bounds as the
/// reading rail beside it, so the two start and stop together.
public struct ActivityRail: Equatable {
    public struct Segment: Equatable, Identifiable {
        public let id: String
        public let state: ActivityState
        public let minutes: ClosedRange<Int>
    }

    public let startMinutes: Int
    public let endMinutes: Int
    public let segments: [Segment]
}

public enum CalendarActivity {
    /// Nil when there is nothing to draw: no span inside waking hours, or a
    /// sleep that comes before the wake.
    public static func rail(day: String, sleep: SleepDay?, spans: [CalendarActivitySpan],
                            calendar: Calendar = .current) -> ActivityRail? {
        guard let dayStart = CalendarSleep.dayStart(day, calendar: calendar) else { return nil }
        let minutes = { (ts: Int) in (ts - dayStart) / 60 }
        let clamp = { (m: Int) in max(0, min(CalendarTimeline.minutesPerDay, m)) }
        let start = clamp(sleep?.wakeAt.map(minutes) ?? 0)
        let end = clamp(sleep?.sleepAt.map(minutes) ?? CalendarTimeline.minutesPerDay)
        guard end > start else { return nil }
        let segments = spans.compactMap { span -> ActivityRail.Segment? in
            let from = max(start, minutes(span.startedAt)), to = min(end, minutes(span.endedAt))
            return to > from ? .init(id: span.id, state: span.state, minutes: from...to) : nil
        }
        return segments.isEmpty ? nil : ActivityRail(startMinutes: start, endMinutes: end, segments: segments)
    }
}
