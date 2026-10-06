import Foundation

/// The replica collections behind the calendar. Synced as their own scope, and
/// only when the server lists them: asking an older server for a collection it
/// doesn't know is a 400, which would fail the journal's sync along with it.
public enum CalendarSync {
    public static let collections = ["calendar_event_exceptions", "calendar_events"]

    public static func supported(by serverCollections: [String]) -> Bool {
        collections.allSatisfy(serverCollections.contains)
    }
}

/// A replicated `calendar_events` row: the series template, not an instance.
/// The device expands it itself (`CalendarExpansion`), so the calendar works
/// offline from the same rows the server expands for the web app.
public struct CalendarEvent: Hashable {
    public let id: String
    public var title: String
    public var description: String?
    /// 'YYYY-MM-DD' — wall-clock strings, never timestamps, exactly as stored.
    public var date: String
    public var time: String?
    public var endTime: String?
    public var allDay: Bool
    public var tags: [String]
    public var categoryTags: [String]
    public var repeatFreq: String?
    public var repeatInterval: Int
    public var repeatByweekday: String?
    public var repeatUntil: String?

    public init(id: String, title: String, description: String? = nil, date: String, time: String? = nil,
                endTime: String? = nil, allDay: Bool = false, tags: [String] = [], categoryTags: [String] = [],
                repeatFreq: String? = nil, repeatInterval: Int = 1, repeatByweekday: String? = nil,
                repeatUntil: String? = nil) {
        self.id = id; self.title = title; self.description = description; self.date = date
        self.time = time; self.endTime = endTime; self.allDay = allDay; self.tags = tags
        self.categoryTags = categoryTags; self.repeatFreq = repeatFreq; self.repeatInterval = repeatInterval
        self.repeatByweekday = repeatByweekday; self.repeatUntil = repeatUntil
    }

    public init?(record: SyncChange) {
        guard record.collection == "calendar_events", !record.deleted, let data = record.data,
              let date = data["date"]?.string, CivilDate(date) != nil else { return nil }
        self.init(id: record.id, title: data["title"]?.string ?? "", description: data["description"]?.string,
                  date: date, time: data["time"]?.string, endTime: data["endTime"]?.string,
                  allDay: (data["allDay"]?.number ?? 0) != 0,
                  tags: Self.list(data["tags"]), categoryTags: Self.list(data["categoryTags"]),
                  repeatFreq: data["repeatFreq"]?.string,
                  repeatInterval: max(1, Int(data["repeatInterval"]?.number ?? 1)),
                  repeatByweekday: data["repeatByweekday"]?.string, repeatUntil: data["repeatUntil"]?.string)
    }

    public var isSeries: Bool { CalendarExpansion.frequencies.contains(repeatFreq ?? "") }

    /// Tag columns hold a JSON array as text.
    private static func list(_ value: JSONValue?) -> [String] {
        guard let text = value?.string,
              let items = try? JSONDecoder().decode([String].self, from: Data(text.utf8)) else { return [] }
        return items
    }
}

/// A replicated `calendar_event_exceptions` row: one occurrence skipped or moved.
public struct CalendarException: Equatable {
    public var eventID: String
    public let date: String
    public let action: String
    public let newDate: String?
    public let newTime: String?
    public let newEndTime: String?

    public init(eventID: String, date: String, action: String, newDate: String? = nil,
                newTime: String? = nil, newEndTime: String? = nil) {
        self.eventID = eventID; self.date = date; self.action = action
        self.newDate = newDate; self.newTime = newTime; self.newEndTime = newEndTime
    }

    public init?(record: SyncChange) {
        guard record.collection == "calendar_event_exceptions", !record.deleted, let data = record.data,
              let event = data["eventId"]?.string, let date = data["date"]?.string,
              let action = data["action"]?.string, ["skip", "move"].contains(action) else { return nil }
        self.init(eventID: event, date: date, action: action, newDate: data["newDate"]?.string,
                  newTime: data["newTime"]?.string, newEndTime: data["newEndTime"]?.string)
    }
}

