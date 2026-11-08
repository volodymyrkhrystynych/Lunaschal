import Foundation
import XCTest
@testable import LunaschalCore

final class CodeAgentTests: XCTestCase {
    // Shaped exactly as backend/routes/code_agent.py returns it.
    private let detailJSON = """
    {"task": {"id": "01JB0000000000000000000000", "repoId": "R", "title": "Fix the streak",
              "prompt": "Fix the off-by-one", "harness": "pi", "status": "done", "phase": "build",
              "sessionId": "s", "pendingInput": null, "baseBranch": "main", "baseSha": "aaa",
              "headSha": "bbb", "plan": "1. fix", "summary": "Fixed.",
              "diffStat": [{"path": "a.py", "added": 3, "removed": 1}, {"path": "img.png", "added": null, "removed": null}],
              "testExit": 0, "testOutput": "12 passed", "prUrl": null, "error": null,
              "createdAt": "2026-11-02T10:00:00+00:00", "startedAt": "2026-11-02T10:00:00+00:00",
              "finishedAt": null, "updatedAt": "2026-11-02T10:30:00+00:00",
              "pending": null, "running": false},
     "events": [
       {"seq": 1, "kind": "user", "payload": {"text": "Fix the off-by-one"}, "createdAt": "x"},
       {"seq": 2, "kind": "proxy", "payload": {"action": "call", "waited": 4.6, "ran": 38.2, "cancels": 0,
                                               "usage": {"prompt_tokens": 100}}, "createdAt": "x"},
       {"seq": 3, "kind": "proxy", "payload": {"action": "promoted", "reason": "r", "tools": ["read"]}, "createdAt": "x"},
       {"seq": 4, "kind": "permission", "payload": {"title": "Edit a.py", "kind": "edit", "decision": "refused"}, "createdAt": "x"},
       {"seq": 5, "kind": "test", "payload": {"exit": 0, "tail": "ok"}, "createdAt": "x"},
       {"seq": 6, "kind": "status", "payload": {"status": "done"}, "createdAt": "x"},
       {"seq": 7, "kind": "status", "payload": {"note": "the plan step changed files; they were reverted"}, "createdAt": "x"}
     ]}
    """

    func testTheServersTaskDecodes() throws {
        let detail = try JSONDecoder().decode(CodeTaskDetail.self, from: Data(detailJSON.utf8))
        let task = detail.task
        XCTAssertEqual(task.status, "done")
        XCTAssertEqual(task.statusLabel, "Ready to review")
        XCTAssertTrue(task.canPush)
        XCTAssertTrue(task.canMessage)
        XCTAssertFalse(task.canCancel)
        XCTAssertEqual(task.testsPassed, true)
        XCTAssertEqual(task.linesAdded, 3)
        XCTAssertEqual(task.linesRemoved, 1)
        XCTAssertEqual(detail.events.map(\.seq), [1, 2, 3, 4, 5, 6, 7])
    }

    func testEventLabels() throws {
        let events = try JSONDecoder().decode(CodeTaskDetail.self, from: Data(detailJSON.utf8)).events
        XCTAssertEqual(events.map(\.label), [
            "Fix the off-by-one",
            "Model: 38s (waited 5s for the GPU)",
            "Rescued a tool call from its thinking",
            "Refused: Edit a.py",
            "Tests passed",
            "Status: Ready to review",
            "the plan step changed files; they were reverted",
        ])
        XCTAssertEqual(events.map(\.isDetail), [false, true, true, true, false, false, false])
    }

    func testModelCallLabelsShowTheCache() {
        XCTAssertEqual(event(1, "proxy", ["action": .string("call"), "ran": .number(4.1),
                                          "prefilled": .number(1200), "cached": .number(148000)]).label,
                       "Model: 4s · 1.2k new, 148k cached")
        XCTAssertEqual(event(2, "proxy", ["action": .string("call"), "ran": .number(3),
                                          "prefilled": .number(812), "cached": .number(0)]).label,
                       "Model: 3s · 812 new")
    }

    func testLookupLabelsMatchTheWeb() {
        func look(_ p: [String: JSONValue]) -> String {
            event(1, "proxy", ["action": .string("lookup")].merging(p) { $1 }).label
        }
        XCTAssertEqual(look(["tool": .string("library_search"), "arg": .string("flask streaming"),
                             "ok": .bool(true), "count": .number(5)]),
                       "Library: 5 results for \u{2018}flask streaming\u{2019}")
        XCTAssertEqual(look(["tool": .string("library_read"), "arg": .string("A/Streaming"), "ok": .bool(true)]),
                       "Read from the library: A/Streaming")
        XCTAssertEqual(look(["tool": .string("web_research"), "arg": .string("q"), "ok": .bool(false),
                             "blocked": .bool(true)]),
                       "Web research refused: search the library first")
        XCTAssertEqual(look(["tool": .string("web_research"), "arg": .string("q"), "ok": .bool(true),
                             "count": .number(1), "seconds": .number(41.6)]),
                       "Web research: 1 source for \u{2018}q\u{2019} in 42s")
        XCTAssertEqual(look(["tool": .string("repo_wiki_search"), "arg": .string("proxy"), "ok": .bool(true),
                             "count": .number(2)]),
                       "Wiki: 2 notes for \u{2018}proxy\u{2019}")
        XCTAssertEqual(look(["tool": .string("code_map"), "arg": .string("slot"), "ok": .bool(true)]),
                       "Code map: \u{2018}slot\u{2019}")
    }

