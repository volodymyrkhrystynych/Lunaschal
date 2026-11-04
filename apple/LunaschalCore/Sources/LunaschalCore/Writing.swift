import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Writing on the phone: a book's chapters, notes and discussions, over the
// desktop's /api/writing routes. Chapters and notes are kept on disk by
// WritingStore rather than the replica, because a book is edited every few
// seconds and the replica logs a full copy of a row per change. A discussion
// needs the server anyway, and is an ordinary chat conversation with
// `writing_project_id` set, so its stream and its confirm cards go through
// `streamChatReply` and `resolveProposal` unchanged: the server reads the project
// from the conversation row and builds the prompt, the tools and the scope
// itself (backend/writing/discuss.py).

public struct WritingProject: Codable, Hashable, Identifiable {
    public let id: String
    public var title: String
    public var description: String?
    public var dailyWordGoal: Int?
    public let updatedAt: String?

    public init(id: String, title: String, description: String? = nil, dailyWordGoal: Int? = nil,
                updatedAt: String? = nil) {
        self.id = id; self.title = title; self.description = description
        self.dailyWordGoal = dailyWordGoal; self.updatedAt = updatedAt
    }
}

public struct WritingChapter: Codable, Equatable, Identifiable {
    public let id: String
    public let projectId: String
    public var title: String
    public var position: Int
    /// Only on a single chapter or in a bundle; the list leaves the bodies out.
    public var content: String?
    public var revision: Int

    public init(id: String, projectId: String, title: String, position: Int = 0, content: String? = nil,
                revision: Int = 0) {
        self.id = id; self.projectId = projectId; self.title = title; self.position = position
        self.content = content; self.revision = revision
    }

    enum CodingKeys: String, CodingKey { case id, projectId, title, position, content, revision }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        projectId = try c.decode(String.self, forKey: .projectId)
        title = try c.decode(String.self, forKey: .title)
        position = try c.decodeIfPresent(Int.self, forKey: .position) ?? 0
        content = try c.decodeIfPresent(String.self, forKey: .content)
        revision = try c.decodeIfPresent(Int.self, forKey: .revision) ?? 0
    }
}

/// A whole book, bodies included: what the phone keeps to work offline.
public struct WritingBundle: Codable, Equatable {
    public let project: WritingProject
    public let chapters: [WritingChapter]
    public let notes: [WritingNote]

    public init(project: WritingProject, chapters: [WritingChapter], notes: [WritingNote]) {
        self.project = project; self.chapters = chapters; self.notes = notes
    }
}

/// Net words per 4am day. A day that cut more than it added is negative.
public struct WritingWordStats: Codable, Equatable {
    public struct Day: Codable, Equatable {
        public let day: String
        public let words: Int
    }
    public let today: String
    public let todayWords: Int
    public let dailyWordGoal: Int?
    public let days: [Day]

    public init(today: String, todayWords: Int, dailyWordGoal: Int?, days: [Day] = []) {
        self.today = today; self.todayWords = todayWords
        self.dailyWordGoal = dailyWordGoal; self.days = days
    }
}

public enum WritingDocType {
    public static let all: [(id: String, label: String)] = [
        ("character", "Character"), ("outline", "Outline"), ("worldbuilding", "World"), ("note", "Note"),
    ]
    public static func label(_ id: String) -> String { all.first { $0.id == id }?.label ?? id.capitalized }
}

public struct WritingNote: Codable, Equatable, Identifiable {
    public let id: String
    public let projectId: String
    public var title: String
    public var docType: String
    /// The server stores a 0/1; read either that or a bool.
    public var pinned: Bool
    /// Only on a single note; the list leaves the bodies out.
    public var content: String?
    public var revision: Int
    public let updatedAt: String?

    public init(id: String, projectId: String, title: String, docType: String = "note", pinned: Bool = false,
                content: String? = nil, revision: Int = 0, updatedAt: String? = nil) {
        self.id = id; self.projectId = projectId; self.title = title; self.docType = docType
        self.pinned = pinned; self.content = content; self.revision = revision; self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey { case id, projectId, title, docType, pinned, content, revision, updatedAt }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        projectId = try c.decode(String.self, forKey: .projectId)
        title = try c.decode(String.self, forKey: .title)
        docType = try c.decodeIfPresent(String.self, forKey: .docType) ?? "note"
        if let flag = try? c.decodeIfPresent(Bool.self, forKey: .pinned) {
            pinned = flag
        } else {
            pinned = (try? c.decodeIfPresent(Int.self, forKey: .pinned)).flatMap { $0 }.map { $0 != 0 } ?? false
        }
        content = try c.decodeIfPresent(String.self, forKey: .content)
        revision = try c.decodeIfPresent(Int.self, forKey: .revision) ?? 0
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

public struct WritingNoteRevision: Codable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let content: String
    /// "edit", "accept" (a discussion proposal the author confirmed) or "restore".
    public let source: String
    public let createdAt: String

    public var label: String {
        ["edit": "Before your edits", "accept": "Before an accepted proposal",
         "restore": "Before a restore"][source] ?? source
    }
}

public struct WritingDiscussion: Codable, Hashable, Identifiable {
    public let id: String
    public let title: String?
    /// The chapter it was opened from, which "this chapter" then means.
    public let writingChapterId: String?
    public let updatedAt: String?

    public init(id: String, title: String?, writingChapterId: String? = nil, updatedAt: String? = nil) {
        self.id = id; self.title = title
        self.writingChapterId = writingChapterId; self.updatedAt = updatedAt
    }

    public var displayTitle: String {
        let trimmed = (title ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "Untitled discussion" : trimmed
    }
}

/// The fields a note edit sends; nil fields are left out of the PATCH.
public struct WritingNoteEdit: Encodable, Equatable {
    public var title: String?
    public var content: String?
    public var docType: String?
    public var pinned: Bool?

