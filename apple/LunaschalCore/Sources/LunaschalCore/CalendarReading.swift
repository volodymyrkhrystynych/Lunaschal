import Foundation

/// One stretch of in-app reading on a day, as `GET /api/calendar/reading/<date>`
/// answers: already clipped to the 4am day by the server. Unix seconds.
/// `ficId` is absent from servers (and cached days) older than session
/// merging; the title stands in for it then.
public struct CalendarReadingSpan: Codable, Equatable, Identifiable {
    public let id: String
    public let ficId: String?
    public let title: String
    public let chapterNumber: Int
    public let startedAt: Int
    public let endedAt: Int

    public init(id: String, ficId: String? = nil, title: String, chapterNumber: Int, startedAt: Int, endedAt: Int) {
        self.id = id; self.ficId = ficId; self.title = title; self.chapterNumber = chapterNumber
        self.startedAt = startedAt; self.endedAt = endedAt
    }

    var ficKey: String { ficId ?? "title:\(title)" }
}

/// Chapters of one fic read back to back: one label on the rail instead of
/// one per chapter. Spans are in start order and never empty.
public struct ReadingSession: Equatable, Identifiable {
    public let spans: [CalendarReadingSpan]
    public var id: String { spans[0].id }
    public var title: String { spans[0].title }
    public var startedAt: Int { spans[0].startedAt }
    public var endedAt: Int { spans.map(\.endedAt).max()! }
    /// Distinct chapters, ascending — a chapter reopened counts once.
    public var chapters: [Int] { Array(Set(spans.map(\.chapterNumber))).sorted() }

    /// "Ch. 12", "Ch. 12–20" for an unbroken run, "Ch. 3, 7" for two or three
    /// apart, "5 chapters" beyond that.
    public var chapterLabel: String {
        let chapters = chapters
        guard let first = chapters.first, let last = chapters.last else { return "" }
        if chapters.count == 1 { return "Ch. \(first)" }
        if last - first + 1 == chapters.count { return "Ch. \(first)–\(last)" }
        if chapters.count <= 3 { return "Ch. " + chapters.map(String.init).joined(separator: ", ") }
        return "\(chapters.count) chapters"
    }
}

/// A session placed on the day view, in offset minutes. `segments` are the
/// stretches actually read, so the rail still shows the breaks between
/// chapters; `labelMinutes` is where its one label sits, pushed down past the
/// one before it when two sessions are closer together than a label is tall.
public struct PlacedReading: Equatable, Identifiable {
    public let session: ReadingSession
    public let segments: [ClosedRange<Int>]
    public let startMinutes: Int
    public let endMinutes: Int
    public let labelMinutes: Int
    public var id: String { session.id }
}

/// The reading rail: it runs from waking to sleep (the day's edges when
/// either is unknown) with the read stretches filled in.
public struct ReadingRail: Equatable {
    public let startMinutes: Int
    public let endMinutes: Int
    public let spans: [PlacedReading]
}

/// `src/lib/calendarReading.ts`'s `readingLayout`, natively, plus the merging
/// of a fic's consecutive chapters into one session, which the web does not do.
public enum CalendarReading {
    /// A break longer than this starts a new session, even in the same fic.
    public static let sessionGapSeconds = 30 * 60

    /// Consecutive spans of one fic, each starting within `sessionGapSeconds`
    /// of the last one's end. Any other fic read in between splits them.
    public static func sessions(_ spans: [CalendarReadingSpan]) -> [ReadingSession] {
        var groups: [[CalendarReadingSpan]] = []
        for span in spans.sorted(by: { ($0.startedAt, $0.id) < ($1.startedAt, $1.id) }) {
            if let last = groups.last, last[0].ficKey == span.ficKey,
               span.startedAt - last.map(\.endedAt).max()! <= sessionGapSeconds {
                groups[groups.count - 1].append(span)
            } else {
                groups.append([span])
            }
        }
        return groups.map(ReadingSession.init(spans:))
    }

