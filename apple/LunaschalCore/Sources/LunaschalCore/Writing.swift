import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Writing on the phone: a book's notes and its discussions, over the desktop's
// /api/writing routes. Fetched, not replicated — notes are small and a
// discussion needs the server anyway. A discussion is an ordinary chat
// conversation with `writing_project_id` set, so its stream and its confirm
// cards go through `streamChatReply` and `resolveProposal` unchanged: the
// server reads the project from the conversation row and builds the prompt,
// the tools and the scope itself (backend/writing/discuss.py). Chapters stay
// on the desktop.

public struct WritingProject: Codable, Hashable, Identifiable {
    public let id: String
    public let title: String
    public let description: String?
    public let updatedAt: String?

    public init(id: String, title: String, description: String? = nil, updatedAt: String? = nil) {
        self.id = id; self.title = title; self.description = description; self.updatedAt = updatedAt
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
    public let updatedAt: String?

    public init(id: String, projectId: String, title: String, docType: String = "note", pinned: Bool = false,
                content: String? = nil, updatedAt: String? = nil) {
        self.id = id; self.projectId = projectId; self.title = title; self.docType = docType
        self.pinned = pinned; self.content = content; self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey { case id, projectId, title, docType, pinned, content, updatedAt }

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
    public let updatedAt: String?

    public init(id: String, title: String?, updatedAt: String? = nil) {
        self.id = id; self.title = title; self.updatedAt = updatedAt
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

    public func createWritingDiscussion(projectID: String) async throws -> String {
        struct Reply: Decodable { let id: String }
        let data = try await chatJSON("api/writing/projects/\(try Self.pathID(projectID))/conversations",
                                      [String: String]())
        return try JSONDecoder().decode(Reply.self, from: data).id
    }

    /// One discussion and its messages — the same shape as today's chat.
    public func writingConversation(_ id: String) async throws -> ChatConversation? {
        try JSONDecoder().decode(ChatConversation?.self,
                                 from: await get("api/chat/conversations/\(try Self.pathID(id))", [:]))
    }
}
