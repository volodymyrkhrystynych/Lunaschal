import Foundation

/// One day's wake and sleep, as `GET /api/calendar/sleep/<date>` answers.
/// Times are unix seconds: a 01:30 bedtime belongs to the next calendar date,
/// and an instant is the one form that survives that.
public struct SleepDay: Codable, Equatable {
    public let date: String
    public var wakeAt: Int?
    public var sleepAt: Int?
    public var wakeSource: String?
    public var sleepSource: String?
    /// The far ends of the two nights touching this day, from its neighbours.
    public var previousSleepAt: Int?
    public var nextWakeAt: Int?

    public init(date: String, wakeAt: Int? = nil, sleepAt: Int? = nil, wakeSource: String? = nil,
                sleepSource: String? = nil, previousSleepAt: Int? = nil, nextWakeAt: Int? = nil) {
        self.date = date; self.wakeAt = wakeAt; self.sleepAt = sleepAt; self.wakeSource = wakeSource
        self.sleepSource = sleepSource; self.previousSleepAt = previousSleepAt; self.nextWakeAt = nextWakeAt
    }
}

/// An asleep span on the day view, in its offset minutes.
public struct SleepBand: Equatable, Identifiable {
    public enum Kind: String { case morning, evening }
    public let kind: Kind
    public let startMinutes: Int
    public let endMinutes: Int
    public let label: String
    public var id: String { kind.rawValue }
}

/// `src/lib/sleep.ts` and `backend/sleep.py`'s clock arithmetic, natively.
public enum CalendarSleep {
    /// The 4am start of a day key's window, in unix seconds.
    public static func dayStart(_ day: String, calendar: Calendar = .current) -> Int? {
        let parts = day.split(separator: "-").compactMap { Int($0) }
        guard CivilDate(day) != nil, parts.count == 3,
              let date = calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2],
                                                            hour: DayKey.rolloverHour)) else { return nil }
        return Int(date.timeIntervalSince1970)
    }

    /// 'HH:MM' → the instant it names inside `day`'s window: before 4am is the
    /// next calendar date, as the server's `time_to_timestamp` reads it.
    public static func timestamp(day: String, clock: String, calendar: Calendar = .current) -> Int? {
        guard let start = dayStart(day, calendar: calendar), let wall = CalendarTimeline.minutes(clock) else { return nil }
        return start + CalendarTimeline.offset(fromWall: wall) * 60
    }

    public static func clock(_ timestamp: Int, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.hour, .minute], from: Date(timeIntervalSince1970: TimeInterval(timestamp)))
        return CalendarTimeline.time((parts.hour ?? 0) * 60 + (parts.minute ?? 0))
    }

    /// The asleep spans to shade on `day`'s timeline. A band is drawn only
    /// from an end someone knows: an unknown far end falls back to the edge
    /// of the window, an unknown near end draws nothing.
    public static func bands(_ sleep: SleepDay, calendar: Calendar = .current) -> [SleepBand] {
        guard let start = dayStart(sleep.date, calendar: calendar) else { return [] }
        let minutes = { (ts: Int) in (ts - start) / 60 }
        let clamp = { (m: Int) in max(0, min(CalendarTimeline.minutesPerDay, m)) }
        var bands: [SleepBand] = []
        if let wake = sleep.wakeAt {
            let end = minutes(wake)
            let from = sleep.previousSleepAt.map(minutes) ?? 0
            if end > 0, end > from {
                bands.append(SleepBand(kind: .morning, startMinutes: clamp(from), endMinutes: clamp(end),
                                       label: "asleep · woke \(clock(wake, calendar: calendar))"))
            }
        }
        if let bed = sleep.sleepAt {
            let from = minutes(bed)
            let end = sleep.nextWakeAt.map(minutes) ?? CalendarTimeline.minutesPerDay
            if from < CalendarTimeline.minutesPerDay, end > from {
                bands.append(SleepBand(kind: .evening, startMinutes: clamp(from), endMinutes: clamp(end),
                                       label: "asleep from \(clock(bed, calendar: calendar))"))
            }
        }
        return bands
    }

    /// The cached days with the queued hand-set times applied, the way the
    /// server will apply them — neighbours included, since a day's bedtime is
    /// the next day's `previousSleepAt`. An end handed back to the server
    /// keeps whatever derived value was cached, or none.
    public static func overlay(_ cache: [String: SleepDay], pending ops: [CalendarOp],
                               calendar: Calendar = .current) -> [String: SleepDay] {
        var days = cache
        for op in ops {
            guard case let .sleep(date, wake, sleep) = op.change else { continue }
            var day = days[date] ?? SleepDay(date: date)
            day.wakeAt = wake.flatMap { timestamp(day: date, clock: $0, calendar: calendar) }
                ?? (day.wakeSource == "auto" ? day.wakeAt : nil)
            day.wakeSource = wake != nil ? "manual" : day.wakeAt == nil ? nil : "auto"
            day.sleepAt = sleep.flatMap { timestamp(day: date, clock: $0, calendar: calendar) }
                ?? (day.sleepSource == "auto" ? day.sleepAt : nil)
            day.sleepSource = sleep != nil ? "manual" : day.sleepAt == nil ? nil : "auto"
            days[date] = day
            let before = CalendarTimeline.addingDays(-1, to: date), after = CalendarTimeline.addingDays(1, to: date)
            days[before]?.nextWakeAt = day.wakeAt
            days[after]?.previousSleepAt = day.sleepAt
        }
        return days
    }
}
