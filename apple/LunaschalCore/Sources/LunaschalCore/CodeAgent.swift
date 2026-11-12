import Foundation

// MARK: The Code tab (the server's /api/code routes)
//
// A coding task is handed to a local agent running in a sealed container on
// the server; the phone approves its plan, answers its questions, reviews the
// diff and the test run, and pushes. Everything here is plain data and rules,
// so it is tested on Linux; App/CodeViews.swift draws it.

public struct CodeFileStat: Codable, Equatable, Sendable {
    public let path: String
    public let added: Int?
    public let removed: Int?

    public init(path: String, added: Int?, removed: Int?) {
        self.path = path; self.added = added; self.removed = removed
    }
}

public struct CodeApproval: Codable, Equatable, Sendable {
    public let id: String
    public let kind: String            // "plan" | "question"
    public let request: [String: JSONValue]
    public let status: String

    public init(id: String, kind: String, request: [String: JSONValue], status: String) {
        self.id = id; self.kind = kind; self.request = request; self.status = status
    }

    public var question: String? {
        if case let .string(q)? = request["question"], !q.isEmpty { return q }
        return nil
    }
}

public struct CodeTask: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let repoId: String
    public let title: String
    public let prompt: String
    public let harness: String
    public let status: String
    public let phase: String
    public let plan: String?
    public let summary: String?
    public let diffStat: [CodeFileStat]?
    public let headSha: String?
    public let testExit: Int?
    public let testOutput: String?
    public let prUrl: String?
    public let error: String?
    public let createdAt: String?
    public let updatedAt: String?
    public let pending: CodeApproval?
    public let running: Bool?

    public init(id: String, repoId: String, title: String, prompt: String, harness: String, status: String,
                phase: String, plan: String? = nil, summary: String? = nil, diffStat: [CodeFileStat]? = nil,
                headSha: String? = nil, testExit: Int? = nil, testOutput: String? = nil, prUrl: String? = nil,
                error: String? = nil, createdAt: String? = nil, updatedAt: String? = nil,
                pending: CodeApproval? = nil, running: Bool? = nil) {
        self.id = id; self.repoId = repoId; self.title = title; self.prompt = prompt; self.harness = harness
        self.status = status; self.phase = phase; self.plan = plan; self.summary = summary
        self.diffStat = diffStat; self.headSha = headSha; self.testExit = testExit; self.testOutput = testOutput
        self.prUrl = prUrl; self.error = error; self.createdAt = createdAt; self.updatedAt = updatedAt
        self.pending = pending; self.running = running
    }

    /// The agent (or the queue) is working; nothing for the user to do yet.
    public var isActive: Bool { CodeTaskStatus.active.contains(status) }
    /// A question about the code rather than a change to it.
    public var isQuestion: Bool { phase == "ask" }
    /// An answered question can become a change, planned in the same session.
    public var canPlanFromAnswer: Bool { status == "answered" }
    public var isClosed: Bool { CodeTaskStatus.closed.contains(status) }
    public var awaitsPlanApproval: Bool { status == "awaiting_plan" }
    public var canMessage: Bool {
        ["answered", "needs_input", "done", "error", "interrupted", "awaiting_plan"].contains(status)
    }
    public var canContinue: Bool { status == "interrupted" || status == "error" }
    public var canPush: Bool { (status == "done" || status == "needs_input") && headSha != nil }
    public var canCancel: Bool { isActive }
    public var canDiscard: Bool { !isActive && !isClosed }
    public var testsPassed: Bool? { testExit.map { $0 == 0 } }
    /// The verdict worth showing: none while the agent is at work, because the
    /// stored one describes code it is in the middle of changing. Same rule as
    /// src/lib/codeTasks.ts's `testVerdict`.
    public var testVerdict: Bool? { isActive ? nil : testsPassed }
    public var statusLabel: String { CodeTaskStatus.label(status) }

    public var linesAdded: Int { (diffStat ?? []).reduce(0) { $0 + ($1.added ?? 0) } }
    public var linesRemoved: Int { (diffStat ?? []).reduce(0) { $0 + ($1.removed ?? 0) } }
}

