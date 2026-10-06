import XCTest
@testable import LunaschalCore

final class JournalFeedTests: XCTestCase {
    private var utc: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        return calendar
    }

    private func at(_ iso: String) -> Date { JournalTimestamp.parse(iso)! }

    private func occurrence(_ event: CalendarEvent) -> CalendarOccurrence {
        CalendarOccurrence(event: event, occurrenceDate: event.date, date: event.date, time: event.time, endTime: event.endTime)
    }

    func testServerTimestampsParseWithOrWithoutFractions() {
        XCTAssertEqual(JournalTimestamp.parse("2026-10-05T12:00:00+00:00")?.timeIntervalSince1970, 1_791_201_600)
        XCTAssertEqual(JournalTimestamp.parse("2026-10-05T12:00:00.500Z")?.timeIntervalSince1970, 1_791_201_600.5)
        XCTAssertNil(JournalTimestamp.parse("yesterday"))
    }

    func testAttachmentsReadTheirRowAndKeepTheServersOrder() {
        func row(_ id: String, _ data: [String: JSONValue]) -> SyncChange {
            SyncChange(revision: 1, collection: "journal_attachments", id: id, deleted: false, data: data)
        }
        let rows = [
            row("b", ["entryId": .string("e1"), "kind": .string("audio"), "position": .number(1),
                      "transcript": .string("  hello  ")]),
            row("a", ["entryId": .string("e1"), "kind": .string("image"), "position": .number(0)]),
            row("c", ["entryId": .string("e2"), "kind": .string("file"), "mime": .string("video/mp4")]),
            row("d", ["entryId": .string("e2"), "kind": .string("youtube"),
                      "sourceUrl": .string("https://youtu.be/x"), "transcript": .string("   ")]),
            SyncChange(revision: 1, collection: "journal_attachments", id: "gone", deleted: true, data: nil),
        ]
        let grouped = JournalAttachmentItem.grouped(rows)
        XCTAssertEqual(grouped["e1"]?.map(\.id), ["a", "b"])
        XCTAssertEqual(grouped["e1"]?.last?.transcript, "hello")
        XCTAssertEqual(grouped["e1"]?.first?.media, .image)
        XCTAssertEqual(grouped["e2"]?.first?.media, .video, "an old 'file' row is read by its MIME type")
        XCTAssertEqual(grouped["e2"]?.last?.media, .youtube)
        XCTAssertNil(grouped["e2"]?.last?.transcript, "blank text is no text")
        XCTAssertEqual(grouped["e2"]?.last?.sourceURL, "https://youtu.be/x")
    }

    func testABrowserClipIsKnownToNeedThePhonesCopy() {
        func clip(_ mime: String, _ name: String = "") -> JournalAttachmentItem {
            JournalAttachmentItem(id: "a", entryID: "e", kind: "audio", name: name, mime: mime)
        }
        XCTAssertFalse(clip("audio/webm;codecs=opus").phonePlayable)
        XCTAssertFalse(clip("audio/ogg").phonePlayable)
        XCTAssertFalse(clip("", "memo.webm").phonePlayable)
        XCTAssertTrue(clip("audio/mp4").phonePlayable)
        XCTAssertTrue(clip("", "Morning walk").phonePlayable, "a name is a title, not a file name")
        XCTAssertEqual(clip("audio/webm").playerMIME, "audio/mp4")
        XCTAssertEqual(JournalAttachmentItem(id: "y", entryID: "e", kind: "youtube").playerMIME, "video/mp4")
    }

    func testACategorisedEventWrapsTheEntriesWrittenDuringIt() {
        let times = ["2026-10-05T19:00:00Z", "2026-10-05T17:30:00Z", "2026-10-05T17:10:00Z",
                     "2026-10-05T16:00:00Z"].map(at)
        let walk = occurrence(CalendarEvent(id: "walk", title: "Walk", date: "2026-10-05", time: "17:00",
                                            endTime: "18:00", categoryTags: ["outside"]))
        let unsorted = occurrence(CalendarEvent(id: "call", title: "Call", date: "2026-10-05", time: "15:30",
                                                endTime: "16:30"))
        let spans = JournalEventGroups.spans(times: times, occurrences: [walk, unsorted], calendar: utc)
        XCTAssertEqual(spans.map(\.occurrence.event.id), ["walk"], "no category, no border, as on the web")
        XCTAssertEqual(spans.first?.start, 1)
        XCTAssertEqual(spans.first?.end, 2)
    }

    func testEventsWithoutAnEndOrPastMidnightOrAllDay() {
        let noEnd = occurrence(CalendarEvent(id: "n", title: "N", date: "2026-10-05", time: "10:00",
                                             categoryTags: ["work"]))
        XCTAssertEqual(JournalEventGroups.window(noEnd, calendar: utc)?.upperBound, at("2026-10-05T10:30:00Z"),
                       "a start alone is the default half hour")
        let late = occurrence(CalendarEvent(id: "l", title: "L", date: "2026-10-05", time: "23:00",
                                            endTime: "01:00", categoryTags: ["leisure"]))
        XCTAssertEqual(JournalEventGroups.window(late, calendar: utc)?.upperBound, at("2026-10-06T01:00:00Z"))
        let allDay = occurrence(CalendarEvent(id: "a", title: "A", date: "2026-10-05", allDay: true,
                                              categoryTags: ["family"]))
        let day = JournalEventGroups.window(allDay, calendar: utc)
        XCTAssertEqual(day?.lowerBound, at("2026-10-05T04:00:00Z"))
        XCTAssertTrue(day?.contains(at("2026-10-06T01:00:00Z")) == true, "1am still belongs to the day before")
        XCTAssertFalse(day?.contains(at("2026-10-06T04:00:00Z")) == true)
        XCTAssertNil(JournalEventGroups.window(occurrence(CalendarEvent(id: "u", title: "U", date: "2026-10-05")),
                                               calendar: utc))
    }

    func testAnItemSitsInsideOneBorderNeverTwo() {
        let times = ["2026-10-05T12:50:00Z", "2026-10-05T12:20:00Z", "2026-10-05T11:10:00Z"].map(at)
        let lunch = occurrence(CalendarEvent(id: "lunch", title: "Lunch", date: "2026-10-05", time: "12:00",
                                             endTime: "13:00", categoryTags: ["family"]))
        let day = occurrence(CalendarEvent(id: "day", title: "Day", date: "2026-10-05", allDay: true,
                                           categoryTags: ["work"]))
        let spans = JournalEventGroups.spans(times: times, occurrences: [lunch, day], calendar: utc)
        XCTAssertEqual(spans.map(\.occurrence.event.id), ["day"], "the wider run starting first keeps its items")
        let later = occurrence(CalendarEvent(id: "later", title: "Later", date: "2026-10-05", time: "11:00",
                                             endTime: "11:30", categoryTags: ["work"]))
        let both = JournalEventGroups.spans(times: times, occurrences: [later, lunch], calendar: utc)
        XCTAssertEqual(both.map(\.occurrence.event.id), ["lunch", "later"])
        XCTAssertEqual(both.map(\.start), [0, 2])
    }

    func testTheEventRangeCoversTheFeedsDaysWithMargin() {
        let range = JournalEventGroups.dayRange([at("2026-10-05T02:00:00Z"), at("2026-10-07T12:00:00Z")], calendar: utc)
        XCTAssertEqual(range?.start, "2026-10-03")
        XCTAssertEqual(range?.end, "2026-10-08")
        XCTAssertNil(JournalEventGroups.dayRange([], calendar: utc))
    }
}

final class JournalAttachmentURLTests: XCTestCase {
    func testAttachmentURLsAskForThePhonesCopyOnlyWhenTold() throws {
        let api = try JournalAPI(server: URL(string: "https://host.example")!, token: "t", allowCellular: true)
        let id = ULID.make()
        XCTAssertEqual(try api.journalAttachmentURL(id).absoluteString,
                       "https://host.example/api/journal/attachments/\(id)/file")
        XCTAssertEqual(try api.journalAttachmentURL(id, playable: true).absoluteString,
                       "https://host.example/api/journal/attachments/\(id)/file?playable=1")
        XCTAssertEqual(try api.journalAttachmentURL(id, thumbnail: true, playable: true).absoluteString,
                       "https://host.example/api/journal/attachments/\(id)/thumbnail")
        XCTAssertThrowsError(try api.journalAttachmentURL("../../etc"))
    }
}
