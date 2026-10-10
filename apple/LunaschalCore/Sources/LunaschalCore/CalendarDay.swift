import Foundation

/// Drag editing is an explicit choice for this calendar visit, not a saved
/// preference. Off produces no preview or schedule change.
public enum CalendarDragMode: String {
    case off = "Off", move = "Move", resize = "Resize"

    public var next: CalendarDragMode {
        switch self {
        case .off: return .move
        case .move: return .resize
        case .resize: return .off
        }
    }

    public func adjusted(start: Int, end: Int, by delta: Int) -> (start: Int, end: Int)? {
        switch self {
        case .off: return nil
        case .move: return CalendarTimeline.moved(start: start, end: end, by: delta)
        case .resize: return CalendarTimeline.resized(start: start, end: end, by: delta)
        }
    }
}

/// The day view's timeline, as `src/lib/calendarDayLayout.ts` draws it on the
/// web: the app's 4am-to-4am day, so it spans two calendar dates — `day` from
/// 04:00, then the date after it up to 04:00.
///
/// Two minute-spaces meet here, as on the web. *Wall* minutes are minutes since
/// midnight, what an event's 'HH:MM' means. *Offset* minutes run down the
/// timeline from its 4am top. Layout works in offsets; conversion happens only
/// where an event is read or written.
public enum CalendarTimeline {
    public static let minutesPerDay = 24 * 60
    public static let dayStartMinutes = DayKey.rolloverHour * 60
    /// What a new event, or one with a start and no end, is drawn as.
    public static let defaultDurationMinutes = 30
    /// Where "+" puts an event on a day that isn't today: 8am.
    public static let defaultHour = 8
    /// The hours labelling the grid, top to bottom: 4am … 11pm, then 12am … 3am.
    public static let displayHours = (0..<24).map { (DayKey.rolloverHour + $0) % 24 }

    public static func offset(fromWall wall: Int) -> Int {
        ((wall - dayStartMinutes) % minutesPerDay + minutesPerDay) % minutesPerDay
    }

    public static func wall(fromOffset offset: Int) -> Int { (offset + dayStartMinutes) % minutesPerDay }

    /// Whether an offset lands on the calendar date after the day it's drawn for.
    public static func isAfterMidnight(_ offset: Int) -> Bool { offset >= minutesPerDay - dayStartMinutes }

    /// 'HH:MM' → wall minutes.
    public static func minutes(_ time: String) -> Int? {
        let parts = time.prefix(5).split(separator: ":")
        guard parts.count == 2, let h = Int(parts[0]), let m = Int(parts[1]),
              (0..<24).contains(h), (0..<60).contains(m) else { return nil }
        return h * 60 + m
    }

    public static func time(_ wall: Int) -> String {
        let clamped = max(0, min(minutesPerDay - 1, wall))
        return String(format: "%02d:%02d", clamped / 60, clamped % 60)
    }

    /// An end at or before the start ran past midnight, as the server stores it.
    public static func duration(from start: String, to end: String?) -> Int {
        guard let end, let a = minutes(start), let b = minutes(end) else { return defaultDurationMinutes }
        let length = b - a
        return length > 0 ? length : length + minutesPerDay
    }

    public static func addingDays(_ count: Int, to iso: String) -> String {
        CivilDate(iso).map { $0.adding(days: count).iso } ?? iso
    }

    /// '12am', '4pm' — the grid's labels.
    public static func hourLabel(_ hour: Int) -> String {
        switch hour {
        case 0: return "12am"
        case 12: return "12pm"
        case 1..<12: return "\(hour)am"
        default: return "\(hour - 12)pm"
        }
    }

