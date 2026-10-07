import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Chat (the desktop's /api/chat and /api/tasks/chat-todos routes)

extension JournalAPI: ChatRecordingTransport {
    /// Today's conversation and its messages, or nil before anything was said.
    public func chatToday() async throws -> ChatConversation? {
        let data = try await get("api/chat/today", [:])
        return try JSONDecoder().decode(ChatConversation?.self, from: data)
    }

    public func chatConversationID() async throws -> String {
        struct Reply: Decodable { let id: String }
        return try JSONDecoder().decode(Reply.self, from: await postJSON("api/chat/conversations", [String: String]())).id
    }

    /// Saves the user's message; its photos must already be staged on the server.
    public func addChatMessage(conversationID: String, content: String, attachmentIDs: [String]) async throws -> String {
        struct Body: Encodable { let role = "user"; let content: String; let attachmentIds: [String] }
        struct Reply: Decodable { let id: String }
        let data = try await postJSON("api/chat/conversations/\(try Self.pathID(conversationID))/messages",
                                      Body(content: content, attachmentIds: attachmentIDs))
        return try JSONDecoder().decode(Reply.self, from: data).id
    }

    /// "New chat" carries a compact summary across the break; "Clean slate" doesn't.
    public func startNewChat(conversationID: String, carryContext: Bool) async throws {
        _ = try await postJSON("api/chat/conversations/\(try Self.pathID(conversationID))/break",
                               ["carryContext": carryContext])
    }

    #if canImport(Darwin)
    /// Starts the reply and relays its frames. The reply runs on the server
    /// whether or not this stream is still being read, writing into the row
    /// the first frame names, so a dropped stream is recovered by `chatToday`.
    public func streamChatReply(conversationID: String, turns: [ChatTurn]) -> AsyncThrowingStream<ChatStreamEvent, Error> {
        struct Body: Encodable { let messages: [ChatTurn]; let conversationId: String }
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var req = request("api/chat/stream", method: "POST")
                    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    req.timeoutInterval = 15 * 60
                    req.httpBody = try JSONEncoder().encode(Body(messages: turns, conversationId: conversationID))
                    let (bytes, response) = try await session.bytes(for: req)
                    guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
                    guard (200..<300).contains(http.statusCode) else {
                        var body = Data()
                        for try await byte in bytes { body.append(byte); if body.count > 64 * 1024 { break } }
                        throw ChatError.server(Self.errorMessage(body) ?? HTTPFailure(status: http.statusCode).localizedDescription)
                    }
                    for try await line in bytes.lines {
                        for event in ChatStreamEvent.parse(line: line) { continuation.yield(event) }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
    #endif

    /// Stages photos for the next message. The server starts reading them at
    /// once, so a photo is usually described by the time the message is sent.
    public func uploadChatPhotos(conversationID: String, photos: [(data: Data, filename: String, contentType: String)],
                                 latitude: Double?, longitude: Double?) async throws -> [ChatAttachment] {
        var sources: [URL] = []
        defer { sources.forEach { try? FileManager.default.removeItem(at: $0) } }
        var files: [MultipartFile] = []
        for photo in photos {
            let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try photo.data.write(to: url)
            sources.append(url)
            files.append(MultipartFile(field: "image", filename: photo.filename, contentType: photo.contentType, source: url))
        }
        var fields: [(String, String)] = []
        if let latitude, let longitude { fields = [("latitude", String(latitude)), ("longitude", String(longitude))] }
        let (body, boundary) = try writeMultipart(fields: fields, files: files)
        defer { try? FileManager.default.removeItem(at: body) }
        var req = request("api/chat/conversations/\(try Self.pathID(conversationID))/attachments", method: "POST")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body)
        try checkChat(data, response)
        return try JSONDecoder().decode([ChatAttachment].self, from: data)
    }

    public func chatAttachment(_ id: String) async throws -> ChatAttachment {
        try JSONDecoder().decode(ChatAttachment.self, from: await get("api/chat/attachments/\(try Self.pathID(id))", [:]))
    }

    public func chatAttachmentData(_ id: String) async throws -> Data {
        try await get("api/chat/attachments/\(try Self.pathID(id))/file", [:])
    }

    /// Takes back a staged photo before its message is sent.
    public func deleteChatAttachment(_ id: String) async throws {
        try await send("api/chat/attachments/\(try Self.pathID(id))", method: "DELETE")
    }

    public func sendChatRecording(_ item: ChatRecording, conversationID: String, audioURL: URL) async throws {
        guard (try audioURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else { throw CaptureError.missingAudio }
        var fields = [("attachmentId", item.id), ("messageId", item.messageID),
                      ("attachmentIds", String(decoding: try JSONEncoder().encode(item.attachmentIDs), as: UTF8.self)),
                      // When it was said, not when it synced: the server reads it as the user awake.
                      ("capturedAt", ISO8601DateFormatter().string(from: item.createdAt))]
        if let text = item.text { fields.append(("text", text)) }
        let (body, boundary) = try writeMultipart(fields: fields, files: [
            MultipartFile(field: "audio", filename: "recording.m4a", contentType: "audio/mp4", source: audioURL)
        ])
        defer { try? FileManager.default.removeItem(at: body) }
        var req = request("api/chat/conversations/\(try Self.pathID(conversationID))/recordings", method: "POST")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body)
        try check(data, response)
        try Self.validateChatRecordingAcknowledgement(data, for: item)
    }

    /// The reply names the message and the clip; both must be this recording's.
    public static func validateChatRecordingAcknowledgement(_ data: Data, for item: ChatRecording) throws {
        struct Reply: Decodable { let id: String; let attachment: Attachment; struct Attachment: Decodable { let id: String } }
        let reply = try JSONDecoder().decode(Reply.self, from: data)
        guard reply.id == item.messageID, reply.attachment.id == item.id else { throw CaptureError.invalidResponse }
    }

    /// Accepts (with what the card shows, edits included) or dismisses a confirm card.
    public func resolveProposal(messageID: String, proposalID: String, accept: Bool, data: [String: JSONValue]?) async throws {
        struct Body: Encodable { let action: String; let data: [String: JSONValue]? }
        var req = request("api/chat/proposals/\(try Self.pathID(messageID))/\(try Self.pathID(proposalID))", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(Body(action: accept ? "accept" : "dismiss", data: accept ? data : nil))
        let (reply, response) = try await session.data(for: req)
        try checkChat(reply, response)
    }

    // MARK: Today's to-dos

    public func chatTodos() async throws -> [ChatTodo] {
        try JSONDecoder().decode([ChatTodo].self, from: await get("api/tasks/chat-todos", [:]))
    }

    public func updateChatTodo(_ id: String, title: String? = nil, done: Bool? = nil) async throws {
        struct Body: Encodable { let title: String?; let done: Bool? }
        var req = request("api/tasks/chat-todos/\(try Self.pathID(id))", method: "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(Body(title: title, done: done))
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    public func deleteChatTodo(_ id: String) async throws {
        try await send("api/tasks/chat-todos/\(try Self.pathID(id))", method: "DELETE")
    }

    public func promoteChatTodo(_ id: String, _ todo: TodoPromotion) async throws {
        var req = request("api/tasks/chat-todos/\(try Self.pathID(id))/promote", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(todo)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    // MARK: "Flashcard this" drafts

    public func draftCards(from note: String) async throws -> [DraftCard] {
        struct Reply: Decodable { let cards: [DraftCard] }
        return try JSONDecoder().decode(Reply.self, from: await chatJSON("api/learning/generate-from-note", ["content": note])).cards
    }

    public func approveCard(_ id: String, force: Bool) async throws -> CardApproval {
        struct Reply: Decodable {
            let status: String; let score: Double?; let similar: Similar?
            struct Similar: Decodable { let question: String }
        }
        let reply = try JSONDecoder().decode(Reply.self, from: await chatJSON("api/learning/queue/\(try Self.pathID(id))/approve",
                                                                              ["force": force]))
        if reply.status == "duplicateHint", let similar = reply.similar {
            return .duplicate(question: similar.question, score: reply.score ?? 0)
        }
        return .approved
    }

    public func regenerateCard(_ id: String, direction: String) async throws -> [DraftCard] {
        struct Reply: Decodable { let cards: [DraftCard]? }
        return try JSONDecoder().decode(Reply.self, from: await chatJSON("api/learning/queue/\(try Self.pathID(id))/regenerate",
                                                                         ["direction": direction])).cards ?? []
    }

    public func discardCard(_ id: String) async throws {
        try await send("api/learning/queue/\(try Self.pathID(id))", method: "DELETE")
    }

    // MARK: Helpers

    func chatJSON<Body: Encodable>(_ path: String, _ body: Body) async throws -> Data {
        var req = request(path, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
        return data
    }

    func send(_ path: String, method: String) async throws {
        let (data, response) = try await session.data(for: request(path, method: method))
        try checkChat(data, response)
    }

    /// Like `check`, but says what the server said: these are answered in
    /// front of the user, and "fill in the date" beats "HTTP 400".
    func checkChat(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            if let message = Self.errorMessage(data) { throw ChatError.server(message) }
            throw HTTPFailure(status: http.statusCode)
        }
    }

    static func errorMessage(_ data: Data) -> String? {
        struct Reply: Decodable { let error: String }
        return (try? JSONDecoder().decode(Reply.self, from: data))?.error
    }

    /// Ids go into the path, so only plain ones are allowed.
    static func pathID(_ id: String) throws -> String {
        guard !id.isEmpty, id.count <= 64, id.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") })
        else { throw CaptureError.invalidID }
        return id
    }
}

public enum ChatError: LocalizedError, Equatable {
    case server(String)
    public var errorDescription: String? {
        switch self { case let .server(message): return message }
    }
}
