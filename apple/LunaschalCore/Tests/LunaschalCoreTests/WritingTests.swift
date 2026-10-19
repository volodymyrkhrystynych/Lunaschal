import Foundation
import XCTest
@testable import LunaschalCore

final class WritingTests: XCTestCase {
    func testANoteDecodesThePinTheServerStoresAsAnInteger() throws {
        let listed = try JSONDecoder().decode([WritingNote].self, from: Data("""
        [{"id": "N1", "projectId": "P", "title": "Mara", "docType": "character", "pinned": 1,
          "createdAt": "2026-10-05T12:00:00+00:00", "updatedAt": "2026-10-05T12:00:00+00:00"},
         {"id": "N2", "projectId": "P", "title": "Outline", "docType": "outline", "pinned": 0,
          "createdAt": "2026-10-05T12:00:00+00:00", "updatedAt": "2026-10-05T12:00:00+00:00"}]
        """.utf8))
        XCTAssertEqual(listed.map(\.pinned), [true, false])
        XCTAssertNil(listed[0].content)

        let single = try JSONDecoder().decode(WritingNote.self, from: Data("""
        {"id": "N1", "projectId": "P", "title": "Mara", "content": "Docks.", "pinned": true}
        """.utf8))
        XCTAssertEqual(single.content, "Docks.")
        XCTAssertEqual(single.docType, "note")
        XCTAssertTrue(single.pinned)
    }

    func testAnEditSendsOnlyWhatChanged() throws {
        let json = String(decoding: try JSONEncoder().encode(WritingNoteEdit(pinned: true)), as: UTF8.self)
        XCTAssertEqual(json, #"{"pinned":true}"#)
    }

    func testRevisionsSayWhatTheyUndo() throws {
        let revisions = try JSONDecoder().decode([WritingNoteRevision].self, from: Data("""
        [{"id": "R1", "docId": "N1", "title": "Mara", "content": "Docks.", "source": "accept",
          "messageId": "M", "createdAt": "2026-10-05T12:00:00+00:00"}]
        """.utf8))
        XCTAssertEqual(revisions.first?.label, "Before an accepted proposal")
    }

    func testADiscussionWithNoTitleSaysSo() {
        XCTAssertEqual(WritingDiscussion(id: "D", title: "  ").displayTitle, "Untitled discussion")
        XCTAssertEqual(WritingDiscussion(id: "D", title: "Plot").displayTitle, "Plot")
    }

    func testNoteCardsReadLikeTheDesktops() throws {
        let meta = ChatMeta(#"""
        {"proposals": [
          {"id": "p1", "kind": "writing_note", "status": "pending",
           "data": {"title": "Harbourmaster", "docType": "character", "content": "Corrupt."}},
          {"id": "p2", "kind": "writing_note_update", "status": "accepted",
           "data": {"noteId": "N1", "title": "Mara", "mode": "append", "content": "Swims."}}
        ]}
        """#)
        XCTAssertEqual(meta.proposals.map(\.headline), ["Add to the notes?", "Update this note?"])
        XCTAssertEqual(meta.proposals[0].acceptLabel, "Add Note")
        XCTAssertEqual(meta.proposals[1].resolvedLabel,
                       "Updated note “Mara” — the earlier version is in its history")
    }

    func testTheNewToolsHaveLabels() {
        func step(_ json: String) -> AgentStep { AgentStep(JSONValue.parse(json)!) }
        XCTAssertEqual(step(#"{"tool": "propose_note", "ok": true, "arg": "new character note \"Mara\""}"#).label,
                       #"Staged new character note "Mara""#)
        XCTAssertEqual(step(#"{"tool": "canon_search", "ok": true, "arg": "lock", "count": 2}"#).label,
                       #"Searched the canon wiki for "lock" (2)"#)
        XCTAssertEqual(step(#"{"tool": "canon_read", "ok": true, "title": "Mara Vell"}"#).label,
                       "Read canon: Mara Vell")
    }
}