    /// Where "+" drops a new event: the current time, snapped to the half hour,
    /// when `day` is today (past midnight that is the *next* calendar date);
    /// 8am on any other day.
    public static func newEventSlot(day: String, now: Date = Date(), calendar: Calendar = .current)
        -> (date: String, time: String, endTime: String) {
        var start = offset(fromWall: defaultHour * 60)
        if day == DayKey.of(now, calendar: calendar) {
            let parts = calendar.dateComponents([.hour, .minute], from: now)
            let current = offset(fromWall: (parts.hour ?? 0) * 60 + (parts.minute ?? 0))
            start = min(minutesPerDay - defaultDurationMinutes, Int((Double(current) / 30).rounded()) * 30)
        }
        return (isAfterMidnight(start) ? addingDays(1, to: day) : day,
                time(wall(fromOffset: start)),
                time(wall(fromOffset: start + defaultDurationMinutes)))
    }

    /// Nothing is dragged shorter than this: a zero-length event can't be grabbed again.
    public static let minDurationMinutes = 15
    /// Drags land on this grid, so a thumb picks a sane time.
    public static let snapMinutes = 5

    public static func snapped(_ minutes: Double) -> Int {
        Int((minutes / Double(snapMinutes)).rounded()) * snapMinutes
    }

    /// The whole event shifted, its length kept, clamped to the 4am ends.
    public static func moved(start: Int, end: Int, by delta: Int) -> (start: Int, end: Int) {
        let length = end - start
        let newStart = max(0, min(start + delta, minutesPerDay - length))
        return (newStart, newStart + length)
    }

    /// The end dragged, the start kept: at least `minDurationMinutes`, at most the bottom of the day.
    public static func resized(start: Int, end: Int, by delta: Int) -> (start: Int, end: Int) {
        (start, max(start + minDurationMinutes, min(minutesPerDay, end + delta)))
    }

    /// What a drag that ended at `start`…`end` on `day`'s timeline asks the
    /// server for, or nil when it ended where it began. Past midnight is the
    /// next calendar date, so the date moves along with the time.
    public static func reschedule(_ item: TimedOccurrence, day: String, start: Int, end: Int) -> CalendarChange? {
        guard start != item.startMinutes || end != item.endMinutes else { return nil }
        let occurrence = item.occurrence
        return .reschedule(id: occurrence.event.id,
                           occurrence: occurrence.isRecurring ? occurrence.occurrenceDate : nil,
                           date: isAfterMidnight(start) ? addingDays(1, to: day) : day,
                           time: time(wall(fromOffset: start)), endTime: time(wall(fromOffset: end)))
    }

    /// One day as the view draws it. `labelMinutes` is how much of the
    /// timeline one event's title and time take up, for keeping them apart.
    public static func plan(day: String, events: [CalendarEvent], exceptions: [CalendarException],
                            labelMinutes: Int = 14) -> CalendarDayPlan {
        let next = addingDays(1, to: day)
        let all = CalendarExpansion.expand(events, exceptions: exceptions, start: day, end: next)
        // All-day chips come from this day's date alone. An untimed event has
        // nowhere on the grid either, so it sits with them rather than vanish.
        let chips = all.filter { $0.date == day && ($0.event.allDay || $0.time.flatMap(minutes) == nil) }
        let timed: [(CalendarOccurrence, Int, Int)] = all.compactMap { occurrence in
            guard !occurrence.event.allDay, let time = occurrence.time, let wall = minutes(time) else { return nil }
            let here = occurrence.date == day ? wall >= dayStartMinutes : occurrence.date == next && wall < dayStartMinutes
            guard here else { return nil }
            let start = offset(fromWall: wall)
            return (occurrence, start, start + duration(from: time, to: occurrence.endTime))
        }
        let ranges = timed.map { (id: $0.0.id, start: $0.1, end: $0.2) }
        let lanes = self.lanes(ranges)
        let labels = self.labels(ranges, lanes: lanes, height: labelMinutes)
        return CalendarDayPlan(allDay: chips, timed: timed.map {
            TimedOccurrence(occurrence: $0.0, startMinutes: $0.1, endMinutes: $0.2, lane: lanes[$0.0.id] ?? 0,
                            labelLane: labels[$0.0.id]?.lane ?? 0, labelRow: labels[$0.0.id]?.row ?? 0)
        })
    }