public enum CodeTaskStatus {
    public static let active: Set<String> = ["queued", "answering", "planning", "running", "testing"]
    /// States that wait on the user: the "Needs you" section.
    public static let needsYou: Set<String> = ["answered", "awaiting_plan", "needs_input", "done", "error",
                                               "interrupted"]
    public static let closed: Set<String> = ["cancelled", "pushed", "discarded"]

    /// The same words as src/lib/codeTasks.ts — CodeAgentTests pins them.
    public static func label(_ status: String) -> String {
        switch status {
        case "queued": return "Queued"
        case "answering": return "Looking into it"
        case "answered": return "Answered"
        case "planning": return "Planning"
        case "awaiting_plan": return "Plan ready"
        case "running": return "Working"
        case "needs_input": return "Has a question"
        case "testing": return "Running tests"
        case "done": return "Ready to review"
        case "interrupted": return "Interrupted"
        case "error": return "Failed"
        case "cancelled": return "Cancelled"
        case "pushed": return "Pushed"
        case "discarded": return "Discarded"
        default: return status.capitalized
        }
    }

    /// Fast while the agent works — a phase is minutes, so 3 s is plenty — and
    /// slow otherwise, when only a person can move the task on.
    public static func pollInterval(_ tasks: [CodeTask]) -> TimeInterval {
        tasks.contains(where: \.isActive) ? 3 : 20
    }
}

/// What a running task is doing right now, for the banner that says the agent
/// is at work. Same wording as `activity` in src/lib/codeTasks.ts.
public struct CodeActivity: Equatable, Sendable {
    public let headline: String
    /// When the current status began, if the event log says.
    public let since: Date?
    /// The latest thing it did in this status, if anything.
    public let doing: String?

    public init(headline: String, since: Date?, doing: String?) {
        self.headline = headline; self.since = since; self.doing = doing
    }

    public static func of(_ task: CodeTask, events: [CodeTaskEvent]) -> CodeActivity? {
        guard task.isActive else { return nil }
        let start = events.lastIndex { $0.kind == "status" && $0.payload["status"] == .string(task.status) }
        var doing: String?
        if task.status != "testing" {
            let after = events[(start.map { $0 + 1 } ?? 0)...]
            for event in after.reversed() {
                if let step = step(event) { doing = step; break }
            }
        }
        return CodeActivity(headline: headline(task.status),
                            since: start.flatMap { JournalTimestamp.parse(events[$0].createdAt) },
                            doing: doing)
    }

    static func headline(_ status: String) -> String {
        switch status {
        case "queued": return "Queued — starts when the agent is free"
        case "answering": return "The agent is looking into it"
        case "planning": return "The agent is planning"
        case "running": return "The agent is working"
        case "testing": return "Running the test suite"
        default: return CodeTaskStatus.label(status)
        }
    }

    private static func step(_ event: CodeTaskEvent) -> String? {
        switch event.kind {
        case "tool_call":
            var title = "a tool"
            if case let .string(t)? = event.payload["title"] { title = t }
            let line = (title.split(separator: "\n", omittingEmptySubsequences: false).first.map(String.init) ?? "")
                .trimmingCharacters(in: .whitespaces)
            return "Running: " + clip(line, 90)
        case "thought": return "Thinking it over"
        case "message": return "Writing its reply"
        case "proxy": return clip(event.label, 90)
        default: return nil
        }
    }

    /// Cut at `n` Unicode scalars — what `Array.from` counts in codeTasks.ts.
    static func clip(_ s: String, _ n: Int) -> String {
        let scalars = s.unicodeScalars
        guard scalars.count > n else { return s }
        var out = String.UnicodeScalarView()
        out.append(contentsOf: scalars.prefix(n - 1))
        return String(out) + "…"
    }

