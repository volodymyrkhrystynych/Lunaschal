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

    // MARK: Passage

    func testASelectionIsTrimmedToThePassage() {
        let text = "The wind rose.  The lamp went out.\nShe waited."
        // "  The lamp went out.\n" — a drag that caught the spaces and the newline.
        XCTAssertEqual(WritingPassage.selection(in: text, location: 14, length: 21), "The lamp went out.")
    }

    func testOffsetsAreUTF16LikeATextViews() {
        // The emoji is two UTF-16 units; the selection starts after it.
        let text = "🌊 The tide."
        XCTAssertEqual(WritingPassage.selection(in: text, location: 3, length: 9), "The tide.")
    }

    func testNothingToDiscussIsNil() {
        XCTAssertNil(WritingPassage.selection(in: "Some text", location: 2, length: 0))
        XCTAssertNil(WritingPassage.selection(in: "a   b", location: 1, length: 3))
        XCTAssertNil(WritingPassage.selection(in: "short", location: 3, length: 10))
        XCTAssertNil(WritingPassage.selection(in: "short", location: -1, length: 2))
        let long = String(repeating: "x", count: WritingPassage.maxCharacters + 1)
        XCTAssertNil(WritingPassage.selection(in: long, location: 0, length: long.utf16.count))
    }

    func testADiscussionRemembersItsPassage() throws {
        let listed = try JSONDecoder().decode([WritingDiscussion].self, from: Data("""
        [{"id": "D1", "title": "Rewrite: The wind…", "writingChapterId": "C1",
          "writingPassage": "The wind rose.", "mode": "chat"},
         {"id": "D2", "title": "Plot", "writingChapterId": null}]
        """.utf8))
        XCTAssertEqual(listed.map(\.writingPassage), ["The wind rose.", nil])
    }

    // MARK: Canon

    func testCanonSourcesSayWhereTheyAre() throws {
        let sources = try JSONDecoder().decode([WritingCanonSource].self, from: Data("""
        [{"id": "S1", "name": "Salt Roads Wiki", "wikiHost": "saltroads.fandom.com", "status": "ready",
          "pages": 1204, "chunks": 5000, "error": null, "licence": "CC BY-SA 3.0"},
         {"id": "S2", "name": "", "status": "importing", "pages": 0, "progress": {"stage": "parsing", "pages": 40}},
         {"id": "S3", "name": "Broken", "status": "error", "pages": 0, "error": "not a MediaWiki dump"}]
        """.utf8))
        XCTAssertEqual(sources.map(\.displayName), ["Salt Roads Wiki", "Importing wiki", "Broken"])
        XCTAssertEqual(sources[0].summary, "\(1204.formatted()) pages")
        XCTAssertEqual(sources[1].summary, "Importing — 40 pages so far")
        XCTAssertEqual(sources[2].summary, "not a MediaWiki dump")
    }

    func testCanonHitsAndArticlesDecode() throws {
        let hits = try JSONDecoder().decode([WritingCanonHit].self, from: Data("""
        [{"id": "S1:7", "sourceId": "S1", "sourceName": "Salt Roads Wiki", "title": "Mara Vell › Abilities",
          "snippet": "She can pick any lock…"}]
        """.utf8))
        XCTAssertEqual(hits.first?.id, "S1:7")
        let article = try JSONDecoder().decode(WritingCanonArticle.self, from: Data("""
        {"id": "S1:7", "title": "Mara Vell", "text": "She can pick any lock.",
         "attribution": "Source: saltroads.fandom.com (Fandom wiki), CC BY-SA 3.0."}
        """.utf8))
        XCTAssertTrue(article.attribution.contains("CC BY-SA"))
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

    func testChapterCardsSayWhatTheyDo() {
        let proposal = ChatMeta(#"""
        {"proposals": [{"id": "p", "kind": "writing_chapter_update", "status": "accepted",
                        "data": {"title": "The Storm"}}]}
        """#).proposals[0]
        XCTAssertEqual(proposal.headline, "Change this chapter?")
        XCTAssertEqual(proposal.acceptLabel, "Update Chapter")
        XCTAssertTrue(proposal.resolvedLabel.contains("The Storm"))
    }
}
