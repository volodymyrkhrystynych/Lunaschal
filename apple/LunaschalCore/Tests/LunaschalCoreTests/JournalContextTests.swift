import Foundation
import XCTest
@testable import LunaschalCore

final class JournalContextTests: XCTestCase {
    func testServerPromptsDecodeAndUseAuthenticatedJournalMedia() throws {
        let prompts = try JSONDecoder().decode([JournalContextPrompt].self, from: Data("""
        [{"id":"01K00000000000000000000001","title":"Game night","createdAt":"2026-10-13T20:00:00Z",
          "content":"","rawContent":null,"contextPromptDay":"2026-10-14","attachments":[
          {"id":"clip","kind":"audio","name":"Game audio","mime":"audio/webm","position":1,"transcript":null,"description":"Music"},
          {"id":"photo","kind":"image","name":"Victory","mime":"image/jpeg","position":0,"transcript":"AI caption"}]}]
        """.utf8))
        let prompt = try XCTUnwrap(prompts.first)
        XCTAssertEqual(prompt.displayTitle, "Game night")
        XCTAssertEqual(prompt.media.map(\.id), ["photo", "clip"])
        XCTAssertTrue(prompt.media.allSatisfy { $0.entryID == prompt.id && $0.collection == "journal_attachments" })
        XCTAssertFalse(prompt.media[1].phonePlayable)
        XCTAssertEqual(prompt.media[1].playerMIME, "audio/mp4")
        XCTAssertEqual(prompt.createdAt, "2026-10-13T20:00:00Z")
    }

    func testResolutionUsesSameActionsAsWebAndDoesNotChangeDate() throws {
        let save = JournalContextResolution(action: .save, content: "  Playing with friends\n")
        let saved = try JSONSerialization.jsonObject(with: JSONEncoder().encode(save)) as? [String: String]
        XCTAssertEqual(saved, ["action": "save", "content": "Playing with friends"])
        let dismiss = JournalContextResolution(action: .dismiss, content: "Ignored")
        let dismissed = try JSONSerialization.jsonObject(with: JSONEncoder().encode(dismiss)) as? [String: String]
        XCTAssertEqual(dismissed, ["action": "dismiss"])
    }