    /// 45s, 3m 05s, 1h 02m.
    public static func elapsed(_ seconds: TimeInterval) -> String {
        let s = max(0, Int(seconds.rounded(.down)))
        if s < 60 { return "\(s)s" }
        let m = s / 60
        if m < 60 { return "\(m)m " + String(format: "%02d", s % 60) + "s" }
        return "\(m / 60)h " + String(format: "%02d", m % 60) + "m"
    }
}

/// What a new task is: a question to answer, or a change to plan and build.
public enum CodeTaskMode: String, CaseIterable, Sendable {
    case ask, plan

    public var title: String { self == .ask ? "Ask" : "Change" }
    public var placeholder: String {
        self == .ask ? "Ask a question about the code…" : "Describe a change for the agent…"
    }
    public var startLabel: String { self == .ask ? "Ask" : "Start planning" }
    public var explanation: String {
        self == .ask ? "The agent reads the code and answers. It changes nothing, and you can turn the answer into a plan."
                     : "The agent explores the code and writes a plan first. Nothing changes until you approve it."
    }
}

public struct CodeTaskEvent: Codable, Equatable, Identifiable, Sendable {
    public let seq: Int
    public let kind: String
    public let payload: [String: JSONValue]
    public let createdAt: String?

    public init(seq: Int, kind: String, payload: [String: JSONValue], createdAt: String?) {
        self.seq = seq; self.kind = kind; self.payload = payload; self.createdAt = createdAt
    }

    public var id: Int { seq }

    private func string(_ key: String) -> String? {
        if case let .string(s)? = payload[key] { return s }
        return nil
    }

    private func number(_ key: String) -> Double? {
        if case let .number(n)? = payload[key] { return n }
        return nil
    }

    /// Long text the timeline shows in full (messages, the user's words).
    public var text: String? { string("text") }

    /// One line for the timeline. Same wording as src/lib/codeTasks.ts.
    public var label: String {
        switch kind {
        case "user": return string("text") ?? "You"
        case "message": return string("text") ?? ""
        case "thought": return "Thought it over"
        case "tool_call": return string("title") ?? "Used a tool"
        case "tool_result":
            let status = string("status") ?? "done"
            return "\(string("title") ?? "Tool") — \(status)"
        case "plan": return "Updated its checklist"
        case "permission":
            return "\(string("decision") == "allowed" ? "Allowed" : "Refused"): \(string("title") ?? string("kind") ?? "a tool")"
        case "test":
            let exit = Int(number("exit") ?? -1)
            return exit == 0 ? "Tests passed" : "Tests failed (exit \(exit))"
        case "status":
            if let note = string("note") { return note }
            return "Status: \(CodeTaskStatus.label(string("status") ?? ""))"
        case "proxy":
            switch string("action") {
            case "call":
                let ran = Int((number("ran") ?? 0).rounded())
                let waited = number("waited") ?? 0
                var label = "Model: \(ran)s"
                // What llama-server prefilled versus reused: a long call with a
                // big "new" number is a lost prompt cache, not a slow model.
                if let prefilled = number("prefilled") {
                    let cached = number("cached") ?? 0
                    label += " · \(Self.tokens(prefilled)) new"
                    if cached > 0 { label += ", \(Self.tokens(cached)) cached" }
                }
                return waited >= 1 ? label + " (waited \(Int(waited.rounded()))s for the GPU)" : label
            case "lookup": return lookupLabel
            case "preempted": return "Paused for a chat message"
            case "paused": return "Waiting: GPU inference is paused"
            case "paused_out": return "Gave up waiting for the GPU"
            case "promoted": return "Rescued a tool call from its thinking"
            case "poked": return "Nudged it to make a tool call properly"
            case "stuck": return "Stuck: tool calls keep landing in its thinking"
            default: return "Model"
            }
        default: return kind
        }
    }

