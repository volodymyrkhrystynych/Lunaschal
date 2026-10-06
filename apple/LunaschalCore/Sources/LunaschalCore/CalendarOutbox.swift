import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// An event's fields as the create and edit routes take them. Absent values
/// are sent as explicit nulls, so an edit can clear a description, a time or
/// a repeat rule; `POST` reads a null the same as a missing field.
public struct CalendarDraft: Codable, Equatable, Identifiable {
    public let id: String
    public var title: String
    public var date: String
    public var time: String?
    public var endTime: String?
    public var allDay: Bool
    public var description: String?
    public var tags: [String]?
    /// The six categories, ticked by hand. Nil leaves them to the server's
    /// classifier and isn't sent; an empty list clears them.
    public var categoryTags: [String]?
    public var repeatFreq: String?
    public var repeatInterval: Int?
    public var repeatByweekday: [Int]?
    public var repeatUntil: String?

    public init(id: String = ULID.make(), title: String, date: String, time: String? = nil, endTime: String? = nil,
                allDay: Bool = false, description: String? = nil, tags: [String] = [],
                categoryTags: [String]? = nil, repeatFreq: String? = nil, repeatInterval: Int = 1, repeatByweekday: [Int] = [],
                repeatUntil: String? = nil) {
        self.id = id
        self.title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        self.date = date
        self.allDay = allDay
        // An all-day event carries no clock, as the server stores it.
        self.time = allDay ? nil : time
        self.endTime = allDay ? nil : endTime
        let text = description?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        self.description = text.isEmpty ? nil : text
        let cleaned = tags.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        self.tags = cleaned.isEmpty ? nil : cleaned
        self.categoryTags = categoryTags.map { picked in CalendarCategory.all.filter(picked.contains) }
        let freq = repeatFreq.flatMap { CalendarExpansion.frequencies.contains($0) ? $0 : nil }
        self.repeatFreq = freq
        self.repeatInterval = freq == nil ? nil : max(1, repeatInterval)
        self.repeatByweekday = freq == "weekly" && !repeatByweekday.isEmpty ? repeatByweekday.sorted() : nil
        self.repeatUntil = freq == nil ? nil : repeatUntil
    }

    /// The form opened on an existing event, keeping its id.
    public init(editing event: CalendarEvent) {
        self.init(id: event.id, title: event.title, date: event.date, time: event.time, endTime: event.endTime,
                  allDay: event.allDay, description: event.description, tags: event.tags,
                  categoryTags: event.categoryTags, repeatFreq: event.repeatFreq, repeatInterval: event.repeatInterval,
                  repeatByweekday: (event.repeatByweekday ?? "").split(separator: ",").compactMap { Int($0) },
                  repeatUntil: event.repeatUntil)
    }

    private enum CodingKeys: String, CodingKey {
        case id, title, date, time, endTime, allDay, description, tags, categoryTags
        case repeatFreq, repeatInterval, repeatByweekday, repeatUntil
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(title, forKey: .title)
        try c.encode(date, forKey: .date)
        try c.encode(time, forKey: .time)
        try c.encode(endTime, forKey: .endTime)
        try c.encode(allDay, forKey: .allDay)
        try c.encode(description, forKey: .description)
        try c.encode(tags, forKey: .tags)
        try c.encodeIfPresent(categoryTags, forKey: .categoryTags)
        try c.encode(repeatFreq, forKey: .repeatFreq)
        try c.encode(repeatInterval, forKey: .repeatInterval)
        try c.encode(repeatByweekday, forKey: .repeatByweekday)
        try c.encode(repeatUntil, forKey: .repeatUntil)
    }

    /// What the form must not send: the server would refuse it.
    public var problem: String? {
        if title.isEmpty { return "Give the event a title." }
        if CivilDate(date) == nil { return "That date isn't valid." }
        if let until = repeatUntil, until < date { return "A repeat can't end before it starts." }
        return nil
    }

    /// How it shows on the calendar until the server's copy arrives.
    public var event: CalendarEvent {
        CalendarEvent(id: id, title: title, description: description, date: date, time: time, endTime: endTime,
                      allDay: allDay, tags: tags ?? [], categoryTags: categoryTags ?? [], repeatFreq: repeatFreq, repeatInterval: repeatInterval ?? 1,
                      repeatByweekday: repeatByweekday?.map(String.init).joined(separator: ","),
                      repeatUntil: repeatUntil)
    }