    public init(title: String? = nil, content: String? = nil, docType: String? = nil, pinned: Bool? = nil) {
        self.title = title; self.content = content; self.docType = docType; self.pinned = pinned
    }
}

extension JournalAPI {
    public func writingProjects() async throws -> [WritingProject] {
        try JSONDecoder().decode([WritingProject].self, from: await get("api/writing/projects", [:]))
    }

    /// A new book; the server answers with its id.
    public func createWritingProject(title: String, description: String?) async throws -> String {
        struct Body: Encodable { let title: String; let description: String? }
        struct Reply: Decodable { let id: String }
        let data = try await chatJSON("api/writing/projects", Body(title: title, description: description))
        return try JSONDecoder().decode(Reply.self, from: data).id
    }

    public func writingNotes(projectID: String) async throws -> [WritingNote] {
        try JSONDecoder().decode([WritingNote].self,
                                 from: await get("api/writing/projects/\(try Self.pathID(projectID))/notes", [:]))
    }

    public func writingNote(_ id: String) async throws -> WritingNote {
        try JSONDecoder().decode(WritingNote.self, from: await get("api/writing/notes/\(try Self.pathID(id))", [:]))
    }

    public func createWritingNote(projectID: String, title: String, docType: String = "note") async throws -> String {
        struct Reply: Decodable { let id: String }
        let data = try await chatJSON("api/writing/projects/\(try Self.pathID(projectID))/notes",
                                      ["title": title, "docType": docType])
        return try JSONDecoder().decode(Reply.self, from: data).id
    }

    public func updateWritingNote(_ id: String, _ edit: WritingNoteEdit) async throws {
        var req = request("api/writing/notes/\(try Self.pathID(id))", method: "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(edit)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    public func writingNoteRevisions(_ id: String) async throws -> [WritingNoteRevision] {
        try JSONDecoder().decode([WritingNoteRevision].self,
                                 from: await get("api/writing/notes/\(try Self.pathID(id))/revisions", [:]))
    }

    public func restoreWritingNote(_ id: String, revisionID: String) async throws -> WritingNote {
        let data = try await chatJSON(
            "api/writing/notes/\(try Self.pathID(id))/revisions/\(try Self.pathID(revisionID))/restore",
            [String: String]())
        return try JSONDecoder().decode(WritingNote.self, from: data)
    }

    public func writingDiscussions(projectID: String) async throws -> [WritingDiscussion] {
        try JSONDecoder().decode([WritingDiscussion].self,
                                 from: await get("api/writing/projects/\(try Self.pathID(projectID))/conversations", [:]))
    }

    public func createWritingDiscussion(projectID: String, chapterID: String? = nil) async throws -> String {
        struct Reply: Decodable { let id: String }
        var body: [String: String] = [:]
        if let chapterID { body["chapterId"] = chapterID }
        let data = try await chatJSON("api/writing/projects/\(try Self.pathID(projectID))/conversations", body)
        return try JSONDecoder().decode(Reply.self, from: data).id
    }

    public func updateWritingProject(_ id: String, title: String? = nil, description: String? = nil) async throws {
        var body: [String: String] = [:]
        if let title { body["title"] = title }
        if let description { body["description"] = description }
        try await writingPatch("api/writing/projects/\(try Self.pathID(id))", body)
    }

    /// nil clears the goal, so this one always sends the field.
    public func setWritingGoal(_ id: String, wordsPerDay: Int?) async throws {
        struct Body: Encodable {
            let goal: Int?
            enum CodingKeys: String, CodingKey { case goal = "dailyWordGoal" }
            func encode(to encoder: Encoder) throws {
                var c = encoder.container(keyedBy: CodingKeys.self)
                try c.encode(goal, forKey: .goal)
            }
        }
        try await writingPatch("api/writing/projects/\(try Self.pathID(id))", Body(goal: wordsPerDay))
    }

    public func deleteWritingProject(_ id: String) async throws {
        try await send("api/writing/projects/\(try Self.pathID(id))", method: "DELETE")
    }

    public func deleteWritingDocument(_ key: WritingDocumentKey) async throws {
        try await send("api/writing/\(key.kind.routeName)/\(try Self.pathID(key.id))", method: "DELETE")
    }

    public func orderWritingChapters(projectID: String, ids: [String]) async throws {
        var req = request("api/writing/projects/\(try Self.pathID(projectID))/chapters/order", method: "PUT")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["ids": ids])
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    public func writingWordStats(projectID: String) async throws -> WritingWordStats {
        try JSONDecoder().decode(WritingWordStats.self,
                                 from: await get("api/writing/projects/\(try Self.pathID(projectID))/stats", [:]))
    }

    public func writingChapterRevisions(_ id: String) async throws -> [WritingNoteRevision] {
        try JSONDecoder().decode([WritingNoteRevision].self,
                                 from: await get("api/writing/chapters/\(try Self.pathID(id))/revisions", [:]))
    }

    public func restoreWritingChapter(_ id: String, revisionID: String) async throws -> WritingChapter {
        let data = try await chatJSON(
            "api/writing/chapters/\(try Self.pathID(id))/revisions/\(try Self.pathID(revisionID))/restore",
            [String: String]())
        return try JSONDecoder().decode(WritingChapter.self, from: data)
    }

    private func writingPatch<Body: Encodable>(_ path: String, _ body: Body) async throws {
        var req = request(path, method: "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    /// One discussion and its messages — the same shape as today's chat.
    public func writingConversation(_ id: String) async throws -> ChatConversation? {
        try JSONDecoder().decode(ChatConversation?.self,
                                 from: await get("api/chat/conversations/\(try Self.pathID(id))", [:]))
    }
}