    /// A library, wiki, code-map or web lookup the agent made through the host.
    private var lookupLabel: String {
        let arg = string("arg").map { "\u{2018}\($0)\u{2019}" } ?? ""
        let ok = payload["ok"] == .bool(true)
        let count = number("count").map { Int($0) }
        let found = { (noun: String) in count.map { "\($0) \(noun)\($0 == 1 ? "" : "s")" } ?? noun + "s" }
        switch string("tool") {
        case "library_search":
            return ok ? "Library: \(found("result")) for \(arg)" : "Library search failed: \(arg)"
        case "library_read":
            return ok ? "Read from the library: \(string("arg") ?? "an article")" : "Couldn't read from the library"
        case "web_research":
            if payload["blocked"] == .bool(true) { return "Web research refused: search the library first" }
            let secs = Int((number("seconds") ?? 0).rounded())
            return ok ? "Web research: \(found("source")) for \(arg) in \(secs)s" : "Web research failed: \(arg)"
        case "repo_wiki_search": return "Wiki: \(found("note")) for \(arg)"
        case "repo_wiki_read": return ok ? "Read wiki note \(string("arg") ?? "")" : "No such wiki note"
        case "code_map": return "Code map: \(arg)"
        default: return "Looked something up"
        }
    }

    /// 812, 4.3k, 150k.
    static func tokens(_ n: Double) -> String {
        if n < 1000 { return String(Int(n)) }
        if n < 10000 { return String(format: "%.1fk", n / 1000) }
        return "\(Int((n / 1000).rounded()))k"
    }

    /// Model calls and tool plumbing: collapsed by default on the phone.
    public var isDetail: Bool {
        ["thought", "tool_call", "tool_result", "permission", "proxy", "plan"].contains(kind)
    }
}

public struct CodeTaskList: Codable, Equatable, Sendable {
    public let tasks: [CodeTask]
    public let waiting: Int

    public init(tasks: [CodeTask], waiting: Int) { self.tasks = tasks; self.waiting = waiting }
}

public struct CodeTaskDetail: Codable, Equatable, Sendable {
    public let task: CodeTask
    public let events: [CodeTaskEvent]

    public init(task: CodeTask, events: [CodeTaskEvent]) { self.task = task; self.events = events }
}

public struct CodeDiff: Codable, Equatable, Sendable {
    public let files: [CodeFileStat]
    public let commits: [String]
    public let patch: String
    public let truncated: Bool

    public init(files: [CodeFileStat], commits: [String], patch: String, truncated: Bool) {
        self.files = files; self.commits = commits; self.patch = patch; self.truncated = truncated
    }
}

public struct CodeRepo: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let slug: String
    public let name: String
    public let cloneState: String?
    public let isDefault: Int?

    public init(id: String, slug: String, name: String, cloneState: String?, isDefault: Int?) {
        self.id = id; self.slug = slug; self.name = name; self.cloneState = cloneState; self.isDefault = isDefault
    }

    public var isDefaultRepo: Bool { (isDefault ?? 0) != 0 }
}

// MARK: Unified diff

public struct DiffLine: Equatable, Sendable {
    public enum Kind: Equatable, Sendable { case context, added, removed, note }
    public let kind: Kind
    public let text: String
    public let oldNumber: Int?
    public let newNumber: Int?
}

public struct DiffHunk: Equatable, Sendable {
    public let header: String
    public let lines: [DiffLine]
}

public struct DiffFile: Equatable, Identifiable, Sendable {
    public let path: String
    public let oldPath: String?
    public let isNew: Bool
    public let isDeleted: Bool
    public let isBinary: Bool
    public let hunks: [DiffHunk]

    public var id: String { path }
    public var added: Int { hunks.reduce(0) { $0 + $1.lines.filter { $0.kind == .added }.count } }
    public var removed: Int { hunks.reduce(0) { $0 + $1.lines.filter { $0.kind == .removed }.count } }
}