    /// Comma-separated input → tags, as the web form reads it.
    public static func tags(from text: String) -> [String] {
        text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}

/// One change to the calendar, as the server will be told it — the same
/// choices the web's event details offer.
public enum CalendarChange: Codable, Equatable {
    case create(CalendarDraft)
    /// Every occurrence, past ones included ("All events", or a one-off).
    case update(CalendarDraft)
    /// `date` and later; earlier occurrences keep what they had. `newID` is
    /// the new series' id, minted here so a replay can't split twice.
    case updateFrom(date: String, newID: String, CalendarDraft)
    /// The whole series, history included.
    case delete(id: String)
    /// One occurrence.
    case skip(id: String, date: String)
    /// "This and future": the series stops before `date`.
    case endSeries(id: String, date: String)
    /// A drag on the day view: a one-off gets its new date and times; one
    /// occurrence of a series (`occurrence` is its own date) is moved alone,
    /// the scoping the web's drag applies.
    case reschedule(id: String, occurrence: String?, date: String, time: String, endTime: String)
    /// The categories ticked on the event's page. They belong to the whole
    /// series, as the web's are; an empty list clears them.
    case categories(id: String, tags: [String])
    /// The day's wake and sleep, set by hand ('HH:MM'); nil hands that end
    /// back to the server's derived value.
    case sleep(date: String, wake: String?, sleep: String?)

    /// Every event id this change touches.
    var eventIDs: [String] {
        switch self {
        case let .create(draft), let .update(draft): return [draft.id]
        case let .updateFrom(_, newID, draft): return [draft.id, newID]
        case let .delete(id), let .skip(id, _), let .endSeries(id, _), let .reschedule(id, _, _, _, _),
             let .categories(id, _): return [id]
        case .sleep: return []
        }
    }

    var isDelete: Bool {
        switch self {
        case .delete, .skip, .endSeries: return true
        default: return false
        }
    }

    var subject: String {
        switch self {
        case let .create(draft), let .update(draft), let .updateFrom(_, _, draft): return "“\(draft.title)”"
        case .reschedule: return "a moved event"
        case .categories: return "the categories"
        case .sleep: return "the sleep times"
        default: return "a deletion"
        }
    }
}

public struct CalendarOp: Codable, Equatable, Identifiable {
    public let id: String
    public let change: CalendarChange

    public init(id: String = ULID.make(), change: CalendarChange) { self.id = id; self.change = change }
}

/// Changes waiting for the server, oldest first.
public final class CalendarOutbox {
    public let root: URL
    private var file: URL { root.appendingPathComponent("outbox.json") }

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [CalendarOp] {
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        let data = try Data(contentsOf: file)
        if let ops = try? JSONDecoder().decode([CalendarOp].self, from: data) { return ops }
        // The first version queued only new events, as bare drafts.
        return try JSONDecoder().decode([CalendarDraft].self, from: data).map { CalendarOp(id: $0.id, change: .create($0)) }
    }

    public func append(_ change: CalendarChange) throws {
        try write(try list() + [CalendarOp(change: change)])
    }

    public func remove(_ op: CalendarOp) throws {
        try write(try list().filter { $0.id != op.id })
    }

    private func write(_ ops: [CalendarOp]) throws {
        try JSONEncoder().encode(ops).write(to: file, options: .atomic)
    }
}

/// The calendar as it will be once the queued changes reach the server: the
/// replica's rows with each change applied the way the server applies it.
public struct CalendarOverlay: Equatable {
    public var events: [CalendarEvent]
    public var exceptions: [CalendarException]
    /// Events with a change not yet on the server.
    public var pendingIDs: Set<String>

    public init(events: [CalendarEvent], exceptions: [CalendarException], pending ops: [CalendarOp]) {
        self.events = events
        self.exceptions = exceptions
        pendingIDs = []
        for op in ops {
            apply(op.change)
            pendingIDs.formUnion(op.change.eventIDs)
        }
    }

