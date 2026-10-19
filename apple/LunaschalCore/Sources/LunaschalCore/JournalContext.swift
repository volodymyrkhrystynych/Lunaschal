import Foundation

/// The server owns yesterday/backlog selection and permanent dismissal, shared
/// by every client. Captions and titles are previews, not user context.
public struct JournalContextPrompt: Decodable, Identifiable {
    public let id: String
    public let title: String?
    public let createdAt: String?
    public let attachments: [Attachment]

    public struct Attachment: Decodable {
        public let id: String
        public let kind: String
        public let name: String
        public let mime: String?
        public let position: Int
        public let transcript: String?
        public let description: String?
    }

    public var displayTitle: String {
        let value = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? "Untitled media entry" : value
    }

    public var media: [JournalAttachmentItem] {
        attachments.map {
            JournalAttachmentItem(id: $0.id, entryID: id, kind: $0.kind, name: $0.name,
                                  mime: $0.mime ?? "", position: $0.position,
                                  transcript: $0.transcript, description: $0.description)
        }.sorted { ($0.position, $0.id) < ($1.position, $1.id) }
    }
}

public enum JournalContextAction: String, Encodable { case save, dismiss }

public struct JournalContextResolution: Encodable {
    public let action: JournalContextAction
    public let content: String?

    public init(action: JournalContextAction, content: String? = nil) {
        self.action = action
        self.content = action == .save ? content?.trimmingCharacters(in: .whitespacesAndNewlines) : nil
    }
}

public protocol JournalContextTransport {
    func journalContextPrompts() async throws -> [JournalContextPrompt]
    func resolveJournalContext(_ id: String, resolution: JournalContextResolution) async throws
}

extension JournalAPI: JournalContextTransport {
    public func journalContextPrompts() async throws -> [JournalContextPrompt] {
        try JSONDecoder().decode([JournalContextPrompt].self,
            from: await postJSON("api/journal/context-prompts/prepare", [String: String]()))
    }

    public func resolveJournalContext(_ id: String, resolution: JournalContextResolution) async throws {
        _ = try await postJSON("api/journal/\(try Self.pathID(id))/context-prompt", resolution)
    }
}