/// Parses `git diff` output into files, hunks and numbered lines — the shape
/// a phone-width review screen wants, one file collapsed at a time.
public enum UnifiedDiff {
    public static func parse(_ patch: String) -> [DiffFile] {
        var files: [DiffFile] = []
        var path: String?, oldPath: String?
        var isNew = false, isDeleted = false, isBinary = false
        var hunks: [DiffHunk] = []
        var header = ""
        var lines: [DiffLine] = []
        var oldLine = 0, newLine = 0

        func closeHunk() {
            if !header.isEmpty { hunks.append(DiffHunk(header: header, lines: lines)) }
            header = ""; lines = []
        }
        func closeFile() {
            closeHunk()
            if let path {
                files.append(DiffFile(path: path, oldPath: oldPath == path ? nil : oldPath, isNew: isNew,
                                      isDeleted: isDeleted, isBinary: isBinary, hunks: hunks))
            }
            path = nil; oldPath = nil; isNew = false; isDeleted = false; isBinary = false; hunks = []
        }

        for raw in patch.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if line.hasPrefix("diff --git ") {
                closeFile()
                // "diff --git a/x b/y": the b/ side names the file after the change.
                let rest = line.dropFirst("diff --git ".count)
                if let range = rest.range(of: " b/", options: .backwards) {
                    path = String(rest[range.upperBound...])
                    let a = rest[..<range.lowerBound]
                    oldPath = a.hasPrefix("a/") ? String(a.dropFirst(2)) : String(a)
                }
                continue
            }
            if header.isEmpty {
                if line.hasPrefix("new file mode") { isNew = true; continue }
                if line.hasPrefix("deleted file mode") { isDeleted = true; continue }
                if line.hasPrefix("Binary files") { isBinary = true; continue }
                if line.hasPrefix("rename from ") { oldPath = String(line.dropFirst("rename from ".count)); continue }
                if line.hasPrefix("rename to ") { path = String(line.dropFirst("rename to ".count)); continue }
                if line.hasPrefix("--- ") || line.hasPrefix("+++ ") || line.hasPrefix("index ")
                    || line.hasPrefix("similarity ") || line.hasPrefix("old mode") || line.hasPrefix("new mode") {
                    continue
                }
            }
            if line.hasPrefix("@@") {
                closeHunk()
                header = line
                (oldLine, newLine) = hunkStarts(line)
                continue
            }
            guard !header.isEmpty else { continue }
            if line.hasPrefix("+") {
                lines.append(DiffLine(kind: .added, text: String(line.dropFirst()), oldNumber: nil, newNumber: newLine))
                newLine += 1
            } else if line.hasPrefix("-") {
                lines.append(DiffLine(kind: .removed, text: String(line.dropFirst()), oldNumber: oldLine, newNumber: nil))
                oldLine += 1
            } else if line.hasPrefix("\\") {
                lines.append(DiffLine(kind: .note, text: line, oldNumber: nil, newNumber: nil))
            } else if line.hasPrefix(" ") {
                lines.append(DiffLine(kind: .context, text: String(line.dropFirst()), oldNumber: oldLine, newNumber: newLine))
                oldLine += 1; newLine += 1
            }
            // A bare empty line is the patch's own trailing newline.
        }
        closeFile()
        return files
    }

    /// "@@ -12,7 +12,9 @@ def f():" -> (12, 12)
    static func hunkStarts(_ header: String) -> (Int, Int) {
        func start(_ marker: Character) -> Int {
            guard let token = header.split(separator: " ").first(where: { $0.first == marker }) else { return 1 }
            return Int(token.dropFirst().split(separator: ",").first ?? "1") ?? 1
        }
        return (start("-"), start("+"))
    }
}

// MARK: Push

public enum CodePushPayload {
    /// The task a Code-tab notification is about, from its userInfo.
    public static func taskID(from userInfo: [AnyHashable: Any]) -> String? {
        guard userInfo["kind"] as? String == "code_task",
              let id = userInfo["taskId"] as? String, ULID.isValid(id) else { return nil }
        return id
    }

    /// APNs hands over the device token as bytes; the server wants hex.
    public static func hexToken(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}
