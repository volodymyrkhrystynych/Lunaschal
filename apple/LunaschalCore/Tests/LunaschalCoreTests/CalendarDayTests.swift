import XCTest
@testable import LunaschalCore

final class CalendarDayTests: XCTestCase {
    private func timed(_ id: String, _ date: String, _ time: String?, _ end: String? = nil,
                       allDay: Bool = false) -> CalendarEvent {
        CalendarEvent(id: id, title: id, date: date, time: time, endTime: end, allDay: allDay)
    }

    func testTimelineRunsFromFourAmToFourAm() {
        XCTAssertEqual(CalendarTimeline.offset(fromWall: 4 * 60), 0)
        XCTAssertEqual(CalendarTimeline.offset(fromWall: 0), 20 * 60)
        XCTAssertEqual(CalendarTimeline.offset(fromWall: 3 * 60 + 59), 1439)
        XCTAssertEqual(CalendarTimeline.wall(fromOffset: 1440), 4 * 60)
        XCTAssertTrue(CalendarTimeline.isAfterMidnight(20 * 60))
        XCTAssertFalse(CalendarTimeline.isAfterMidnight(20 * 60 - 1))
        XCTAssertEqual(CalendarTimeline.displayHours.first, 4)
        XCTAssertEqual(CalendarTimeline.displayHours.last, 3)
        XCTAssertEqual(CalendarTimeline.hourLabel(0), "12am")
        XCTAssertEqual(CalendarTimeline.hourLabel(13), "1pm")
    }

    func testDurationWrapsPastMidnightAndDefaultsWithoutAnEnd() {
        XCTAssertEqual(CalendarTimeline.duration(from: "21:20", to: "00:30"), 190)
        XCTAssertEqual(CalendarTimeline.duration(from: "09:00", to: "10:15"), 75)
        XCTAssertEqual(CalendarTimeline.duration(from: "09:00", to: nil), 30)
    }

    func testPlanTakesTheNightFromTheNextDateAndLeavesItsMorning() {
        let events = [
            timed("morning", "2026-10-05", "09:00", "10:00"),
            timed("too-early", "2026-10-05", "02:00", "03:00"),   // the previous night's
            timed("late", "2026-10-06", "01:30", "02:00"),        // tonight's
            timed("tomorrow", "2026-10-06", "09:00"),
            timed("all-day", "2026-10-05", nil, allDay: true),
            timed("untimed", "2026-10-05", nil),
            timed("next-all-day", "2026-10-06", nil, allDay: true),
        ]
        let plan = CalendarTimeline.plan(day: "2026-10-05", events: events, exceptions: [])
        XCTAssertEqual(plan.timed.map(\.occurrence.event.id), ["morning", "late"])
        XCTAssertEqual(plan.timed.map(\.startMinutes), [300, 1290])
        XCTAssertEqual(plan.timed.map(\.endMinutes), [360, 1320])
        XCTAssertEqual(Set(plan.allDay.map(\.event.id)), ["all-day", "untimed"])
    }

    func testOverlapsGetSideBySideLanesLongestLeftmost() {
        let events = [
            timed("short", "2026-10-05", "09:30", "10:00"),
            timed("long", "2026-10-05", "09:00", "12:00"),
            timed("after", "2026-10-05", "12:00", "13:00"),
            timed("also", "2026-10-05", "09:45", "10:30"),
        ]
        let lanes = Dictionary(uniqueKeysWithValues: CalendarTimeline.plan(day: "2026-10-05", events: events, exceptions: [])
            .timed.map { ($0.occurrence.event.id, $0.lane) })
        XCTAssertEqual(lanes, ["long": 0, "also": 1, "short": 2, "after": 0])
    }

