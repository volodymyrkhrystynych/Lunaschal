import Foundation

// The Chat tab on the phone: the same one conversation per 4am day as the
// desktop's (src/components/Chat/ChatPanel.tsx), over the same routes. The
// rules here are ports of src/lib/chatSegments.ts, chatPolling.ts,
// agentSteps.ts and chatAttachments.ts; keep them in step.

public struct ChatAttachment: Codable, Equatable, Identifiable {
    public let id: String
    public let conversationId: String
    public let messageId: String?
    public let mime: String?
    /// "image" for a photo, "audio" for a clip the message was spoken into.
    public let kind: String
    public let description: String?
    public let descriptionStatus: String?
    public let descriptionError: String?
    public let transcript: String?
    public let transcriptStatus: String?
    public let transcriptError: String?
    public let position: Int?
    public let createdAt: String?

    public var isPhoto: Bool { kind != "audio" }
}

public struct ChatMessage: Codable, Equatable, Identifiable {
    public let id: String
    public let role: String
    public let content: String
    public let metadata: String?
    /// "streaming" while the server's background run is still writing the reply.
    public let status: String?
    public let error: String?
    public let rawContent: String?
    public let attachments: [ChatAttachment]?
    public let createdAt: String
    public let finishedAt: String?

    public init(id: String, role: String, content: String, metadata: String? = nil, status: String? = nil,
                error: String? = nil, rawContent: String? = nil, attachments: [ChatAttachment]? = nil,
                createdAt: String, finishedAt: String? = nil) {
        self.id = id; self.role = role; self.content = content; self.metadata = metadata; self.status = status
        self.error = error; self.rawContent = rawContent; self.attachments = attachments
        self.createdAt = createdAt; self.finishedAt = finishedAt
    }

    public var meta: ChatMeta { ChatMeta(metadata) }
    public var isBreak: Bool { role == "system" && JSONValue.parse(metadata)?["break"]?.bool == true }
    public var photos: [ChatAttachment] { (attachments ?? []).filter(\.isPhoto) }
    public var clips: [ChatAttachment] { (attachments ?? []).filter { !$0.isPhoto } }
    /// A clip still being transcribed is the whole of its message, so it counts.
    public var hasBody: Bool { !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !(attachments ?? []).isEmpty }
    /// A reply that ended with no text: the trace is all there is to show.
    public var isBlankReply: Bool { role == "assistant" && !hasBody && status != "streaming" && status != "error" }
    /// A reply is stamped by when it finished, a question by when it was sent.
    public var stampedAt: String { finishedAt ?? createdAt }
}

public struct ChatConversation: Codable, Equatable {
    public let id: String
    public var messages: [ChatMessage]

    public init(id: String, messages: [ChatMessage]) { self.id = id; self.messages = messages }
}

public enum ChatSegments {
    /// What the model is sent: everything after the last "New chat" break.
    public static func context(_ messages: [ChatMessage]) -> [ChatMessage] {
        guard let last = messages.lastIndex(where: \.isBreak) else { return messages }
        return Array(messages[(last + 1)...])
    }

    /// Whether anything in the current segment can be cleared.
    public static func hasCurrentSegment(_ messages: [ChatMessage]) -> Bool {
        context(messages).contains { $0.role == "user" || $0.role == "assistant" }
    }

    /// Keep asking the server while a reply is generating (only the newest row
    /// counts: an older one stuck "streaming" is a dead run) or any clip is
    /// still being transcribed, since neither is returned to a request.
    public static func shouldPoll(_ messages: [ChatMessage]) -> Bool {
        if messages.last?.status == "streaming" { return true }
        return messages.contains { ($0.attachments ?? []).contains { $0.transcriptStatus == "running" } }
    }

    public static let pollInterval: TimeInterval = 1.5
}

public struct AgentStep: Equatable {
    public let tool: String?
    public let arg: String?
    public let ok: Bool?
    public let count: Int?
    public let queries: [String]?
    public let title: String?
    public let error: String?
    public let timedOut: Bool
    public let kind: String?
    public let duplicate: Bool

    public init(_ value: JSONValue) {
        tool = value["tool"]?.string
        arg = value["arg"]?.string
        ok = value["ok"]?.bool
        count = value["count"]?.int
        queries = value["queries"]?.array?.compactMap(\.string)
        title = value["title"]?.string
        error = value["error"]?.string
        timedOut = value["timedOut"]?.bool == true
        kind = value["kind"]?.string
        duplicate = value["duplicate"]?.bool == true
    }

    public var writesChatTodo: Bool { tool == "add_todos" && ok == true }

    static let proposalLabels = [
        "propose_calendar_event": "a calendar event", "propose_calorie_log": "a calorie entry",
        "propose_food_log": "a food entry", "draft_flashcard": "a flashcard draft", "propose_flashcards": "flashcards",
    ]