    /// `labelMinutes` is one label's height in timeline minutes.
    public static func rail(day: String, sleep: SleepDay?, spans: [CalendarReadingSpan],
                            labelMinutes: Int, calendar: Calendar = .current) -> ReadingRail? {
        guard let dayStart = CalendarSleep.dayStart(day, calendar: calendar) else { return nil }
        let minutes = { (ts: Int) in (ts - dayStart) / 60 }
        let clamp = { (m: Int) in max(0, min(CalendarTimeline.minutesPerDay, m)) }
        let start = clamp(sleep?.wakeAt.map(minutes) ?? 0)
        let end = clamp(sleep?.sleepAt.map(minutes) ?? CalendarTimeline.minutesPerDay)
        guard end > start else { return nil }
        // Trimmed before merging, so a span wholly outside waking hours
        // neither shows nor bridges two sessions.
        let awake = spans.filter { min(end, minutes($0.endedAt)) > max(start, minutes($0.startedAt)) }
        var nextLabel = 0
        let placed = sessions(awake).map { session -> PlacedReading in
            let segments = session.spans.map { max(start, minutes($0.startedAt))...min(end, minutes($0.endedAt)) }
            let from = segments.map(\.lowerBound).min()!, to = segments.map(\.upperBound).max()!
            let label = max(from, nextLabel)
            nextLabel = label + labelMinutes
            return PlacedReading(session: session, segments: segments, startMinutes: from, endMinutes: to,
                                 labelMinutes: label)
        }
        return ReadingRail(startMinutes: start, endMinutes: end, spans: placed)
    }
}

/// How wide each label on the day view may be drawn, so that an event's title
/// (left, beside its line) and a reading title (right, against the rail) use
/// the whole row between them and still never overlap. Points throughout;
/// the view measures, this decides.
public enum CalendarLabelFit {
    /// A label: its vertical extent, and how wide its text wants to be on one line.
    public struct Box: Equatable {
        public let top: Double
        public let bottom: Double
        public let preferred: Double
        public init(top: Double, bottom: Double, preferred: Double) {
            self.top = top; self.bottom = bottom; self.preferred = preferred
        }
        func overlaps(_ top: Double, _ bottom: Double) -> Bool { self.top < bottom && top < self.bottom }
    }

    /// An event's label, which starts at `left` and grows rightwards.
    public struct EventLabel: Equatable {
        public let box: Box
        public let left: Double
        public init(box: Box, left: Double) { self.box = box; self.left = left }
    }

    /// Something a reading label must not cover: an event's line, by its span and right edge.
    public struct Obstacle: Equatable {
        public let top: Double
        public let bottom: Double
        public let right: Double
        public init(top: Double, bottom: Double, right: Double) { self.top = top; self.bottom = bottom; self.right = right }
    }

    /// Space kept between any two neighbours.
    public static let gap: Double = 6
    /// Below this a label is unreadable; a reading label gets at least this
    /// much even if it has to sit over an event line.
    public static let minimum: Double = 72

    /// `right` is where reading labels end (just left of the rail) and where
    /// event labels must stop; `readingFloor` is the leftmost a reading label
    /// may reach where nothing else is in the way (the hour gutter's edge).
    public static func widths(events: [EventLabel], readings: [Box], obstacles: [Obstacle],
                              right: Double, readingFloor: Double) -> (events: [Double], readings: [Double]) {
        var eventWidths = events.map { max(0, min($0.box.preferred, right - gap - $0.left)) }
        var readingWidths = readings.map { box -> Double in
            let floor = obstacles.filter { box.overlaps($0.top, $0.bottom) }.map { $0.right + gap }.max() ?? readingFloor
            return min(box.preferred, max(minimum, right - max(floor, readingFloor)))
        }
        // Each pair sharing a row splits that row; a label keeps the
        // narrowest share any neighbour leaves it, so every pair still fits.
        for (r, reading) in readings.enumerated() {
            for (e, event) in events.enumerated() where reading.overlaps(event.box.top, event.box.bottom) {
                let room = max(0, right - event.left - gap)
                let (a, b) = split(room, eventWidths[e], readingWidths[r])
                eventWidths[e] = min(eventWidths[e], a)
                readingWidths[r] = min(readingWidths[r], b)
            }
        }
        return (eventWidths, readingWidths)
    }

    /// Both if they fit; otherwise the one that wants less than half keeps it
    /// and the other takes the rest; otherwise half each.
    static func split(_ room: Double, _ a: Double, _ b: Double) -> (Double, Double) {
        if a + b <= room { return (a, b) }
        if a <= room / 2 { return (a, room - a) }
        if b <= room / 2 { return (room - b, b) }
        return (room / 2, room / 2)
    }
}
