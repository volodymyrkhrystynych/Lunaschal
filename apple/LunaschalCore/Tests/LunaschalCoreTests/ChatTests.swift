import Foundation
import XCTest
@testable import LunaschalCore

final class ChatTests: XCTestCase {
    private func message(_ id: String, _ role: String, _ content: String = "x", metadata: String? = nil,
                         status: String? = nil, attachments: [ChatAttachment]? = nil) -> ChatMessage {
        ChatMessage(id: id, role: role, content: content, metadata: metadata, status: status,
                    attachments: attachments, createdAt: "2026-10-05T12:00:00+00:00")
    }

    private func clip(_ status: String?) -> ChatAttachment {
        try! JSONDecoder().decode(ChatAttachment.self, from: Data("""
        {"id": "A", "conversationId": "C", "messageId": "M", "mime": "audio/mp4", "kind": "audio",
         "description": null, "descriptionStatus": null, "descriptionError": null, "transcript": null,
         "transcriptStatus": \(status.map { "\"\($0)\"" } ?? "null"), "transcriptError": null, "position": 0,
         "createdAt": "2026-10-05T12:00:00+00:00", "url": "/api/chat/attachments/A/file", "latitude": null, "longitude": null}
        """.utf8))
    }

    // The cases from src/lib/chatSegments.test.ts: only what follows the last
    // "New chat" break is sent, and the break markers themselves never are.
    func testOnlyTheSegmentAfterTheLastBreakIsSent() {
        let messages = [message("1", "user"), message("2", "assistant"),
                        message("3", "system", "", metadata: #"{"break": true}"#),
                        message("4", "user"), message("5", "system", "briefing", metadata: #"{"briefing": true}"#)]
        XCTAssertEqual(ChatSegments.context(messages).map(\.id), ["4", "5"])
        XCTAssertTrue(ChatSegments.hasCurrentSegment(messages))
        XCTAssertFalse(ChatSegments.hasCurrentSegment(Array(messages.prefix(3))))
        XCTAssertEqual(ChatSegments.context([message("1", "user")]).map(\.id), ["1"])
        XCTAssertFalse(message("x", "system", metadata: "{not json").isBreak)

        let turns = ChatTurn.request(history: messages, adding: ChatTurn(
            id: "6", role: "user", content: "hi", metadata: nil, createdAt: "now", attachmentIds: ["P"]))
        XCTAssertEqual(turns.map(\.id), ["4", "5", "6"])
        let json = String(decoding: try! JSONEncoder().encode(turns.last!), as: UTF8.self)
        XCTAssertTrue(json.contains(#""metadata":null"#), json)
        XCTAssertTrue(json.contains(#""attachmentIds":["P"]"#), json)
    }

    // src/lib/chatPolling.test.ts: a running reply on the newest row, or a clip
    // anywhere still being transcribed.
    func testPollingWatchesTheNewestReplyAndEveryTranscript() {
        XCTAssertFalse(ChatSegments.shouldPoll([]))
        XCTAssertTrue(ChatSegments.shouldPoll([message("1", "user"), message("2", "assistant", status: "streaming")]))
        XCTAssertFalse(ChatSegments.shouldPoll([message("1", "assistant", status: "streaming"), message("2", "user")]))
        XCTAssertTrue(ChatSegments.shouldPoll([message("1", "user", "", attachments: [clip("running")]), message("2", "user")]))
        XCTAssertFalse(ChatSegments.shouldPoll([message("1", "user", "", attachments: [clip("done")])]))
    }

    func testTheServersConversationDecodes() throws {
        let json = """
        {"id": "C", "title": null, "dayKey": "2026-10-05", "mode": "chat", "createdAt": "x", "updatedAt": "x",
         "messages": [{"id": "M", "conversationId": "C", "role": "assistant", "content": "", "metadata": null,
                       "status": "streaming", "error": null, "rawContent": null, "createdAt": "2026-10-05T12:00:00+00:00",
                       "finishedAt": null, "attachments": []}]}
        """
        let conversation = try JSONDecoder().decode(ChatConversation?.self, from: Data(json.utf8))
        XCTAssertEqual(conversation?.messages.first?.status, "streaming")
        XCTAssertNil(try JSONDecoder().decode(ChatConversation?.self, from: Data("null".utf8)))
    }

    func testMetadataReadsLooselyAndBlankRepliesAreNamed() {
        let meta = ChatMeta(#"{"steps": [{"tool": "web_search", "arg": "rain", "ok": true, "count": 3}, {"tool": 4}],"#
                            + #" "sources": [{"url": "https://a"}, {"title": "no url"}], "thinking": "hm", "truncated": true,"#
                            + #" "proposals": [{"id": "p1", "kind": "calorie", "data": {"calories": 300}, "status": "pending"}, {"kind": "x"}]}"#)
        XCTAssertEqual(meta.steps.count, 2)
        XCTAssertEqual(meta.steps[0].label, #"Searched the web for "rain" — 3 results"#)
        XCTAssertEqual(meta.steps[1].label, "Thinking")
        XCTAssertEqual(meta.sources.map(\.url), ["https://a"])
        XCTAssertEqual(meta.thinking, "hm")
        XCTAssertTrue(meta.truncated)
        XCTAssertEqual(meta.proposals.map(\.id), ["p1"])
        XCTAssertEqual(meta.proposals[0].data["calories"]?.int, 300)
        XCTAssertEqual(ChatMeta("garbage"), ChatMeta(nil))

        XCTAssertTrue(message("1", "assistant", " ").isBlankReply)
        XCTAssertFalse(message("1", "assistant", " ", status: "streaming").isBlankReply)
        XCTAssertFalse(message("1", "user", "", attachments: [clip("running")]).isBlankReply)
    }

    // A selection of src/lib/agentSteps.test.ts's labels.
    func testStepLabelsMatchTheDesktop() {
        func label(_ json: String) -> String { AgentStep(JSONValue.parse(json)!).label }
        XCTAssertEqual(label(#"{"tool": "web_search", "ok": false, "error": "offline"}"#), "Web search unavailable: offline")
        XCTAssertEqual(label(#"{"tool": "local_knowledge_search", "ok": true, "queries": ["a", "b"], "count": 4}"#),
                       "Searched the offline library with 2 queries — 4 results")
        XCTAssertEqual(label(#"{"tool": "deep_research", "ok": true, "timedOut": true, "count": 1}"#),
                       "Deep research timed out — answered from 1 source so far")
        XCTAssertEqual(label(#"{"tool": "search_journal", "arg": "dentist", "ok": true, "count": 0}"#),
                       #"Searched the journal for "dentist" — nothing found"#)
        XCTAssertEqual(label(#"{"tool": "remember", "arg": "likes tea", "ok": true, "duplicate": true}"#), "Already remembered: likes tea")
        XCTAssertEqual(label(#"{"tool": "add_todos", "arg": "call mum", "ok": true}"#), "Added to today's to-dos: call mum")
        XCTAssertEqual(label(#"{"tool": "propose_calendar_event", "title": "Dentist", "ok": true}"#), "Staged a calendar event: Dentist")
        XCTAssertEqual(label(#"{"tool": "propose_flashcards", "ok": false, "error": "no topic"}"#), "Could not stage flashcards — no topic")
        XCTAssertEqual(label(#"{"tool": "ask_user"}"#), "Asked for clarification")
        XCTAssertEqual(label(#"{"tool": "mystery"}"#), "Ran mystery")
        XCTAssertTrue(AgentStep(JSONValue.parse(#"{"tool": "add_todos", "ok": true}"#)!).writesChatTodo)
        XCTAssertFalse(AgentStep(JSONValue.parse(#"{"tool": "add_todos", "ok": false}"#)!).writesChatTodo)
    }

    func testStreamFramesParse() {
        XCTAssertEqual(ChatStreamEvent.parse(line: #"data: {"messageId": "M1"}"#), [.messageID("M1")])
        XCTAssertEqual(ChatStreamEvent.parse(line: #"data: {"content": "Hi"}"#), [.content("Hi")])
        XCTAssertEqual(ChatStreamEvent.parse(line: #"data: {"thinking": "hmm"}"#), [.thinking("hmm")])
        guard case let .step(step)? = ChatStreamEvent.parse(line: #"data: {"tool": "read_day", "arg": "Monday", "ok": true}"#).first
        else { return XCTFail("no step") }
        XCTAssertEqual(step.label, "Looked up Monday")
        XCTAssertEqual(ChatStreamEvent.parse(line: #"data: {"done": true, "steps": [], "proposals": [{"kind": "flashcard_draft", "data": {"content": " useState "}}, {"kind": "calendar", "data": {}}]}"#),
                       [.done(flashcardDrafts: ["useState"])])
        XCTAssertEqual(ChatStreamEvent.parse(line: #"data: {"error": "paused", "inferencePaused": true}"#), [.error("paused")])
        XCTAssertEqual(ChatStreamEvent.parse(line: "data: [DONE]"), [.end])
        XCTAssertEqual(ChatStreamEvent.parse(line: ""), [])
        XCTAssertEqual(ChatStreamEvent.parse(line: "data: {broken"), [])
    }

    func testCardsSayWhatTheDesktopSays() {
        func card(_ json: String) -> ChatProposal { ChatMeta(#"{"proposals": [\#(json)]}"#).proposals[0] }
        XCTAssertEqual(card(#"{"id": "1", "kind": "calendar", "data": {}, "status": "pending"}"#).headline, "Save as calendar event?")
        XCTAssertEqual(card(#"{"id": "1", "kind": "food", "data": {}, "status": "pending"}"#).acceptLabel, "Log Meal")
        XCTAssertEqual(card(#"{"id": "1", "kind": "calendar", "reconstructionDay": "2026-10-04", "data": {}, "status": "pending"}"#).headline,
                       "Suggested event · 2026-10-04")
        XCTAssertEqual(card(#"{"id": "1", "kind": "food", "data": {}, "status": "accepted", "result": {"calorieLogId": "L"}}"#).resolvedLabel,
                       "Saved to your food log, with calories")
        XCTAssertEqual(card(#"{"id": "1", "kind": "flashcards", "data": {}, "status": "accepted", "result": {"count": 1}}"#).resolvedLabel,
                       "Queued 1 card for review in Learning")
        XCTAssertEqual(card(#"{"id": "1", "kind": "calorie", "data": {}, "status": "dismissed"}"#).resolvedLabel, "Dismissed")
    }

    func testEditedCardsGoOutWithWholeNumbers() throws {
        let data: [String: JSONValue] = ["calories": .number(600), "weight": .number(2.5), "title": .string("x"), "time": .null]
        let json = String(decoding: try JSONEncoder().encode(data), as: UTF8.self)
        XCTAssertTrue(json.contains(#""calories":600"#), json)
        XCTAssertTrue(json.contains(#""weight":2.5"#), json)
        XCTAssertTrue(json.contains(#""time":null"#), json)
        XCTAssertEqual(JSONValue.number(600).text, "600")
    }

    func testPhotoStatusAndTodoSummary() {
        func photo(_ status: String) -> ChatAttachment {
            try! JSONDecoder().decode(ChatAttachment.self, from: Data(#"{"id": "P", "conversationId": "C", "messageId": null, "mime": "image/jpeg", "kind": "image", "description": null, "descriptionStatus": "\#(status)", "descriptionError": null, "transcript": null, "transcriptStatus": null, "transcriptError": null, "position": 0, "createdAt": "x"}"#.utf8))
        }
        XCTAssertNil(ChatPhotoStatus.message([]))
        XCTAssertEqual(ChatPhotoStatus.message([photo("running"), photo("done")]), "Reading the photo…")
        XCTAssertEqual(ChatPhotoStatus.message([photo("running"), photo("running")]), "Reading 2 photos…")
        XCTAssertEqual(ChatPhotoStatus.message([photo("error")]), "One photo couldn't be read — it'll be attached, but not described.")
        XCTAssertNil(ChatPhotoStatus.message([photo("done")]))

        let todos = [ChatTodo(id: "1", title: "a", notes: nil, due: nil, priority: 3, done: false),
                     ChatTodo(id: "2", title: "b", notes: nil, due: nil, priority: 3, done: true)]
        XCTAssertEqual(ChatTodo.summary([]), "Today's to-dos")
        XCTAssertEqual(ChatTodo.summary(todos), "1 to-do today")
        XCTAssertEqual(ChatTodo.summary(todos.map { ChatTodo(id: $0.id, title: $0.title, notes: nil, due: nil, priority: 3, done: false) }),
                       "2 to-dos today")
    }

    func testDueDatesGoOutAtLocalNoon() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        let day = calendar.date(from: DateComponents(year: 2026, month: 10, day: 5, hour: 23))!
        XCTAssertEqual(TodoPromotion.dueSeconds(day, calendar: calendar),
                       Int(calendar.date(from: DateComponents(year: 2026, month: 10, day: 5, hour: 12))!.timeIntervalSince1970))
    }

    func testRepliesSplitIntoBlocks() {
        let text = """
        # Plan
        Some **bold** text
        continues here.

        - one
          more of one
        - [x] done
        2. second

        > quoted
        > again

        ```swift
        let x = 1

        print(x)
        ```
        ---
        | a | b |
        |---|:-:|
        | 1 | 2 |
        """
        XCTAssertEqual(MarkdownBlock.parse(text), [
            .heading(level: 1, text: "Plan"),
            .paragraph("Some **bold** text\ncontinues here."),
            .listItem(marker: "•", indent: 0, text: "one\nmore of one"),
            .listItem(marker: "☑", indent: 0, text: "done"),
            .listItem(marker: "2.", indent: 0, text: "second"),
            .quote("quoted\nagain"),
            .code(language: "swift", text: "let x = 1\n\nprint(x)"),
            .rule,
            .table([["a", "b"], ["1", "2"]]),
        ])
        XCTAssertEqual(MarkdownBlock.parse("#hashtag"), [.paragraph("#hashtag")])
        XCTAssertEqual(MarkdownBlock.parse("```\nunclosed"), [.code(language: nil, text: "unclosed")])
        XCTAssertEqual(MarkdownBlock.parse("| not | a table |"), [.paragraph("| not | a table |")])
    }
}

final class ChatRecordingTests: XCTestCase {
    private var root: URL!
    private var store: ChatRecordingStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try ChatRecordingStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func record(_ conversation: String?, at seconds: TimeInterval, bytes: Int = 10) throws -> ChatRecording {
        let item = try store.begin(conversationID: conversation, text: "  ", attachmentIDs: [],
                                   now: Date(timeIntervalSince1970: seconds))
        try Data(repeating: 1, count: bytes).write(to: store.audioURL(item))
        return try XCTUnwrap(try store.finish(item.id))
    }

    func testAnEmptyClipIsDroppedAndAnInterruptedOneIsKept() throws {
        let empty = try store.begin(conversationID: "C", text: nil, attachmentIDs: [])
        XCTAssertNil(try store.finish(empty.id))
        XCTAssertEqual(try store.list(), [])

        let cut = try store.begin(conversationID: "C", text: " what is this ", attachmentIDs: ["P"])
        XCTAssertEqual(cut.text, "what is this")
        try Data([1, 2, 3]).write(to: store.audioURL(cut))
        try store.recoverInterrupted()
        XCTAssertEqual(try store.list().map(\.state), [.pending])
        XCTAssertNil(try record("C", at: 5).text)
    }

    @MainActor
    func testClipsGoOutInOrderIntoTodaysConversation() async throws {
        let first = try record(nil, at: 100)
        let refused = try record("YESTERDAY", at: 200)
        let third = try record(nil, at: 300)
        let server = FakeChatServer(refuse: [refused.id: 400])
        try await ChatRecordingSync(store: store).run(using: server)
        XCTAssertEqual(server.sent.map(\.0), [first.id, refused.id, third.id])
        // Asked once, and a clip that knew its conversation kept it.
        XCTAssertEqual(server.asked, 1)
        XCTAssertEqual(server.sent.map(\.1), ["TODAY", "YESTERDAY", "TODAY"])
        XCTAssertEqual(try store.list().map(\.id), [refused.id])
        XCTAssertEqual(try store.list().first?.state, .failed)
    }

    @MainActor
    func testAnUnreachableServerKeepsEveryClip() async throws {
        let first = try record("C", at: 100)
        _ = try record("C", at: 200)
        do {
            try await ChatRecordingSync(store: store).run(using: FakeChatServer(refuse: [first.id: 503]))
            XCTFail("expected the 503 to stop the pass")
        } catch {}
        XCTAssertEqual(try store.list().map(\.state), [.pending, .pending])
    }

    func testTheReplyMustNameThisClip() throws {
        let item = try record("C", at: 1)
        let mine = Data(#"{"id": "\#(item.messageID)", "attachment": {"id": "\#(item.id)"}}"#.utf8)
        XCTAssertNoThrow(try JournalAPI.validateChatRecordingAcknowledgement(mine, for: item))
        let other = Data(#"{"id": "\#(item.messageID)", "attachment": {"id": "X"}}"#.utf8)
        XCTAssertThrowsError(try JournalAPI.validateChatRecordingAcknowledgement(other, for: item))
    }

    func testPathIDsArePlain() {
        XCTAssertNoThrow(try JournalAPI.pathID("01JABC-def_9"))
        for bad in ["", "../x", "a/b", "a?b", String(repeating: "a", count: 65)] {
            XCTAssertThrowsError(try JournalAPI.pathID(bad), bad)
        }
    }
}

private final class FakeChatServer: ChatRecordingTransport {
    var sent: [(String, String)] = []
    var asked = 0
    let refuse: [String: Int]
    init(refuse: [String: Int] = [:]) { self.refuse = refuse }
    func chatConversationID() async throws -> String { asked += 1; return "TODAY" }
    func sendChatRecording(_ item: ChatRecording, conversationID: String, audioURL: URL) async throws {
        sent.append((item.id, conversationID))
        if let status = refuse[item.id] { throw HTTPFailure(status: status) }
    }
}
