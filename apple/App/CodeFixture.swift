import Foundation
import LunaschalCore

/// Demo data for the Code screens, since UI tests run with no server:
/// launched with `-codeFixture`, the Code page shows a task with a plan to
/// approve, one with a question, a finished one with a diff and a pushed one.
/// Actions change the canned tasks in memory; nothing is fetched or saved.
struct CodeFixture {
    static let argument = "-codeFixture"

    static func ifAsked() -> CodeFixture? {
        #if DEBUG
        ProcessInfo.processInfo.arguments.contains(argument) ? CodeFixture() : nil
        #else
        nil
        #endif
    }

    static let planned = "01K9CODEFIXTURETASK0000001"
    static let asking = "01K9CODEFIXTURETASK0000002"
    static let finished = "01K9CODEFIXTURETASK0000003"
    static let pushed = "01K9CODEFIXTURETASK0000004"

    let repos = [CodeRepo(id: "01K9CODEFIXTUREREPO0000001", slug: "lunaschal", name: "Lunaschal",
                          cloneState: "ready", isDefault: 1)]

    var tasks: [CodeTask] = [
        task(planned, "Parse \"1h30m\" durations", status: "awaiting_plan", phase: "plan",
             plan: """
             ## Plan
             1. Add `parse_duration` to `backend/util/time.py`.
             2. Cover it in `backend/tests/test_time.py`, including the empty string.
             3. Run `pytest backend/tests/test_time.py`.
             """,
             pending: approval("plan", [:])),
        task(asking, "Rename the settings toggle", status: "needs_input", phase: "build",
             summary: "Renamed the web label.\nQUESTION: Should the iOS label change too?",
             diff: [CodeFileStat(path: "src/components/Settings.tsx", added: 1, removed: 1)],
             head: "3333333",
             pending: approval("question", ["question": .string("Should the iOS label change too?")])),
        task(finished, "Fix the off-by-one in the streak counter", status: "done", phase: "build",
             plan: "1. Use day_key_for in streak().",
             summary: "streak() now keys days with day_key_for; added a regression test.",
             diff: [CodeFileStat(path: "backend/lifestyle/activity.py", added: 2, removed: 1),
                    CodeFileStat(path: "backend/tests/test_lifestyle_streak.py", added: 3, removed: 0)],
             head: "5555555", testExit: 0, testOutput: "1803 passed in 412.11s"),
        task(pushed, "Document the backup states", status: "pushed", phase: "build",
             head: "7777777", testExit: 0, prURL: "https://github.com/example/demo-app/pull/12"),
    ]

    let patch = """
    diff --git a/backend/lifestyle/activity.py b/backend/lifestyle/activity.py
    index 1111111..2222222 100644
    --- a/backend/lifestyle/activity.py
    +++ b/backend/lifestyle/activity.py
    @@ -40,4 +40,5 @@ def streak(days):
         ordered = sorted(days)
    -    today = time.strftime('%Y-%m-%d')
    +    # Days roll over at 4am, so "today" is the 4am day.
    +    today = day_key_for(time.time())
         return count_back(ordered, today)
    diff --git a/backend/tests/test_lifestyle_streak.py b/backend/tests/test_lifestyle_streak.py
    new file mode 100644
    --- /dev/null
    +++ b/backend/tests/test_lifestyle_streak.py
    @@ -0,0 +1,3 @@
    +def test_today_counts_once_after_midnight():
    +    days = ['2026-11-01', '2026-11-02']
    +    assert streak(days) == 2

    """

    func events(for id: String) -> [CodeTaskEvent] {
        let prompt = tasks.first { $0.id == id }?.prompt ?? ""
        return [
            event(1, "user", ["text": .string(prompt)]),
            event(2, "tool_call", ["title": .string("rg \"def streak\" backend"), "kind": .string("search")]),
            event(3, "proxy", ["action": .string("call"), "waited": .number(4.2), "ran": .number(38.0)]),
            event(4, "proxy", ["action": .string("promoted"), "reason": .string("1 call recovered")]),
            event(5, "message", ["text": .string(tasks.first { $0.id == id }?.plan ?? "Done.")]),
        ]
    }

    // MARK: Building

    private static func approval(_ kind: String, _ request: [String: JSONValue]) -> CodeApproval {
        CodeApproval(id: "01K9CODEFIXTUREAPPROVAL001", kind: kind, request: request, status: "pending")
    }

    private static func task(_ id: String, _ title: String, status: String, phase: String,
                             plan: String? = nil, summary: String? = nil, diff: [CodeFileStat]? = nil,
                             head: String? = nil, testExit: Int? = nil, testOutput: String? = nil,
                             prURL: String? = nil, pending: CodeApproval? = nil) -> CodeTask {
        CodeTask(id: id, repoId: "01K9CODEFIXTUREREPO0000001", title: title, prompt: title, harness: "pi",
                 status: status, phase: phase, plan: plan, summary: summary, diffStat: diff, headSha: head,
                 testExit: testExit, testOutput: testOutput, prUrl: prURL, error: nil,
                 createdAt: "2026-11-02T10:00:00+00:00", updatedAt: "2026-11-02T10:30:00+00:00",
                 pending: pending, running: false)
    }

    private func event(_ seq: Int, _ kind: String, _ payload: [String: JSONValue]) -> CodeTaskEvent {
        CodeTaskEvent(seq: seq, kind: kind, payload: payload, createdAt: "2026-11-02T10:00:00+00:00")
    }

    /// The canned task after an action, the way the server would answer.
    mutating func apply(_ action: String, to id: String, text: String = "") -> CodeTask? {
        guard let index = tasks.firstIndex(where: { $0.id == id }) else { return nil }
        let t = tasks[index]
        let status: String
        var phase = t.phase
        switch action {
        case "approve": status = "queued"; phase = "build"
        case "revise", "message", "continue": status = "queued"
        case "cancel": status = "cancelled"
        case "discard": status = "discarded"
        case "push": status = "pushed"
        default: return t
        }
        let updated = CodeTask(id: t.id, repoId: t.repoId, title: t.title, prompt: t.prompt, harness: t.harness,
                               status: status, phase: phase, plan: t.plan, summary: t.summary, diffStat: t.diffStat,
                               headSha: t.headSha, testExit: t.testExit, testOutput: t.testOutput,
                               prUrl: action == "push" ? "https://github.com/example/demo-app/pull/13" : t.prUrl,
                               error: nil, createdAt: t.createdAt, updatedAt: t.updatedAt, pending: nil, running: false)
        tasks[index] = updated
        return updated
    }

    mutating func add(prompt: String) -> CodeTask {
        let t = Self.task("01K9CODEFIXTURETASK00000\(String(format: "%02d", tasks.count + 1))", prompt,
                          status: "queued", phase: "plan")
        tasks.insert(t, at: 0)
        return t
    }
}