    /// `stepLabel` in src/lib/agentSteps.ts, word for word.
    public var label: String {
        let target = title ?? arg ?? ""
        let succeeded = ok == true
        func failed(_ text: String, dash: Bool = false) -> String {
            guard let error, !error.isEmpty else { return text }
            return dash ? "\(text) — \(error)" : "\(text): \(error)"
        }
        func found(_ what: String, _ subject: String) -> String {
            (count ?? 0) > 0 ? "Searched \(what) for \"\(subject)\" — \(count!) found"
                             : "Searched \(what) for \"\(subject)\" — nothing found"
        }
        switch tool {
        case "web_search":
            return succeeded ? "Searched the web for \"\(target)\" — \(count ?? 0) results" : failed("Web search unavailable")
        case "web_fetch":
            return succeeded ? "Read \(target)" : "Could not read \(target)"
        case "local_knowledge_search":
            guard succeeded else { return failed("Offline library search unavailable") }
            if let queries, queries.count > 1 {
                return "Searched the offline library with \(queries.count) queries — \(count ?? 0) results"
            }
            return "Searched the offline library for \"\(target)\" — \(count ?? 0) results"
        case "local_knowledge_read":
            return succeeded ? "Read offline article: \(target)" : failed("Could not read offline article")
        case "delegate":
            return succeeded ? "Asked web research about \"\(target)\" — \(count ?? 0) sources" : failed("Web research unavailable")
        case "deep_research":
            let sources = "\(count ?? 0) source\(count == 1 ? "" : "s")"
            guard succeeded else { return failed("Deep research failed") }
            return timedOut ? "Deep research timed out — answered from \(sources) so far" : "Deep-researched \"\(target)\" — \(sources)"
        case "search_conversations":
            return succeeded ? found("past chats", target) : failed("Couldn't search past chats", dash: true)
        case "search_journal":
            return succeeded ? found("the journal", target) : failed("Couldn't search the journal", dash: true)
        case "read_day":
            return succeeded ? "Looked up \(target)" : failed("Couldn't look up that day", dash: true)
        case "writing_list":
            return succeeded ? "Checked the project's chapters and notes (\(count ?? 0))" : failed("Couldn't read the project", dash: true)
        case "writing_search":
            return succeeded ? found("the project", target) : failed("Couldn't search the project", dash: true)
        case "writing_read":
            return succeeded ? "Read \(kind == "chapter" ? "chapter" : "note"): \(target)" : failed("Couldn't open \"\(target)\"", dash: true)
        case "idea_list":
            return succeeded ? "Checked the other ideas for this repo (\(count ?? 0))" : failed("Couldn't read the backlog", dash: true)
        case "idea_search":
            guard succeeded else { return failed("Couldn't search the other ideas", dash: true) }
            return (count ?? 0) > 0 ? "Searched other ideas for \"\(target)\" — \(count!) found"
                                    : "Searched other ideas for \"\(target)\" — nothing found"
        case "idea_read":
            return succeeded ? "Read idea: \(target)" : failed("Couldn't open that idea", dash: true)
        case "wiki_list": return "Checked the research wiki (\(count ?? 0) articles)"
        case "wiki_search": return "Searched the wiki for \"\(target)\""
        case "wiki_read": return "Read wiki note: \(target)"
        case "ask_user":
            return target.isEmpty ? "Asked for clarification" : "Asked for clarification about \(target)"
        case "remember":
            guard succeeded else { return failed("Didn't remember that", dash: true) }
            return duplicate ? "Already remembered: \(target)" : "Remembered: \(target)"
        case "revise_memory":
            return succeeded ? "Updating what's remembered: \(target)" : failed("Couldn't update what's remembered", dash: true)
        case "create_note_to_self":
            return succeeded ? "Noted: \(target)" : failed("Didn't save that note", dash: true)
        case "add_todos":
            return succeeded ? "Added to today's to-dos: \(target)" : failed("Didn't add that", dash: true)
        default:
            if let tool, let kind = Self.proposalLabels[tool] {
                // "Staged", never "saved": nothing is written until the card is accepted.
                return succeeded ? "Staged \(kind)\(target.isEmpty ? "" : ": \(target)")"
                                 : failed("Could not stage \(kind)", dash: true)
            }
            return tool.map { "Ran \($0)" } ?? "Thinking"
        }
    }
}

public struct AgentSource: Equatable {
    public let url: String
    public let title: String?
}

/// A delegate confirm card, persisted on the reply's `metadata.proposals`.
public struct ChatProposal: Equatable, Identifiable {
    public struct Source: Equatable { public let id: String; public let label: String; public let recordedAt: String }

