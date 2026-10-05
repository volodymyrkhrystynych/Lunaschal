import Foundation

/// A voice message for the Chat tab. Stopping the recording is the send: the
/// clip is kept here until `POST /api/chat/conversations/<id>/recordings` has
/// it, and the server transcribes it and starts the reply itself
/// (backend/chat/autoreply.py). So a question asked offline is answered once
/// the phone is back in reach of the server.
public struct ChatRecording: Codable, Equatable, Identifiable {
    public enum State: String, Codable { case recording, pending, failed }

    /// The attachment id the server stores the clip under.
    public let id: String
    public let messageID: String
    /// Today's conversation when it was recorded, if the phone knew it. Without
    /// one, the clip goes into whatever conversation is today's when it uploads.
    public let conversationID: String?
    /// Words typed in the box when the mic was stopped, and photos staged then.
    public let text: String?
    public let attachmentIDs: [String]
    public let createdAt: Date
    public var state: State
    public var lastError: String?
}

public final class ChatRecordingStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [ChatRecording] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(ChatRecording.self, from: Data(contentsOf: $0)) }
            .sorted { $0.createdAt < $1.createdAt }
    }

    /// Writes the manifest before any audio, so a crash mid-recording still
    /// leaves a record saying whose file it is.
    public func begin(conversationID: String?, text: String?, attachmentIDs: [String], now: Date = Date()) throws -> ChatRecording {
        let typed = text?.trimmingCharacters(in: .whitespacesAndNewlines)
        let item = ChatRecording(id: ULID.make(now: now), messageID: ULID.make(now: now), conversationID: conversationID,
                                 text: typed?.isEmpty == false ? typed : nil, attachmentIDs: attachmentIDs,
                                 createdAt: now, state: .recording, lastError: nil)
        try save(item)
        return item
    }

    /// Queues a finished clip; an empty one has nothing to send and is dropped.
    @discardableResult
    public func finish(_ id: String) throws -> ChatRecording? {
        guard var item = try list().first(where: { $0.id == id }) else { return nil }
        guard size(of: audioURL(item)) > 0 else { try remove(item); return nil }
        item.state = .pending
        try save(item)
        return item
    }

    /// After a crash or a kill mid-recording: keep what was captured, send it.
    public func recoverInterrupted() throws {
        for item in try list() where item.state == .recording { try finish(item.id) }
    }

    public func audioURL(_ item: ChatRecording) -> URL { root.appendingPathComponent(item.id).appendingPathExtension("m4a") }

    public func save(_ item: ChatRecording) throws {
        guard ULID.isValid(item.id), ULID.isValid(item.messageID) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(item).write(to: manifest(item.id), options: .atomic)
    }

    public func remove(_ item: ChatRecording) throws {
        for url in [manifest(item.id), audioURL(item)] where fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }

    private func size(of url: URL) -> Int {
        (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
    }
}

public protocol ChatRecordingTransport {
    /// Finds or creates today's conversation.
    func chatConversationID() async throws -> String
    func sendChatRecording(_ item: ChatRecording, conversationID: String, audioURL: URL) async throws
}

/// Uploads queued voice messages oldest first, so they arrive in the order
/// they were spoken. Same rules as the Workout outbox: a clip the server
/// refuses is marked failed and the rest carry on; anything else stops the pass.
@MainActor
public final class ChatRecordingSync {
    private let store: ChatRecordingStore

    public init(store: ChatRecordingStore) { self.store = store }

    public func run(using transport: ChatRecordingTransport) async throws {
        var today: String?
        for var item in try store.list() where item.state == .pending {
            try Task.checkCancellation()
            do {
                let conversation: String
                if let known = item.conversationID { conversation = known }
                else if let today { conversation = today }
                else { conversation = try await transport.chatConversationID(); today = conversation }
                try await transport.sendChatRecording(item, conversationID: conversation, audioURL: store.audioURL(item))
                try store.remove(item)
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
                item.lastError = error.localizedDescription
                if let http = error as? HTTPFailure, ![401, 403].contains(http.status), !http.retryAutomatically {
                    item.state = .failed
                    try store.save(item)
                    continue
                }
                try store.save(item)
                throw error
            }
        }
    }
}
