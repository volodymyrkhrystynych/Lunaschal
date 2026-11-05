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
}