    public let id: String
    public let kind: String
    public let data: [String: JSONValue]
    public let status: String
    public let result: [String: JSONValue]
    public let reconstructionDay: String?
    public let evidence: String?
    public let sources: [Source]

    init?(_ value: JSONValue) {
        guard let id = value["id"]?.string, let kind = value["kind"]?.string else { return nil }
        self.id = id
        self.kind = kind
        data = value["data"]?.object ?? [:]
        status = value["status"]?.string ?? "pending"
        result = value["result"]?.object ?? [:]
        reconstructionDay = value["reconstructionDay"]?.string
        evidence = value["evidence"]?.string
        sources = (value["sources"]?.array ?? []).compactMap { source in
            guard let id = source["id"]?.string else { return nil }
            return Source(id: id, label: source["label"]?.string ?? "", recordedAt: source["recordedAt"]?.string ?? "")
        }
    }

    public var isPending: Bool { status == "pending" }

    public var headline: String {
        if let reconstructionDay { return "Suggested event · \(reconstructionDay)" }
        return ["calendar": "Save as calendar event?", "calorie": "Log calories?", "food": "Add to your food log?",
                "recipe": "Save this recipe?", "recipe_link": "Link this meal to that recipe?",
                "flashcards": "Generate flashcards?"][kind] ?? "Save this?"
    }

    public var acceptLabel: String {
        if reconstructionDay != nil { return "Approve event" }
        return ["calendar": "Save", "calorie": "Log", "food": "Log Meal", "recipe": "Save", "recipe_link": "Link",
                "flashcards": "Queue Cards"][kind] ?? "Save"
    }

    public var resolvedLabel: String {
        if status == "dismissed" { return "Dismissed" }
        switch kind {
        case "calendar": return "Saved to calendar"
        case "calorie": return "Logged calories"
        case "food":
            return result["calorieLogId"]?.string != nil ? "Saved to your food log, with calories" : "Saved to your food log"
        case "recipe": return "Saved to your recipes"
        case "recipe_link": return "Linked to your recipe"
        case "flashcards":
            let count = result["count"]?.int ?? 0
            return "Queued \(count) card\(count == 1 ? "" : "s") for review in Learning"
        default: return "Resolved"
        }
    }
}

/// A reply's agent metadata: `{steps, sources, thinking, truncated, timedOut,
/// proposals, savedAsJournal}`. Anything missing or malformed reads as empty.
public struct ChatMeta: Equatable {
    public let steps: [AgentStep]
    public let sources: [AgentSource]
    public let thinking: String
    public let truncated: Bool
    public let timedOut: Bool
    public let proposals: [ChatProposal]
    public let savedAsJournal: Bool

    public init(_ metadata: String?) {
        let value = JSONValue.parse(metadata)
        steps = (value?["steps"]?.array ?? []).map(AgentStep.init)
        sources = (value?["sources"]?.array ?? []).compactMap { source in
            source["url"]?.string.map { AgentSource(url: $0, title: source["title"]?.string) }
        }
        thinking = value?["thinking"]?.string ?? ""
        truncated = value?["truncated"]?.bool == true
        timedOut = value?["timedOut"]?.bool == true
        proposals = (value?["proposals"]?.array ?? []).compactMap(ChatProposal.init)
        let saved = value?["savedAsJournal"]
        savedAsJournal = saved != nil && saved != .null && saved != .bool(false)
    }
}

/// One frame of `POST /api/chat/stream`, which sends every event as a single
/// `data:` line: the row the reply is going into first, then tool steps,
/// reasoning and text as they arrive, then `done` with the confirm cards.
public enum ChatStreamEvent: Equatable {
    case messageID(String)
    case step(AgentStep)
    case thinking(String)
    case content(String)
    /// The `flashcard_draft` contents, the one proposal the stream hands over live.
    case done(flashcardDrafts: [String])
    case error(String)
    case end

    public static func parse(line: String) -> [ChatStreamEvent] {
        guard line.hasPrefix("data:") else { return [] }
        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        if payload == "[DONE]" { return [.end] }
        guard let value = JSONValue.parse(payload) else { return [] }
        var events: [ChatStreamEvent] = []
        if let id = value["messageId"]?.string { events.append(.messageID(id)) }
        if value["tool"] != nil { events.append(.step(AgentStep(value))) }
        if let text = value["thinking"]?.string, !text.isEmpty { events.append(.thinking(text)) }
        if let text = value["content"]?.string, !text.isEmpty { events.append(.content(text)) }
        if value["done"]?.bool == true {
            let drafts = (value["proposals"]?.array ?? []).compactMap { proposal -> String? in
                guard proposal["kind"]?.string == "flashcard_draft",
                      let text = proposal["data"]?["content"]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                      !text.isEmpty else { return nil }
                return text
            }
            events.append(.done(flashcardDrafts: drafts))
        }
        if let error = value["error"]?.string { events.append(.error(error)) }
        return events
    }
}