/// One concrete instance of an event on one day.
public struct CalendarOccurrence: Hashable, Identifiable {
    public let event: CalendarEvent
    /// The series date this instance belongs to — what an exception is keyed on.
    public let occurrenceDate: String
    /// Where it actually lands, after any move.
    public let date: String
    public let time: String?
    public let endTime: String?

    public var id: String { event.id + "@" + occurrenceDate }
    public var isRecurring: Bool { event.isSeries }
}

/// A date with no time zone: 'YYYY-MM-DD' as a day count, so recurrence
/// arithmetic never meets a DST transition or the device's zone.
struct CivilDate: Comparable, Hashable {
    let days: Int

    init(days: Int) { self.days = days }

    init?(_ iso: String) {
        let parts = iso.split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 3, parts[0].count == 4, parts[1].count == 2, parts[2].count == 2,
              let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2]),
              (1...12).contains(m), (1...Self.length(y, m)).contains(d) else { return nil }
        days = Self.daysFromCivil(y, m, d)
    }

    init(year: Int, month: Int, day: Int) {
        days = Self.daysFromCivil(year, month, min(day, Self.length(year, month)))
    }

    var parts: (year: Int, month: Int, day: Int) {
        // Howard Hinnant's civil_from_days.
        let z = days + 719_468
        let era = (z >= 0 ? z : z - 146_096) / 146_097
        let doe = z - era * 146_097
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
        let mp = (5 * doy + 2) / 153
        let d = doy - (153 * mp + 2) / 5 + 1
        let m = mp < 10 ? mp + 3 : mp - 9
        return (yoe + era * 400 + (m <= 2 ? 1 : 0), m, d)
    }

    var iso: String {
        let p = parts
        return String(format: "%04d-%02d-%02d", p.year, p.month, p.day)
    }

    /// Sunday=0 … Saturday=6, as the server and the web grid number them.
    var weekday: Int { ((days + 4) % 7 + 7) % 7 }

    func adding(days count: Int) -> CivilDate { CivilDate(days: days + count) }

    /// Same day `months` later, clamped to that month's length (Jan 31 → Feb 28/29).
    func adding(months: Int) -> CivilDate {
        let p = parts
        let total = p.year * 12 + (p.month - 1) + months
        let year = Int((Double(total) / 12).rounded(.down))
        return CivilDate(year: year, month: total - year * 12 + 1, day: p.day)
    }

    static func < (a: CivilDate, b: CivilDate) -> Bool { a.days < b.days }

    static func length(_ year: Int, _ month: Int) -> Int {
        switch month {
        case 2: return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0 ? 29 : 28
        case 4, 6, 9, 11: return 30
        default: return 31
        }
    }

    private static func daysFromCivil(_ year: Int, _ month: Int, _ day: Int) -> Int {
        let y = month <= 2 ? year - 1 : year
        let era = (y >= 0 ? y : y - 399) / 400
        let yoe = y - era * 400
        let doy = (153 * (month > 2 ? month - 3 : month + 9) + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        return era * 146_097 + doe - 719_468
    }
}

/// A port of `backend/calendar_recurrence.py`. The two must agree, or the phone
/// and the web app show a series on different days; the tests pin the same cases.
public enum CalendarExpansion {
    public static let frequencies: Set<String> = ["daily", "weekly", "monthly", "yearly"]
    static let maxOccurrences = 400

