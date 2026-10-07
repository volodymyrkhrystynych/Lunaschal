import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Learning (the desktop's /api/learning routes)

/// What the Learning screen asks of the server. `JournalAPI` is the real one;
/// debug builds can stand in an in-memory one to show the screen with no server.
public protocol LearningTransport: AnyObject {
    func dueCards(_ filter: LearningFilter) async throws -> [LearningCard]
    func learningAttempts(_ filter: LearningFilter) async throws -> [LearningAttempt]
    func learningStats(_ filter: LearningFilter) async throws -> LearningStats
    func learningTags() async throws -> [LearningTag]
    func learningFolders() async throws -> [LearningFolder]
    func saveAttempt(_ answer: LearningSession.Answer) async throws
    func rate(_ answer: LearningSession.Answer, _ rating: LearningRating, grade: LearningGrade?) async throws
    func learningQueue() async throws -> [LearningCard]
    func approveQueued(_ id: String, force: Bool) async throws -> LearningApproval
    func regenerateCard(_ id: String, direction: String) async throws -> [DraftCard]
    func discardCard(_ id: String) async throws
    func learningCards(_ filter: LearningFilter, limit: Int) async throws -> [LearningCard]
    func setCardTags(_ id: String, _ tags: [String]) async throws
    func reviseCard(_ id: String, question: String, answer: String) async throws -> String
    func deleteCard(_ id: String) async throws
    func transcribe(audio: URL) async throws -> String
    /// Speech mode's read-aloud: the server's text-to-speech, as audio data.
    func speak(_ text: String) async throws -> Data
}

extension JournalAPI: LearningTransport {}

extension JournalAPI {
    /// At most ten due cards, those already answered in this sitting first.
    public func dueCards(_ filter: LearningFilter = LearningFilter()) async throws -> [LearningCard] {
        try JSONDecoder().decode([LearningCard].self, from: await get("api/learning/due", filter.query))
    }

    /// Answers given but not yet rated, oldest first, with any grades so far.
    public func learningAttempts(_ filter: LearningFilter = LearningFilter()) async throws -> [LearningAttempt] {
        try JSONDecoder().decode([LearningAttempt].self, from: await get("api/learning/attempts", filter.query))
    }

    public func learningStats(_ filter: LearningFilter = LearningFilter()) async throws -> LearningStats {
        try JSONDecoder().decode(LearningStats.self, from: await get("api/learning/stats", filter.query))
    }

    public func learningTags() async throws -> [LearningTag] {
        try JSONDecoder().decode([LearningTag].self, from: await get("api/learning/tags", [:]))
    }

    public func learningFolders() async throws -> [LearningFolder] {
        try JSONDecoder().decode([LearningFolder].self, from: await get("api/learning/folders", [:]))
    }

    /// Saves an answer (or a flip) as it's made; the server grades it in the
    /// background. The id makes a resend a no-op.
    public func saveAttempt(_ answer: LearningSession.Answer) async throws {
        struct Body: Encodable {
            let id: String; let cardId: String; let mode: String; let answer: String?; let answerMode: String?
            let speechMode: Bool
        }
        _ = try await chatJSON("api/learning/attempts", Body(
            id: try Self.pathID(answer.id), cardId: answer.card.id, mode: answer.skipped ? "skipped" : "answered",
            answer: answer.text, answerMode: answer.skipped ? nil : answer.answerMode,
            speechMode: !answer.skipped && answer.speech))
    }

    /// Rates an answer, which schedules the card's next review and closes the
    /// attempt. The attempt's id is the review id, so a resend doesn't
    /// advance the schedule twice.
    public func rate(_ answer: LearningSession.Answer, _ rating: LearningRating, grade: LearningGrade?) async throws {
        struct Body: Encodable {
            let reviewId: String; let rating: Int; let suggestedRating: Int?; let userAnswer: String?
            let coverage: ClaimCoverage?; let answerMode: String
        }
        var suggested: Int?, userAnswer = answer.text, coverage: ClaimCoverage?
        if case let .done(graded, suggestion, gradedAs) = grade {
            suggested = suggestion.rawValue; userAnswer = gradedAs; coverage = graded
        }
        _ = try await chatJSON("api/learning/cards/\(try Self.pathID(answer.card.id))/review", Body(
            reviewId: try Self.pathID(answer.id), rating: rating.rawValue, suggestedRating: suggested,
            userAnswer: userAnswer, coverage: coverage, answerMode: answer.answerMode))
    }

    // MARK: Approval queue

    public func learningQueue() async throws -> [LearningCard] {
        try JSONDecoder().decode([LearningCard].self, from: await get("api/learning/queue", [:]))
    }

    public func approveQueued(_ id: String, force: Bool = false) async throws -> LearningApproval {
        struct Reply: Decodable {
            let status: String; let score: Double?; let similar: Similar?
            struct Similar: Decodable { let id: String; let question: String; let answer: String }
        }
        let reply = try JSONDecoder().decode(Reply.self, from: await chatJSON(
            "api/learning/queue/\(try Self.pathID(id))/approve", ["force": force]))
        if reply.status == "duplicateHint", let similar = reply.similar {
            return .duplicate(similarID: similar.id, question: similar.question, answer: similar.answer,
                              score: reply.score ?? 0)
        }
        return .approved
    }

    // regenerateCard and discardCard (ChatAPI.swift) serve the queue too.

    // MARK: Browsing

    /// The live deck, newest first.
    public func learningCards(_ filter: LearningFilter = LearningFilter(), limit: Int = 200) async throws -> [LearningCard] {
        try JSONDecoder().decode([LearningCard].self, from: await get(
            "api/learning/cards", filter.query.merging(["limit": String(limit)]) { first, _ in first }))
    }

    public func setCardTags(_ id: String, _ tags: [String]) async throws {
        try await learningCall("api/learning/cards/\(try Self.pathID(id))", method: "PATCH", ["tags": tags])
    }

    /// An active card's wording changes through a revision: the old card is
    /// retired and a new one replaces it. Its schedule is reset only when the
    /// server judges the meaning changed. Returns the new card's id.
    public func reviseCard(_ id: String, question: String, answer: String) async throws -> String {
        struct Reply: Decodable { let newCardId: String }
        return try JSONDecoder().decode(Reply.self, from: await chatJSON(
            "api/learning/cards/\(try Self.pathID(id))/revise",
            ["question": question, "answer": answer, "triggerType": "manual_edit"])).newCardId
    }

    public func deleteCard(_ id: String) async throws {
        try await send("api/learning/cards/\(try Self.pathID(id))", method: "DELETE")
    }

    // MARK: Dictation

    /// Speech to text on the server, as the desktop's mic button does it. A
    /// clip with no speech in it comes back as the server's own words.
    public func transcribe(audio: URL) async throws -> String {
        let body = try TranscribeMultipart(audio: audio)
        defer { try? FileManager.default.removeItem(at: body.url) }
        var req = request("api/transcribe", method: "POST")
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body.url)
        try checkChat(data, response)
        struct Reply: Decodable { let text: String? }
        return try JSONDecoder().decode(Reply.self, from: data).text?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    public func speak(_ text: String) async throws -> Data {
        var req = request("api/tts", method: "POST")
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.setValue("audio/wav", forHTTPHeaderField: "Accept")
        req.httpBody = Self.ttsForm(text)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
        return data
    }

    /// `/api/tts` reads `text` from the form; a URL-encoded one is enough.
    static func ttsForm(_ text: String) -> Data {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return Data("text=\(text.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")".utf8)
    }

    private func learningCall<Body: Encodable>(_ path: String, method: String, _ body: Body) async throws {
        var req = request(path, method: method)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }
}

/// `/api/transcribe`'s form: the clip as `audio` and nothing else, so the
/// server doesn't log it as a dictated transcription.
public struct TranscribeMultipart {
    public let url: URL
    public let boundary: String

    public init(audio: URL) throws {
        guard (try audio.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else { throw CaptureError.missingAudio }
        (url, boundary) = try writeMultipart(fields: [], files: [
            MultipartFile(field: "audio", filename: "answer.m4a", contentType: "audio/mp4", source: audio)
        ])
    }
}
