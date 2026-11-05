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
}
