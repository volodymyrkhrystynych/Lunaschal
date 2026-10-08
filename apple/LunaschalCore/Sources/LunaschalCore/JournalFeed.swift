import Foundation

/// One attachment on a server journal entry, read from its replicated row.
/// `kind` is the server's: 'image' | 'audio' | 'video' | 'youtube' | 'file'.
public struct JournalAttachmentItem: Hashable, Identifiable {
    public let id: String
    public let entryID: String
    public let kind: String
    public let name: String
    public let mime: String
    public let position: Int
    public let sourceURL: String?
    public let importStatus: String?
    public let transcript: String?
    public let description: String?
    /// The replica (and media) collection the row came from: a journal
    /// attachment, or a meal's `food_media`.
    public let collection: String

    public init(id: String, entryID: String, kind: String, name: String = "", mime: String = "",
                position: Int = 0, sourceURL: String? = nil, importStatus: String? = nil,
                transcript: String? = nil, description: String? = nil,
                collection: String = "journal_attachments") {
        self.id = id; self.entryID = entryID; self.kind = kind; self.name = name; self.mime = mime
        self.position = position; self.sourceURL = sourceURL; self.importStatus = importStatus
        self.transcript = transcript; self.description = description; self.collection = collection
    }

    public init?(record: SyncChange) {
        guard record.collection == "journal_attachments", !record.deleted, let data = record.data,
              let entryID = data["entryId"]?.string, let kind = data["kind"]?.string else { return nil }
        func text(_ key: String) -> String? {
            guard let value = data[key]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else { return nil }
            return value
        }
        self.init(id: record.id, entryID: entryID, kind: kind, name: data["name"]?.string ?? "",
                  mime: data["mime"]?.string ?? "", position: Int(data["position"]?.number ?? 0),
                  sourceURL: text("sourceUrl"), importStatus: text("importStatus"),
                  transcript: text("transcript"), description: text("description"))
    }

    /// A row's kind, falling back to its MIME type: older rows were stored as 'file'.
    public var media: Media {
        switch kind {
        case "image": return .image
        case "audio": return .audio
        case "video": return .video
        case "youtube": return .youtube
        default:
            if mime.hasPrefix("image/") { return .image }
            if mime.hasPrefix("audio/") { return .audio }
            if mime.hasPrefix("video/") { return .video }
            return .file
        }
    }

    public enum Media { case image, audio, video, youtube, file }

    /// Whether AVFoundation can open the stored file. A clip recorded in a
    /// desktop browser is WebM/Opus, which it cannot; the server then sends an
    /// AAC copy (`backend/journal/playable.py`).
    public var phonePlayable: Bool {
        let foreign = ["webm", "ogg", "oga", "opus"]
        let type = mime.split(separator: ";").first.map(String.init)?.lowercased() ?? ""
        if foreign.contains(where: { type.hasSuffix("/" + $0) }) { return false }
        let ext = name.split(separator: ".").count > 1 ? name.split(separator: ".").last.map { $0.lowercased() } : nil
        return !(type.isEmpty && ext.map(foreign.contains) == true)
    }

    /// The type to tell a player: the archived copy of a YouTube video is an mp4.
    public var playerMIME: String {
        if media == .audio && !phonePlayable { return "audio/mp4" }
        if media == .youtube && !mime.hasPrefix("video/") { return "video/mp4" }
        return mime
    }

    /// An entry's attachments in the order the server shows them.
    public static func grouped(_ records: [SyncChange]) -> [String: [JournalAttachmentItem]] {
        Dictionary(grouping: records.compactMap(JournalAttachmentItem.init(record:)), by: \.entryID)
            .mapValues { $0.sorted { ($0.position, $0.id) < ($1.position, $1.id) } }
    }
}

/// The Journal feed's calendar borders, as `src/lib/journalEventGroups.ts`
/// draws them on the web: an event that has been given a category wraps the
/// run of feed items written during it in a ring of its category colours.
///
/// The feed is newest first and an event's window is one contiguous range of
/// time, so everything it covers is one contiguous run of the feed.
public enum JournalEventGroups {
    public struct Span: Hashable {
        public let occurrence: CalendarOccurrence
        /// Inclusive indexes into the feed passed in.
        public let start: Int
        public let end: Int
    }

    /// The instants an occurrence covers, or nil when it has none to group by
    /// (untimed and not all-day). All day means the app's 4am day, so a 1am
    /// entry still sits inside the day before; a timed event whose end is at
    /// or before its start ran past midnight.
    public static func window(_ occurrence: CalendarOccurrence, calendar: Calendar = .current) -> ClosedRange<Date>? {
        if occurrence.event.allDay {
            guard let start = CalendarSleep.dayStart(occurrence.date, calendar: calendar) else { return nil }
            let from = Date(timeIntervalSince1970: TimeInterval(start))
            return from...from.addingTimeInterval(TimeInterval(CalendarTimeline.minutesPerDay * 60 - 1))
        }
        guard let time = occurrence.time, let wall = CalendarTimeline.minutes(time),
              let midnight = CivilDate(occurrence.date).flatMap({ day -> Date? in
                  let parts = day.iso.split(separator: "-").compactMap { Int($0) }
                  return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
              }),
              let start = calendar.date(byAdding: .minute, value: wall, to: midnight) else { return nil }
        let length = CalendarTimeline.duration(from: time, to: occurrence.endTime)
        return start...start.addingTimeInterval(TimeInterval(length * 60))
    }

    /// Spans over `times` (newest first) for every categorised occurrence that
    /// covers at least one of them. Where two events overlap, the one whose
    /// run starts first in the feed keeps the items: a feed item sits inside
    /// one border, never two.
    public static func spans(times: [Date], occurrences: [CalendarOccurrence],
                             calendar: Calendar = .current) -> [Span] {
        var found: [Span] = []
        for occurrence in occurrences where !occurrence.event.categoryTags.isEmpty {
            guard let window = window(occurrence, calendar: calendar) else { continue }
            var start = -1, end = -1
            for (index, time) in times.enumerated() {
                if window.contains(time) {
                    if start == -1 { start = index }
                    end = index
                } else if start != -1 { break }
            }
            if start != -1 { found.append(Span(occurrence: occurrence, start: start, end: end)) }
        }
        var taken = -1
        return found
            .sorted { ($0.start, -$0.end, $0.occurrence.id) < ($1.start, -$1.end, $1.occurrence.id) }
            .filter { span in
                guard span.start > taken else { return false }
                taken = span.end
                return true
            }
    }

    /// The day keys a feed's times fall on, widened by a day each side so a
    /// moved occurrence or a 4am edge is never missed.
    public static func dayRange(_ times: [Date], calendar: Calendar = .current) -> (start: String, end: String)? {
        guard let first = times.min(), let last = times.max() else { return nil }
        return (CalendarTimeline.addingDays(-1, to: DayKey.of(first, calendar: calendar)),
                CalendarTimeline.addingDays(1, to: DayKey.of(last, calendar: calendar)))
    }
}

public enum JournalTimestamp {
    /// The server's ISO timestamps, with or without fractional seconds.
    /// Made once: the feed parses every entry's time on each redraw, and a
    /// new formatter per call was two ICU setups per entry, on the UI thread.
    /// `ISO8601DateFormatter` is thread-safe.
    private static let plain = ISO8601DateFormatter()
    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    public static func parse(_ value: String?) -> Date? {
        guard let value else { return nil }
        return plain.date(from: value) ?? fractional.date(from: value)
    }
}