    func testContextRecordingSurvivesRestartAndQueuesOnOriginalEntry() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        let originalID = ULID.make()
        let clip = try store.beginClip(transcribe: true, into: originalID)
        try Data([1, 2, 3]).write(to: store.clipURL(clip))
        try store.finishClip(clip.attachmentID)
        let reopened = try CaptureStore(root: root)
        let addition = try XCTUnwrap(reopened.commitAdditions(to: originalID, kind: .journal))
        XCTAssertEqual(addition.entryID, originalID)
        XCTAssertEqual(addition.clips.map(\.attachmentID), [clip.attachmentID])
        XCTAssertTrue(addition.clips[0].transcribe)
        XCTAssertTrue(try reopened.draft(for: originalID).isEmpty)
        XCTAssertEqual(try reopened.pendingAdditions(to: originalID).map(\.id), [addition.id])
        XCTAssertEqual(try Data(contentsOf: reopened.clipURL(addition.clips[0])), Data([1, 2, 3]))
    }

    // MARK: Today

    private var utc: Calendar { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "UTC")!; return c }
    private func at(_ iso: String) -> Date { ISO8601DateFormatter().date(from: iso)! }
    private func photo(_ type: String = "image/jpeg") -> CaptureFile { CaptureFile(name: "Photo", contentType: type) }
    private func capture(_ text: String = "", mode: CaptureMode = .text, files: [CaptureFile] = [],
                         clips: [CaptureClip] = [], links: [String] = [], entryID: String? = nil,
                         asTodo: Bool = false, state: CaptureState = .pending,
                         at date: String = "2026-11-16T12:00:00Z") -> Capture {
        var c = Capture(text: text, mode: mode, now: at(date), linkURLs: links, files: files, clips: clips,
                        entryID: entryID, asTodo: asTodo)
        c.state = state
        return c
    }
    private func prompt(_ id: String, _ createdAt: String, kinds: [String] = ["image"]) throws -> JournalContextPrompt {
        let attachments = kinds.enumerated().map { i, kind in
            "{\"id\":\"a\(i)\",\"kind\":\"\(kind)\",\"name\":\"x\",\"position\":\(i)}"
        }.joined(separator: ",")
        return try JSONDecoder().decode(JournalContextPrompt.self, from: Data(
            "{\"id\":\"\(id)\",\"createdAt\":\"\(createdAt)\",\"attachments\":[\(attachments)]}".utf8))
    }

    func testOnlyWordlessMediaCapturesNeedContext() {
        XCTAssertTrue(JournalContextToday.needsContext(capture(files: [photo()])))
        XCTAssertTrue(JournalContextToday.needsContext(capture(files: [photo(), photo("audio/m4a")])))
        XCTAssertTrue(JournalContextToday.needsContext(capture(clips: [CaptureClip(transcribe: false)])))
        XCTAssertTrue(JournalContextToday.needsContext(capture(mode: .record, state: .pending)))
        XCTAssertTrue(JournalContextToday.needsContext(capture(files: [photo()], state: .failed)))

        XCTAssertFalse(JournalContextToday.needsContext(capture("At the lake", files: [photo()])))
        XCTAssertFalse(JournalContextToday.needsContext(capture(" \n")), "nothing at all is not an entry")
        XCTAssertFalse(JournalContextToday.needsContext(capture(files: [photo()], clips: [CaptureClip(transcribe: true)])),
                       "a Transcribe clip is words on the way")
        XCTAssertFalse(JournalContextToday.needsContext(capture(mode: .transcribe, state: .pending)))
        XCTAssertFalse(JournalContextToday.needsContext(capture(files: [photo("video/quicktime")])))
        XCTAssertFalse(JournalContextToday.needsContext(capture(files: [photo()], links: ["https://youtu.be/x"])))
        XCTAssertFalse(JournalContextToday.needsContext(capture(files: [photo()], entryID: "existing")),
                       "an addition to an entry is not an entry")
        XCTAssertFalse(JournalContextToday.needsContext(capture(files: [photo()], state: .synced)),
                       "once uploaded, the server's list speaks for it")
        var saved = capture(files: [photo()]); saved.autoFile = true
        XCTAssertFalse(JournalContextToday.needsContext(saved),
                       "a Capture-tab Save may yet become a meal or a purchase")
        var food = Capture(now: at("2026-11-16T12:00:00Z"), files: [photo()]); food.state = .pending
        XCTAssertTrue(JournalContextToday.needsContext(food))
        XCTAssertFalse(JournalContextToday.needsContext(
            { var c = Capture(kind: .food, now: at("2026-11-16T12:00:00Z"), files: [photo()]); c.state = .pending; return c }()))
    }

    func testTodayMergesServerAndDeviceNewestFirstAndKeepsTheFourAMDay() throws {
        let day = "2026-11-16"
        let morning = try prompt("01SERVERMORNING", "2026-11-16T09:00:00Z")
        let lastNight = try prompt("01SERVERLATE", "2026-11-17T03:30:00Z")  // still the 16th's day
        let yesterday = try prompt("01YESTERDAY", "2026-11-16T03:59:00Z")   // the 15th's day
        let offline = capture(files: [photo()], at: "2026-11-16T15:00:00Z")
        let typed = capture("Words", files: [photo()], at: "2026-11-16T16:00:00Z")
        let items = JournalContextToday.items(prompts: [morning, lastNight, yesterday],
                                              captures: [offline, typed], day: day, calendar: utc)
        XCTAssertEqual(items.map(\.id), [lastNight.id, offline.id, morning.id])
        XCTAssertEqual(items[1].photoCount, 1)
        XCTAssertEqual(items[1].clipCount, 0)
    }

    func testJustUploadedStaysListedUntilTheServerHasLetItSettle() throws {
        let uploaded = capture(files: [photo()], state: .synced, at: "2026-11-16T12:00:00Z")
        func items(_ now: String, _ prompts: [JournalContextPrompt] = []) -> [JournalContextToday.Item] {
            JournalContextToday.items(prompts: prompts, captures: [uploaded], day: "2026-11-16",
                                      now: at(now), calendar: utc)
        }
        let waiting = items("2026-11-16T12:01:00Z")
        XCTAssertEqual(waiting, [.local(uploaded)])
        XCTAssertTrue(waiting[0].awaitingServer)
        // Past the settle time the server's list alone speaks for it.
        XCTAssertEqual(items("2026-11-16T12:02:01Z"), [])
        let served = try prompt(uploaded.id, "2026-11-16T12:00:00Z")
        XCTAssertEqual(items("2026-11-16T12:01:00Z", [served]), [.prompt(served)])
        // Words on it, or a Capture-tab Save, were never waiting.
        var saved = uploaded; saved.autoFile = true
        XCTAssertEqual(JournalContextToday.items(prompts: [], captures: [saved], day: "2026-11-16",
                                                 now: at("2026-11-16T12:01:00Z"), calendar: utc), [])
    }

    func testServerPromptDefersToTheDeviceWhileItsWordsAreStillUnsent() throws {
        // The photo reached the server first; the Transcribe clip is still here.
        var pending = capture(files: [photo()], clips: [CaptureClip(transcribe: true)])
        let served = try prompt(pending.id, "2026-11-16T12:00:00Z")
        XCTAssertEqual(JournalContextToday.items(prompts: [served], captures: [pending],
                                                 day: "2026-11-16", calendar: utc), [])
        // Wordless and still unsent: one row, the server's, which can be answered now.
        let wordless = capture(files: [photo()])
        let both = try prompt(wordless.id, "2026-11-16T12:00:00Z")
        XCTAssertEqual(JournalContextToday.items(prompts: [both], captures: [wordless],
                                                 day: "2026-11-16", calendar: utc), [.prompt(both)])
        // Uploaded: the server alone decides.
        pending.state = .synced
        XCTAssertEqual(JournalContextToday.items(prompts: [served], captures: [pending],
                                                 day: "2026-11-16", calendar: utc).map(\.id), [served.id])
    }
}