    func testPendingQuestionAndStateRules() throws {
        let json = """
        {"id": "T", "repoId": "R", "title": "t", "prompt": "p", "harness": "omp", "status": "needs_input",
         "phase": "build", "plan": null, "summary": null, "diffStat": null, "headSha": null, "testExit": null,
         "testOutput": null, "prUrl": null, "error": null, "createdAt": null, "updatedAt": null, "running": false,
         "pending": {"id": "A", "taskId": "T", "kind": "question", "request": {"question": "Which file?"},
                     "status": "pending", "response": null, "createdAt": "x", "answeredAt": null}}
        """
        let task = try JSONDecoder().decode(CodeTask.self, from: Data(json.utf8))
        XCTAssertEqual(task.pending?.question, "Which file?")
        XCTAssertFalse(task.canPush, "nothing was imported yet")
        XCTAssertTrue(task.canDiscard)
        XCTAssertFalse(task.isActive)
    }

    func testPollingIsFastOnlyWhileTheAgentWorks() throws {
        func task(_ status: String) throws -> CodeTask {
            try JSONDecoder().decode(CodeTask.self, from: Data("""
            {"id": "T", "repoId": "R", "title": "t", "prompt": "p", "harness": "pi", "status": "\(status)", "phase": "plan"}
            """.utf8))
        }
        XCTAssertEqual(CodeTaskStatus.pollInterval([try task("awaiting_plan"), try task("done")]), 20)
        XCTAssertEqual(CodeTaskStatus.pollInterval([try task("done"), try task("running")]), 3)
        XCTAssertEqual(CodeTaskStatus.pollInterval([]), 20)
    }

    // src/lib/codeTasks.test.ts pins the same words for the web.
    func testStatusLabelsMatchTheWeb() {
        let expected = ["queued": "Queued", "planning": "Planning", "awaiting_plan": "Plan ready",
                        "running": "Working", "needs_input": "Has a question", "testing": "Running tests",
                        "done": "Ready to review", "interrupted": "Interrupted", "error": "Failed",
                        "cancelled": "Cancelled", "pushed": "Pushed", "discarded": "Discarded"]
        for (status, label) in expected { XCTAssertEqual(CodeTaskStatus.label(status), label, status) }
    }

    func testUnifiedDiffParsesFilesHunksAndLineNumbers() {
        let patch = """
        diff --git a/backend/a.py b/backend/a.py
        index 1111111..2222222 100644
        --- a/backend/a.py
        +++ b/backend/a.py
        @@ -10,4 +10,5 @@ def streak():
             days = load()
        -    return len(days)
        +    today = day_key_for(now())
        +    return len([d for d in days if d != today]) + 1
             # end
        \\ No newline at end of file
        diff --git a/backend/tests/test_new.py b/backend/tests/test_new.py
        new file mode 100644
        index 0000000..3333333
        --- /dev/null
        +++ b/backend/tests/test_new.py
        @@ -0,0 +1,2 @@
        +def test_streak():
        +    assert True
        diff --git a/old name.txt b/new name.txt
        similarity index 100%
        rename from old name.txt
        rename to new name.txt
        diff --git a/logo.png b/logo.png
        Binary files a/logo.png and b/logo.png differ

        """
        let files = UnifiedDiff.parse(patch)
        XCTAssertEqual(files.map(\.path), ["backend/a.py", "backend/tests/test_new.py", "new name.txt", "logo.png"])

        let first = files[0]
        XCTAssertEqual(first.hunks.count, 1)
        XCTAssertEqual(first.added, 2)
        XCTAssertEqual(first.removed, 1)
        let lines = first.hunks[0].lines
        XCTAssertEqual(lines[0], DiffLine(kind: .context, text: "    days = load()", oldNumber: 10, newNumber: 10))
        XCTAssertEqual(lines[1], DiffLine(kind: .removed, text: "    return len(days)", oldNumber: 11, newNumber: nil))
        XCTAssertEqual(lines[2].newNumber, 11)
        XCTAssertEqual(lines[3].newNumber, 12)
        XCTAssertEqual(lines[4], DiffLine(kind: .context, text: "    # end", oldNumber: 12, newNumber: 13))
        XCTAssertEqual(lines[5].kind, .note)

        XCTAssertTrue(files[1].isNew)
        XCTAssertEqual(files[1].hunks[0].lines.map(\.newNumber), [1, 2])
        XCTAssertEqual(files[2].oldPath, "old name.txt")
        XCTAssertTrue(files[2].hunks.isEmpty)
        XCTAssertTrue(files[3].isBinary)
        XCTAssertNil(files[3].oldPath)
    }

    func testEmptyPatch() {
        XCTAssertEqual(UnifiedDiff.parse(""), [])
    }