    private mutating func apply(_ change: CalendarChange) {
        switch change {
        case let .create(draft):
            // Already on the server: its copy wins.
            if !events.contains(where: { $0.id == draft.id }) { events.append(draft.event) }
        case let .update(draft):
            edit(draft.id) { $0 = Self.edited($0, with: draft, date: draft.date) }
        case let .updateFrom(date, newID, draft):
            guard let original = events.first(where: { $0.id == draft.id }),
                  !events.contains(where: { $0.id == newID }) else { return }
            guard original.isSeries, let cutoff = Self.cutoff(original, before: date) else {
                // Nothing earlier to keep: the server edits it in place.
                edit(draft.id) { $0 = Self.edited($0, with: draft, date: $0.date) }
                return
            }
            var next = Self.edited(original, with: draft, date: date)
            next = CalendarEvent(id: newID, title: next.title, description: next.description, date: date,
                                 time: next.time, endTime: next.endTime, allDay: next.allDay, tags: next.tags,
                                 categoryTags: next.categoryTags, repeatFreq: next.repeatFreq,
                                 repeatInterval: next.repeatInterval, repeatByweekday: next.repeatByweekday,
                                 repeatUntil: next.repeatUntil)
            for i in exceptions.indices where exceptions[i].eventID == draft.id && exceptions[i].date >= date {
                exceptions[i].eventID = newID
            }
            edit(draft.id) { $0.repeatUntil = cutoff }
            events.append(next)
        case let .delete(id):
            events.removeAll { $0.id == id }
            exceptions.removeAll { $0.eventID == id }
        case let .skip(id, date):
            exceptions.removeAll { $0.eventID == id && $0.date == date }
            exceptions.append(CalendarException(eventID: id, date: date, action: "skip"))
        case let .reschedule(id, occurrence, date, time, endTime):
            if let occurrence {
                exceptions.removeAll { $0.eventID == id && $0.date == occurrence }
                exceptions.append(CalendarException(eventID: id, date: occurrence, action: "move",
                                                    newDate: date == occurrence ? nil : date,
                                                    newTime: time, newEndTime: endTime))
            } else {
                edit(id) { $0.date = date; $0.time = time; $0.endTime = endTime }
            }
        case let .categories(id, tags):
            edit(id) { $0.categoryTags = CalendarCategory.all.filter(tags.contains) }
        case .sleep:
            break
        case let .endSeries(id, date):
            guard let event = events.first(where: { $0.id == id }) else { return }
            if let cutoff = Self.cutoff(event, before: date) {
                edit(id) { $0.repeatUntil = cutoff }
                exceptions.removeAll { $0.eventID == id && $0.date >= date }
            } else {
                events.removeAll { $0.id == id }
                exceptions.removeAll { $0.eventID == id }
            }
        }
    }

    private mutating func edit(_ id: String, _ change: (inout CalendarEvent) -> Void) {
        guard let index = events.firstIndex(where: { $0.id == id }) else { return }
        change(&events[index])
    }

    /// The day before `date`, unless that's before the series even starts.
    static func cutoff(_ event: CalendarEvent, before date: String) -> String? {
        let day = CalendarTimeline.addingDays(-1, to: date)
        return day >= event.date ? day : nil
    }

    /// Categories the edit didn't set are the classifier's, so they stay.
    static func edited(_ event: CalendarEvent, with draft: CalendarDraft, date: String) -> CalendarEvent {
        var copy = draft.event
        copy.date = date
        copy.categoryTags = draft.categoryTags ?? event.categoryTags
        return CalendarEvent(id: event.id, title: copy.title, description: copy.description, date: copy.date,
                             time: copy.time, endTime: copy.endTime, allDay: copy.allDay, tags: copy.tags,
                             categoryTags: copy.categoryTags, repeatFreq: copy.repeatFreq,
                             repeatInterval: copy.repeatInterval, repeatByweekday: copy.repeatByweekday,
                             repeatUntil: copy.repeatUntil)
    }
}

public protocol CalendarTransport {
    func send(_ change: CalendarChange) async throws
}

/// Sends the queue in order. A refusal is dropped and reported; anything else
/// (offline, signed out, a server error) stops with the rest still queued.
public final class CalendarSyncer {
    private let outbox: CalendarOutbox

    public init(outbox: CalendarOutbox) { self.outbox = outbox }

    public func run(using transport: CalendarTransport) async throws -> [String] {
        var refused: [String] = []
        for op in try outbox.list() {
            try Task.checkCancellation()
            do {
                try await transport.send(op.change)
            } catch let refusal as TodoRefusal {
                // Removing something already gone is what was wanted.
                if !(refusal.status == 404 && op.change.isDelete) {
                    refused.append("The server didn't take \(op.change.subject): \(refusal.message)")
                }
            }
            try outbox.remove(op)
        }
        return refused
    }
}

/// One change as an HTTP request: what the transport sends, testable without one.
struct CalendarRoute {
    let method: String
    let path: String
    let body: Data?
}

extension JournalAPI: CalendarTransport {
    public func send(_ change: CalendarChange) async throws {
        try await calendarCall(try Self.calendarRoute(change))
    }