    /// Where each label goes. Overlapping events form a group whose labels
    /// all sit to the right of the group's last line, so no line covers a
    /// label; and a label that would land on another one in the group (two
    /// events ending together) moves up a row.
    static func labels(_ ranges: [(id: String, start: Int, end: Int)], lanes: [String: Int],
                       height: Int) -> [String: (lane: Int, row: Int)] {
        // Groups: runs of events joined by overlaps, in start order.
        var groups: [[(id: String, start: Int, end: Int)]] = []
        var reach = Int.min
        for range in ranges.sorted(by: { ($0.start, $0.end, $0.id) < ($1.start, $1.end, $1.id) }) {
            if range.start < reach, !groups.isEmpty { groups[groups.count - 1].append(range) } else { groups.append([range]) }
            reach = max(range.start < reach ? reach : Int.min, range.end)
        }
        var out: [String: (lane: Int, row: Int)] = [:]
        for group in groups {
            let column = group.count > 1 ? (group.compactMap { lanes[$0.id] }.max() ?? 0) : lanes[group[0].id] ?? 0
            var placed: [(top: Int, bottom: Int)] = []
            // Rows count up from the foot, so the rightmost line's label goes
            // in first: read top to bottom, labels then follow the lines left to right.
            for range in group.sorted(by: { ($0.end, -(lanes[$0.id] ?? 0)) < ($1.end, -(lanes[$1.id] ?? 0)) }) {
                var row = 0
                while placed.contains(where: { $0.top < range.end - row * height && range.end - (row + 1) * height < $0.bottom }) {
                    row += 1
                }
                placed.append((range.end - (row + 1) * height, range.end - row * height))
                out[range.id] = (column, row)
            }
        }
        return out
    }

    /// Overlapping events side by side: longest first takes the leftmost lane,
    /// each one the smallest lane nothing it overlaps already holds.
    static func lanes(_ ranges: [(id: String, start: Int, end: Int)]) -> [String: Int] {
        let sorted = ranges.sorted {
            let (a, b) = ($0.end - $0.start, $1.end - $1.start)
            return a != b ? a > b : $0.start != $1.start ? $0.start < $1.start : $0.id < $1.id
        }
        var placed: [[(Int, Int)]] = []
        var out: [String: Int] = [:]
        for range in sorted {
            var lane = 0
            while lane < placed.count, placed[lane].contains(where: { $0.0 < range.end && range.start < $0.1 }) { lane += 1 }
            if lane == placed.count { placed.append([]) }
            placed[lane].append((range.start, range.end))
            out[range.id] = lane
        }
        return out
    }
}

public struct TimedOccurrence: Equatable, Identifiable {
    public let occurrence: CalendarOccurrence
    public let startMinutes: Int
    public let endMinutes: Int
    public let lane: Int
    /// The lane whose right edge the title and time sit beside.
    public let labelLane: Int
    /// Rows up from the event's foot, when another label already sits there.
    public let labelRow: Int
    public var id: String { occurrence.id }
}

public struct CalendarDayPlan: Equatable {
    public let allDay: [CalendarOccurrence]
    public let timed: [TimedOccurrence]
}

/// The six categories the server's classifier assigns, in its order.
public enum CalendarCategory {
    public static let all = ["leisure", "work", "exercise", "family", "outside", "indoors"]
    /// The web's `CATEGORY_COLORS`, as 0xRRGGBB.
    public static let colors: [String: UInt32] = [
        "outside": 0x3b82f6, "work": 0x27a164, "family": 0x9b59b6,
        "indoors": 0x8a8f98, "leisure": 0xd9b382, "exercise": 0xe0475a,
    ]
}