    func testPushPayload() {
        let id = "01JB0000000000000000000000"
        XCTAssertEqual(CodePushPayload.taskID(from: ["kind": "code_task", "taskId": id, "aps": [:]]), id)
        XCTAssertNil(CodePushPayload.taskID(from: ["kind": "other", "taskId": id]))
        XCTAssertNil(CodePushPayload.taskID(from: ["kind": "code_task", "taskId": "../etc"]))
        XCTAssertEqual(CodePushPayload.hexToken(Data([0x00, 0xab, 0xff])), "00abff")
    }

    func testRepoDecodesTheRegistryRow() throws {
        let repos = try JSONDecoder().decode([CodeRepo].self, from: Data("""
        [{"id": "R", "slug": "lunaschal", "name": "Lunaschal", "remoteUrl": "https://github.com/x/y",
          "cloneState": "ready", "isDefault": 1, "hasCheckout": true, "hasGraph": false}]
        """.utf8))
        XCTAssertTrue(repos[0].isDefaultRepo)
    }

    // MARK: Terminal

    private func event(_ seq: Int, _ kind: String, _ payload: [String: JSONValue]) -> CodeTaskEvent {
        CodeTaskEvent(seq: seq, kind: kind, payload: payload, createdAt: nil)
    }

    func testTheTerminalReadsLikeASession() {
        let lines = CodeTerminal.lines([
            event(1, "user", ["text": .string("Fix the streak")]),
            event(2, "status", ["status": .string("planning")]),
            event(3, "thought", ["text": .string("  Look at habits.py first  ")]),
            event(4, "tool_call", ["title": .string("Read backend/habits.py"), "status": .string("pending")]),
            event(5, "tool_result", ["status": .string("in_progress")]),
            event(6, "tool_result", ["status": .string("completed")]),
            event(7, "proxy", ["action": .string("call"), "ran": .number(38.2), "waited": .number(0)]),
            event(8, "message", ["text": .string("1. Change the loop\n2. Add a test")]),
        ])
        XCTAssertEqual(lines.map(\.style), [.prompt, .dim, .thought, .tool, .dim, .dim, .text])
        XCTAssertEqual(lines.map(\.text), [
            "❯ Fix the streak", "── planning ──", "Look at habits.py first", "● Read backend/habits.py",
            "  ⎿ done", "  · model: 38s", "1. Change the loop\n2. Add a test",
        ])
        XCTAssertEqual(Set(lines.map(\.id)).count, lines.count)
    }

    func testTerminalFailuresAndTestOutput() {
        let lines = CodeTerminal.lines([
            event(1, "permission", ["title": .string("Edit a.py"), "decision": .string("allowed")]),
            event(2, "permission", ["title": .string("Edit a.py"), "decision": .string("refused")]),
            event(3, "tool_result", ["status": .string("failed")]),
            event(4, "proxy", ["action": .string("stuck")]),
            event(5, "test", ["exit": .number(1), "tail": .string("1 failed\n")]),
            event(6, "status", ["status": .string("error"), "error": .string("session/new timed out after 120s")]),
            event(7, "status", ["note": .string("the plan step changed files; they were reverted")]),
            event(8, "message", ["text": .string("   ")]),
        ])
        XCTAssertEqual(lines.map(\.text), [
            "  ⎿ refused: Edit a.py (the plan step is read-only)",
            "  ⎿ failed",
            "  · stuck: tool calls keep landing in its thinking",
            "✗ tests failed (exit 1)", "1 failed",
            "✗ session/new timed out after 120s",
            "  ! the plan step changed files; they were reverted",
        ])
        XCTAssertEqual(lines.map(\.style), [.dim, .failure, .failure, .failure, .output, .failure, .dim])
        XCTAssertEqual(lines[4].id, "e5-tail")
    }

    func testTerminalChecklist() {
        let lines = CodeTerminal.lines([event(1, "plan", ["entries": .array([
            .object(["content": .string("Read"), "status": .string("completed")]),
            .object(["content": .string("Edit"), "status": .string("in_progress")]),
            .object(["content": .string("Test"), "status": .string("pending")]),
        ])])])
        XCTAssertEqual(lines.map(\.text), ["  ☒ Read\n  ◐ Edit\n  ☐ Test"])
    }

    func testDictationAppendsToTheDraft() {
        XCTAssertEqual(CodeDraft.appending(" fix it ", to: ""), "fix it")
        XCTAssertEqual(CodeDraft.appending("and test it", to: "fix it"), "fix it and test it")
        XCTAssertEqual(CodeDraft.appending("and test it", to: "fix it\n"), "fix it\nand test it")
        XCTAssertEqual(CodeDraft.appending("  ", to: "fix it"), "fix it")
    }

    func testTranscriptIsTheTrimmedText() throws {
        XCTAssertEqual(try JournalAPI.transcript(Data(#"{"text": " add a test ", "language": "en"}"#.utf8)), "add a test")
        XCTAssertThrowsError(try JournalAPI.transcript(Data(#"{"error": "x"}"#.utf8)))
    }
}