    private static func encode<T: Encodable>(_ body: T) throws -> Data { try JSONEncoder().encode(body) }

    static func calendarRoute(_ change: CalendarChange) throws -> CalendarRoute {
        switch change {
        case let .create(draft):
            return CalendarRoute(method: "POST", path: "api/calendar", body: try encode(draft))
        case let .update(draft):
            return CalendarRoute(method: "PATCH", path: "api/calendar/\(try calendarPath(draft.id))", body: try encode(draft))
        case let .updateFrom(date, newID, draft):
            struct Split: Encodable {
                let newId: String
                let draft: CalendarDraft
                func encode(to encoder: Encoder) throws {
                    try draft.encode(to: encoder)
                    var c = encoder.container(keyedBy: Key.self)
                    try c.encode(newId, forKey: .newId)
                }
                enum Key: String, CodingKey { case newId }
            }
            return CalendarRoute(method: "PATCH", path: "api/calendar/\(try calendarPath(draft.id))/from/\(try calendarPath(date))",
                                 body: try encode(Split(newId: newID, draft: draft)))
        case let .delete(id):
            return CalendarRoute(method: "DELETE", path: "api/calendar/\(try calendarPath(id))", body: nil)
        case let .skip(id, date):
            return CalendarRoute(method: "DELETE", path: "api/calendar/\(try calendarPath(id))/occurrence/\(try calendarPath(date))",
                                 body: nil)
        case let .endSeries(id, date):
            return CalendarRoute(method: "DELETE", path: "api/calendar/\(try calendarPath(id))/from/\(try calendarPath(date))",
                                 body: nil)
        case let .reschedule(id, occurrence?, date, time, endTime):
            // newDate only when it really moved, as the web sends it.
            var body = ["newTime": time, "newEndTime": endTime]
            if date != occurrence { body["newDate"] = date }
            return CalendarRoute(method: "PATCH",
                                 path: "api/calendar/\(try calendarPath(id))/occurrence/\(try calendarPath(occurrence))",
                                 body: try encode(body))
        case let .reschedule(id, nil, date, time, endTime):
            return CalendarRoute(method: "PATCH", path: "api/calendar/\(try calendarPath(id))",
                                 body: try encode(["date": date, "time": time, "endTime": endTime]))
        case let .categories(id, tags):
            return CalendarRoute(method: "PATCH", path: "api/calendar/\(try calendarPath(id))",
                                 body: try encode(["categoryTags": CalendarCategory.all.filter(tags.contains)]))
        case let .sleep(date, wake, sleep):
            // Both ends every time, null included: the body is the day's whole manual state.
            struct Ends: Encodable {
                let wake: String?, sleep: String?
                func encode(to encoder: Encoder) throws {
                    var c = encoder.container(keyedBy: CodingKeys.self)
                    try c.encode(wake, forKey: .wake)
                    try c.encode(sleep, forKey: .sleep)
                }
                enum CodingKeys: String, CodingKey { case wake, sleep }
            }
            return CalendarRoute(method: "PUT", path: "api/calendar/sleep/\(try calendarPath(date))",
                                 body: try encode(Ends(wake: wake, sleep: sleep)))
        }
    }

    /// Ids and dates go into the path, so nothing but their own characters may.
    static func calendarPath(_ part: String) throws -> String {
        guard !part.isEmpty, part.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-") }) else {
            throw CaptureError.invalidResponse
        }
        return part
    }

    private func calendarCall(_ route: CalendarRoute) async throws {
        var req = request(route.path, method: route.method)
        if let body = route.body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let failure = HTTPFailure(status: http.statusCode)
            // A 4xx other than sign-in or rate limiting is final, as for to-dos.
            if (400..<500).contains(http.statusCode), ![401, 403].contains(http.statusCode), !failure.retryAutomatically {
                throw TodoRefusal(status: http.statusCode, message: Self.errorMessage(data) ?? "HTTP \(http.statusCode)")
            }
            throw failure
        }
    }
}