/// What the stream request carries for one turn of the current segment.
public struct ChatTurn: Encodable, Equatable {
    public let id: String
    public let role: String
    public let content: String
    public let metadata: String?
    public let createdAt: String
    public let attachmentIds: [String]

    public init(id: String, role: String, content: String, metadata: String?, createdAt: String, attachmentIds: [String]) {
        self.id = id; self.role = role; self.content = content; self.metadata = metadata
        self.createdAt = createdAt; self.attachmentIds = attachmentIds
    }

    public init(_ message: ChatMessage) {
        self.init(id: message.id, role: message.role, content: message.content, metadata: message.metadata,
                  createdAt: message.createdAt, attachmentIds: (message.attachments ?? []).map(\.id))
    }

    // `metadata` goes out as null rather than being left out, as the desktop sends it.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(role, forKey: .role)
        try container.encode(content, forKey: .content)
        try container.encode(metadata, forKey: .metadata)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(attachmentIds, forKey: .attachmentIds)
    }

    enum CodingKeys: String, CodingKey { case id, role, content, metadata, createdAt, attachmentIds }

    /// The current segment plus the message just added.
    public static func request(history: [ChatMessage], adding message: ChatTurn) -> [ChatTurn] {
        ChatSegments.context(history).map(ChatTurn.init) + [message]
    }
}

/// The composer's line under the staged photos (`photoStatusMessage`). The
/// phone doesn't read the server's model settings, so it reports only what
/// the photos themselves say.
public enum ChatPhotoStatus {
    public static func message(_ staged: [ChatAttachment]) -> String? {
        let running = staged.filter { $0.descriptionStatus == "running" }.count
        if running > 0 { return running == 1 ? "Reading the photo…" : "Reading \(running) photos…" }
        let failed = staged.filter { $0.descriptionStatus == "error" }.count
        if failed > 0 {
            return failed == 1 ? "One photo couldn't be read — it'll be attached, but not described."
                               : "\(failed) photos couldn't be read — they'll be attached, but not described."
        }
        return nil
    }
}

/// A day's to-do in the Chat tab's bar, written by the assistant or the briefing.
public struct ChatTodo: Codable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let notes: String?
    public let due: String?
    public let priority: Int
    public let done: Bool

    public init(id: String, title: String, notes: String?, due: String?, priority: Int, done: Bool) {
        self.id = id; self.title = title; self.notes = notes; self.due = due; self.priority = priority; self.done = done
    }

    public static let priorities = [1: "Very unimportant", 2: "Unimportant", 3: "Normal", 4: "Important", 5: "Very important"]

    /// "N to-dos today", or the bar's name when there are none.
    public static func summary(_ todos: [ChatTodo]) -> String {
        guard !todos.isEmpty else { return "Today's to-dos" }
        let pending = todos.filter { !$0.done }.count
        return "\(pending) to-do\(pending == 1 ? "" : "s") today"
    }
}

/// "Send to permanent": the permanent list's fields for a chat to-do.
public struct TodoPromotion: Encodable, Equatable {
    public var title: String
    public var notes: String?
    /// Unix seconds at local noon of the chosen day, as `dueInputToUnix` sends it.
    public var due: Int?
    public var priority: Int
    public var list: String
    public var repeatInterval: Int?
    public var repeatUnit: String?

    public init(title: String, notes: String? = nil, due: Int? = nil, priority: Int = 3, list: String = "todo",
                repeatInterval: Int? = nil, repeatUnit: String? = nil) {
        self.title = title; self.notes = notes; self.due = due; self.priority = priority; self.list = list
        self.repeatInterval = repeatInterval; self.repeatUnit = repeatUnit
    }

    public static func dueSeconds(_ day: Date, calendar: Calendar = .current) -> Int {
        var parts = calendar.dateComponents([.year, .month, .day], from: day)
        parts.hour = 12
        return Int(calendar.date(from: parts)!.timeIntervalSince1970)
    }
}

/// A lesson card drafted from "flashcard this", waiting for Approve.
public struct DraftCard: Codable, Equatable, Identifiable {
    public let id: String
    public let question: String
    public let answer: String
    public init(id: String, question: String, answer: String) { self.id = id; self.question = question; self.answer = answer }
}

public enum CardApproval: Equatable {
    case approved
    case duplicate(question: String, score: Double)
}

/// The calendar's six colour categories (src/lib/calendarCategories.ts).
public enum EventCategories {
    public static let all: [(id: String, label: String)] = [
        ("leisure", "Leisure"), ("work", "Work"), ("exercise", "Exercise"),
        ("family", "Family"), ("outside", "Outside"), ("indoors", "Indoors"),
    ]
}