    /// ISO dates on which `event` occurs within [start, end] inclusive.
    public static func occurrenceDates(_ event: CalendarEvent, start: String, end: String) -> [String] {
        guard let anchor = CivilDate(event.date), let winStart = CivilDate(start),
              var winEnd = CivilDate(end), winStart <= winEnd else { return [] }
        guard let freq = event.repeatFreq, frequencies.contains(freq) else {
            return winStart <= anchor && anchor <= winEnd ? [anchor.iso] : []
        }
        if let until = event.repeatUntil.flatMap(CivilDate.init), until < winEnd { winEnd = until }
        guard winEnd >= anchor, winEnd >= winStart else { return [] }
        let interval = max(1, event.repeatInterval)
        var out: [CivilDate] = []
        switch freq {
        case "daily":
            let steps = ceil(max(0, winStart.days - anchor.days), interval)
            var current = anchor.adding(days: steps * interval)
            while current <= winEnd && out.count < maxOccurrences {
                out.append(current)
                current = current.adding(days: interval)
            }
        case "weekly":
            var weekdays = Set((event.repeatByweekday ?? "").split(separator: ",")
                .compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }.filter { (0...6).contains($0) })
            if weekdays.isEmpty { weekdays = [anchor.weekday] }
            let anchorWeek = anchor.adding(days: -anchor.weekday)
            let startWeek = winStart.adding(days: -winStart.weekday)
            let steps = ceil(max(0, (startWeek.days - anchorWeek.days) / 7), interval)
            var week = anchorWeek.adding(days: steps * interval * 7)
            while week <= winEnd && out.count < maxOccurrences {
                for offset in weekdays.sorted() {
                    let day = week.adding(days: offset)
                    if anchor <= day && day <= winEnd && day >= winStart { out.append(day) }
                }
                week = week.adding(days: interval * 7)
            }
        default:
            // Monthly and yearly both clamp to the month's length rather than skip.
            let unit = freq == "yearly" ? 12 : 1
            let a = anchor.parts, s = winStart.parts
            let behind = freq == "yearly" ? max(0, s.year - a.year) : max(0, (s.year - a.year) * 12 + (s.month - a.month))
            var steps = ceil(behind, interval)
            while out.count < maxOccurrences {
                let current = anchor.adding(months: steps * interval * unit)
                if current > winEnd { break }
                if current >= winStart && current >= anchor { out.append(current) }
                steps += 1
            }
        }
        return out.prefix(maxOccurrences).map(\.iso)
    }

    /// Concrete instances within [start, end], sorted by date then time.
    /// A skip drops an occurrence; a move rewrites it, and can pull one in from
    /// outside the window.
    public static func expand(_ events: [CalendarEvent], exceptions: [CalendarException],
                              start: String, end: String) -> [CalendarOccurrence] {
        guard let winStart = CivilDate(start), let winEnd = CivilDate(end), winStart <= winEnd else { return [] }
        let byEvent = Dictionary(grouping: exceptions, by: \.eventID)
            .mapValues { Dictionary($0.map { ($0.date, $0) }, uniquingKeysWith: { _, last in last }) }
        var out: [CalendarOccurrence] = []
        for event in events {
            let excs = byEvent[event.id] ?? [:]
            let (lo, hi) = searchWindow(excs, winStart, winEnd)
            for iso in occurrenceDates(event, start: lo.iso, end: hi.iso) {
                let exc = excs[iso]
                if exc?.action == "skip" { continue }
                var date = iso, time = event.time, endTime = event.endTime
                if let exc, exc.action == "move" {
                    date = exc.newDate ?? date
                    time = exc.newTime ?? time
                    endTime = exc.newEndTime ?? endTime
                }
                guard let landed = CivilDate(date), winStart <= landed, landed <= winEnd else { continue }
                out.append(CalendarOccurrence(event: event, occurrenceDate: iso, date: date, time: time, endTime: endTime))
            }
        }
        return out.sorted { ($0.date, $0.time ?? "") < ($1.date, $1.time ?? "") }
    }

    private static func searchWindow(_ excs: [String: CalendarException], _ start: CivilDate,
                                     _ end: CivilDate) -> (CivilDate, CivilDate) {
        var lo = start, hi = end
        for (iso, exc) in excs where exc.action == "move" {
            guard let origin = CivilDate(iso), let moved = exc.newDate.flatMap(CivilDate.init),
                  start <= moved && moved <= end else { continue }
            lo = min(lo, origin); hi = max(hi, origin)
        }
        return (lo, hi)
    }

    private static func ceil(_ value: Int, _ divisor: Int) -> Int { (value + divisor - 1) / divisor }
}