    func testPlusUsesNowOnTodayAndEightOtherwise() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        func at(_ text: String) -> Date {
            let format = DateFormatter()
            format.calendar = calendar; format.timeZone = calendar.timeZone
            format.dateFormat = "yyyy-MM-dd HH:mm"
            return format.date(from: text)!
        }
        let morning = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-05 14:10"), calendar: calendar)
        // Ends now: an event is usually logged once it's over.
        XCTAssertEqual(morning.date, "2026-10-05"); XCTAssertEqual(morning.time, "13:40"); XCTAssertEqual(morning.endTime, "14:10")
        // 01:20 is still the 5th's day, and lands on the 6th's date.
        let night = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-06 01:20"), calendar: calendar)
        XCTAssertEqual(night.date, "2026-10-06"); XCTAssertEqual(night.time, "00:50"); XCTAssertEqual(night.endTime, "01:20")
        // Started before midnight, it keeps the day's own date.
        let across = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-06 00:10"), calendar: calendar)
        XCTAssertEqual(across.date, "2026-10-05"); XCTAssertEqual(across.time, "23:40"); XCTAssertEqual(across.endTime, "00:10")
        // Never above the top of the timeline, and never shorter than a drag allows.
        let early = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-05 04:20"), calendar: calendar)
        XCTAssertEqual(early.time, "04:00"); XCTAssertEqual(early.endTime, "04:20")
        let short = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-05 04:10"), calendar: calendar)
        XCTAssertEqual(short.time, "04:00"); XCTAssertEqual(short.endTime, "04:15")
        // At the very top of the day "now" is no length at all, which would read as
        // an event ending the next day.
        let top = CalendarTimeline.newEventSlot(day: "2026-10-05", now: at("2026-10-05 04:00"), calendar: calendar)
        XCTAssertEqual(top.date, "2026-10-05"); XCTAssertEqual(top.time, "04:00"); XCTAssertEqual(top.endTime, "04:15")
        let other = CalendarTimeline.newEventSlot(day: "2026-10-09", now: at("2026-10-05 14:10"), calendar: calendar)
        XCTAssertEqual(other.date, "2026-10-09"); XCTAssertEqual(other.time, "08:00")
    }

    func testDraftEncodesAsTheRoutesReadItWithExplicitNulls() throws {
        let draft = CalendarDraft(id: "01J00000000000000000000000", title: "  Gym ", date: "2026-10-05",
                                  time: "07:00", endTime: "08:00", description: " ", tags: CalendarDraft.tags(from: "health, , gym"),
                                  repeatFreq: "weekly", repeatInterval: 0, repeatByweekday: [3, 1])
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(draft)) as! [String: Any]
        XCTAssertEqual(body["title"] as? String, "Gym")
        XCTAssertEqual(body["tags"] as? [String], ["health", "gym"])
        XCTAssertEqual(body["repeatByweekday"] as? [Int], [1, 3])
        XCTAssertEqual(body["repeatInterval"] as? Int, 1)
        // An edit must be able to clear a field, so absent is null, not missing.
        XCTAssertTrue(body["description"] is NSNull)
        XCTAssertTrue(body["repeatUntil"] is NSNull)
        XCTAssertEqual(draft.event.repeatByweekday, "1,3")
        XCTAssertEqual(try JSONDecoder().decode(CalendarDraft.self, from: JSONEncoder().encode(draft)), draft)

        let allDay = CalendarDraft(title: "Trip", date: "2026-10-05", time: "07:00", allDay: true, repeatFreq: "daily",
                                   repeatByweekday: [1])
        XCTAssertNil(allDay.time)
        XCTAssertNil(allDay.repeatByweekday)
        XCTAssertNotNil(CalendarDraft(title: " ", date: "2026-10-05").problem)
        XCTAssertNotNil(CalendarDraft(title: "x", date: "2026-10-05", repeatFreq: "daily", repeatUntil: "2026-10-01").problem)
        XCTAssertNil(CalendarDraft(title: "x", date: "2026-10-05").problem)
        // A spoken event gets its title from what was said.
        XCTAssertNil(CalendarDraft(title: " ", date: "2026-10-05", clipIDs: ["a"]).problem)
    }

    func testEditingADraftRoundTripsTheEvent() {
        let event = CalendarEvent(id: "e1", title: "Gym", description: "legs", date: "2026-10-05", time: "07:00",
                                  endTime: "08:00", tags: ["health"], categoryTags: ["exercise"], repeatFreq: "weekly",
                                  repeatInterval: 2, repeatByweekday: "1,3", repeatUntil: "2026-12-01")
        XCTAssertEqual(CalendarDraft(editing: event).event, event)
    }

    private let gym = CalendarEvent(id: "gym", title: "Gym", date: "2026-10-01", time: "07:00", endTime: "08:00",
                                    categoryTags: ["exercise"], repeatFreq: "daily")

    private func overlay(_ changes: [CalendarChange], events: [CalendarEvent]? = nil,
                         exceptions: [CalendarException] = []) -> CalendarOverlay {
        CalendarOverlay(events: events ?? [gym], exceptions: exceptions, pending: changes.map { CalendarOp(change: $0) })
    }

    private func days(_ o: CalendarOverlay, _ start: String, _ end: String) -> [String] {
        CalendarExpansion.expand(o.events, exceptions: o.exceptions, start: start, end: end).map { "\($0.date) \($0.event.title)" }
    }

    func testPendingCreatesShowUntilTheReplicaHasThem() {
        let draft = CalendarDraft(title: "Dentist", date: "2026-10-05", time: "10:00")
        XCTAssertEqual(overlay([.create(draft)], events: []).events.map(\.title), ["Dentist"])
        let server = CalendarEvent(id: draft.id, title: "Dentist (server)", date: "2026-10-05", time: "10:00")
        let both = overlay([.create(draft)], events: [server])
        XCTAssertEqual(both.events.map(\.title), ["Dentist (server)"])
        XCTAssertEqual(both.pendingIDs, [draft.id])
    }

    func testEditAllRewritesEveryOccurrenceAndKeepsCategories() {
        var draft = CalendarDraft(editing: gym)
        draft.title = "Run"
        let o = overlay([.update(draft)])
        XCTAssertEqual(days(o, "2026-10-01", "2026-10-02"), ["2026-10-01 Run", "2026-10-02 Run"])
        XCTAssertEqual(o.events.first?.categoryTags, ["exercise"])
    }

    func testEditFromADateSplitsTheSeriesLikeTheServer() {
        var draft = CalendarDraft(editing: gym)
        draft.title = "Run"
        let skip = CalendarException(eventID: "gym", date: "2026-10-06", action: "skip")
        let o = overlay([.updateFrom(date: "2026-10-04", newID: "new", draft)], exceptions: [skip])
        XCTAssertEqual(days(o, "2026-10-02", "2026-10-07"),
                       ["2026-10-02 Gym", "2026-10-03 Gym", "2026-10-04 Run", "2026-10-05 Run", "2026-10-07 Run"])
        XCTAssertEqual(o.pendingIDs, ["gym", "new"])
        // From the very first day there's nothing earlier to keep: edited in place.
        let inPlace = overlay([.updateFrom(date: "2026-10-01", newID: "new", draft)])
        XCTAssertEqual(inPlace.events.map(\.id), ["gym"])
        XCTAssertEqual(days(inPlace, "2026-10-01", "2026-10-01"), ["2026-10-01 Run"])
        // A replay after the split landed doesn't split again.
        let landed = overlay([.updateFrom(date: "2026-10-04", newID: "new", draft)],
                             events: [gym, CalendarEvent(id: "new", title: "Run", date: "2026-10-04")])
        XCTAssertEqual(landed.events.count, 2)
        XCTAssertNil(landed.events.first?.repeatUntil)
    }

    func testDeleteSkipAndEndSeries() {
        XCTAssertEqual(days(overlay([.skip(id: "gym", date: "2026-10-02")]), "2026-10-01", "2026-10-03"),
                       ["2026-10-01 Gym", "2026-10-03 Gym"])
        XCTAssertEqual(days(overlay([.endSeries(id: "gym", date: "2026-10-03")]), "2026-10-01", "2026-10-05"),
                       ["2026-10-01 Gym", "2026-10-02 Gym"])
        XCTAssertEqual(overlay([.endSeries(id: "gym", date: "2026-10-01")]).events, [])
        XCTAssertEqual(overlay([.delete(id: "gym")], exceptions: [CalendarException(eventID: "gym", date: "2026-10-02", action: "skip")]),
                       CalendarOverlay(events: [], exceptions: [], pending: []).with(pending: ["gym"]))
    }

    func testSyncerKeepsQueueOnFailureAndDropsARefusal() async throws {
        final class Transport: CalendarTransport {
            var outcomes: [Error?]
            init(_ outcomes: [Error?]) { self.outcomes = outcomes }
            func send(_ change: CalendarChange, audio: URL?) async throws {
                if let error = outcomes.removeFirst() { throw error }
            }
        }
        let outbox = try CalendarOutbox(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        for title in ["A", "B", "C"] { try outbox.append(.create(CalendarDraft(title: title, date: "2026-10-05"))) }
        let syncer = CalendarSyncer(outbox: outbox)
        let first = Transport([nil, TodoRefusal(status: 400, message: "bad"), URLError(.notConnectedToInternet)])
        do { _ = try await syncer.run(using: first); XCTFail("offline should stop the pass") } catch {}
        XCTAssertEqual(try outbox.list().map(\.change.subject), ["“C”"])
        let none = try await syncer.run(using: Transport([nil]))
        XCTAssertEqual(none, [])
        XCTAssertEqual(try outbox.list(), [])
        try outbox.append(.create(CalendarDraft(title: "D", date: "2026-10-05")))
        try outbox.append(.delete(id: "gone"))
        let refused = try await syncer.run(using: Transport([TodoRefusal(status: 400, message: "bad"),
                                                            TodoRefusal(status: 404, message: "Not found")]))
        // A delete of something already gone is not worth reporting.
        XCTAssertEqual(refused, ["The server didn't take “D”: bad"])
    }

    func testEndsNextDayWhenTheEndIsNotAfterTheStart() {
        func draft(_ start: String, _ end: String, allDay: Bool = false) -> CalendarDraft {
            CalendarDraft(title: "x", date: "2026-10-05", time: start, endTime: end, allDay: allDay)
        }
        XCTAssertFalse(draft("13:00", "14:00").endsNextDay)
        XCTAssertTrue(draft("15:00", "14:00").endsNextDay)
        XCTAssertTrue(draft("23:30", "00:30").endsNextDay)
        XCTAssertFalse(draft("15:00", "14:00", allDay: true).endsNextDay)
    }

    private func spokenOutbox() throws -> (CalendarOutbox, CalendarDraft) {
        let outbox = try CalendarOutbox(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        let recorded = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data("clip".utf8).write(to: recorded)
        let clip = try outbox.stageClip(from: recorded)
        return (outbox, CalendarDraft(title: "", date: "2026-10-05", clipIDs: [clip]))
    }

    func testASpokenCreateTellsTheServerAClipFollowsAndKeepsItOnReplay() throws {
        let (outbox, draft) = try spokenOutbox()
        try outbox.append(.create(draft))
        // What the queue reads back after a restart is what goes up.
        guard case let .create(stored)? = try outbox.list().first?.change else { return XCTFail("no create") }
        let body = try XCTUnwrap(JournalAPI.calendarRoute(.create(stored)).body)
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertEqual(sent["pendingClips"] as? Int, 1)
        XCTAssertEqual(sent["title"] as? String, "")
        XCTAssertNil(sent["clipIDs"])
    }

    func testATypedEventSaysNothingAboutClips() throws {
        let body = try XCTUnwrap(JournalAPI.calendarRoute(.create(CalendarDraft(title: "x", date: "2026-10-05"))).body)
        let sent = try XCTUnwrap(JSONSerialization.jsonObject(with: body) as? [String: Any])
        XCTAssertNil(sent["pendingClips"])
    }

    func testClipsAreQueuedBehindTheirEventAndLeaveWithTheirUpload() async throws {
        final class Transport: CalendarTransport {
            var sent: [(CalendarChange, URL?)] = []
            func send(_ change: CalendarChange, audio: URL?) async throws { sent.append((change, audio)) }
        }
        let (outbox, draft) = try spokenOutbox()
        let clip = draft.clipIDs[0]
        try outbox.append(.create(draft))
        XCTAssertEqual(try outbox.list().map(\.change.subject), ["“”", "a recording"])
        XCTAssertEqual(CalendarOverlay(events: [], exceptions: [], pending: try outbox.list()).pendingIDs, [draft.id])

        let transport = Transport()
        let refused = try await CalendarSyncer(outbox: outbox).run(using: transport)
        XCTAssertEqual(refused, [])
        XCTAssertNil(transport.sent[0].1)
        XCTAssertEqual(transport.sent[1].0, .recording(eventID: draft.id, clipID: clip))
        XCTAssertEqual(transport.sent[1].1?.deletingPathExtension().lastPathComponent, clip)
        XCTAssertFalse(FileManager.default.fileExists(atPath: outbox.clipURL(clip).path))
    }

    func testAClipForADeletedEventIsDroppedQuietly() async throws {
        final class Gone: CalendarTransport {
            func send(_ change: CalendarChange, audio: URL?) async throws {
                throw TodoRefusal(status: 404, message: "Not found")
            }
        }
        let (outbox, draft) = try spokenOutbox()
        try outbox.append(.recording(eventID: draft.id, clipID: draft.clipIDs[0]))
        let refused = try await CalendarSyncer(outbox: outbox).run(using: Gone())
        XCTAssertEqual(refused, [])
        XCTAssertEqual(try outbox.list(), [])
    }

    func testAMissingClipIsReportedInsteadOfBlockingTheQueue() async throws {
        final class Fails: CalendarTransport {
            func send(_ change: CalendarChange, audio: URL?) async throws { XCTFail("nothing to send") }
        }
        let (outbox, draft) = try spokenOutbox()
        outbox.discardClip(draft.clipIDs[0])
        try outbox.append(.recording(eventID: draft.id, clipID: draft.clipIDs[0]))
        let refused = try await CalendarSyncer(outbox: outbox).run(using: Fails())
        XCTAssertEqual(refused.count, 1)
        XCTAssertEqual(try outbox.list(), [])
    }

    func testLaunchSweepKeepsOnlyQueuedClips() throws {
        let (outbox, draft) = try spokenOutbox()
        let queued = draft.clipIDs[0]
        try outbox.append(.create(draft))
        let recorded = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data("clip".utf8).write(to: recorded)
        let orphan = try outbox.stageClip(from: recorded)
        try outbox.sweepClips()
        XCTAssertTrue(FileManager.default.fileExists(atPath: outbox.clipURL(queued).path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: outbox.clipURL(orphan).path))
    }

    func testARecordingIsNotAnUpdateAndOnlyCreatesAndUpdatesCarryClips() throws {
        let route = try JournalAPI.calendarRoute(.recording(eventID: "a", clipID: "b"))
        XCTAssertEqual(route.path, "api/calendar/recordings")
        XCTAssertEqual(route.method, "POST")
        let draft = CalendarDraft(title: "x", date: "2026-10-05", clipIDs: ["c"])
        XCTAssertEqual(CalendarChange.update(draft).clipUploads, [.recording(eventID: draft.id, clipID: "c")])
        XCTAssertEqual(CalendarChange.delete(id: "a").clipUploads, [])
    }

    func testAnOutboxOfBareDraftsReadsAsCreates() throws {
        let outbox = try CalendarOutbox(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        let draft = CalendarDraft(title: "Dentist", date: "2026-10-05", time: "10:00")
        try JSONEncoder().encode([draft]).write(to: outbox.root.appendingPathComponent("outbox.json"))
        XCTAssertEqual(try outbox.list().map(\.change), [.create(draft)])
        try outbox.append(.delete(id: draft.id))
        XCTAssertEqual(try outbox.list().map(\.change), [.create(draft), .delete(id: draft.id)])
    }

    func testPathPartsAreOnlyIdsAndDates() {
        XCTAssertEqual(try JournalAPI.calendarPath("2026-10-05"), "2026-10-05")
        XCTAssertThrowsError(try JournalAPI.calendarPath("../settings"))
        XCTAssertThrowsError(try JournalAPI.calendarPath(""))
    }
}

private extension CalendarOverlay {
    func with(pending: Set<String>) -> CalendarOverlay { var copy = self; copy.pendingIDs = pending; return copy }
}
