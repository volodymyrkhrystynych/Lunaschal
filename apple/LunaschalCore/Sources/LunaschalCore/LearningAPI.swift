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
    /// Rates a review card from its pages: self-rated, with no attempt behind it.
    func rateReview(_ card: LearningCard, _ rating: LearningRating, reviewID: String) async throws
    /// One of a review card's pages, as image data.
    func reviewPage(_ page: LearningReviewPages.Page) async throws -> Data
    func learningQueue() async throws -> [LearningCard]
    func approveQueued(_ id: String, force: Bool) async throws -> LearningApproval
    func regenerateCard(_ id: String, direction: String) async throws -> [DraftCard]
    func discardCard(_ id: String) async throws
    func learningCards(_ filter: LearningFilter, limit: Int) async throws -> [LearningCard]
    func setCardTags(_ id: String, _ tags: [String]) async throws
    func reviseCard(_ id: String, question: String, answer: String) async throws -> String
    func deleteCard(_ id: String) async throws
    /// Takes a card out of review for good; it and its history are kept.
    func retireCard(_ id: String) async throws
    /// A spoken answer: the clip is the answer, transcribed and then graded on
    /// the server, so nothing waits on the words before the next card.
    func saveRecordedAttempt(_ answer: LearningSession.Answer, clip: URL) async throws
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

    /// The review id is minted per rating, so a resend of the same tap
    /// doesn't advance the schedule twice.
    public func rateReview(_ card: LearningCard, _ rating: LearningRating, reviewID: String) async throws {
        struct Body: Encodable { let reviewId: String; let rating: Int; let answerMode: String }
        _ = try await chatJSON("api/learning/cards/\(try Self.pathID(card.id))/review", Body(
            reviewId: try Self.pathID(reviewID), rating: rating.rawValue, answerMode: "self"))
    }

    /// Built from the page's id rather than the url it came with, so nothing
    /// the server sends can point the request anywhere but an attachment.
    public func reviewPage(_ page: LearningReviewPages.Page) async throws -> Data {
        try await get("api/journal/attachments/\(try Self.pathID(page.id))/file", [:])
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

    public func retireCard(_ id: String) async throws {
        _ = try await chatJSON("api/learning/cards/\(try Self.pathID(id))/retire", [String: String]())
    }

    // MARK: Dictation

    /// Uploads a spoken answer as its attempt. The attempt id is the client's,
    /// so a resend after a dropped connection is a no-op on the server.
    public func saveRecordedAttempt(_ answer: LearningSession.Answer, clip: URL) async throws {
        let body = try RecordedAnswerMultipart(answer: answer, audio: clip)
        defer { try? FileManager.default.removeItem(at: body.url) }
        var req = request("api/learning/attempts/recording", method: "POST")
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body.url)
        try checkChat(data, response)
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

/// `/api/learning/attempts/recording`'s form: the attempt's id and card, any
/// words typed before the mic was tapped, speech mode, and the clip as `audio`.
public struct RecordedAnswerMultipart {
    public let url: URL
    public let boundary: String

    public init(answer: LearningSession.Answer, audio: URL) throws {
        guard (try audio.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else { throw CaptureError.missingAudio }
        var fields = [("id", try JournalAPI.pathID(answer.id)), ("cardId", answer.card.id),
                      ("speechMode", answer.speech ? "true" : "false")]
        if let typed = answer.text { fields.append(("answer", typed)) }
        (url, boundary) = try writeMultipart(fields: fields, files: [
            MultipartFile(field: "audio", filename: "answer.m4a", contentType: "audio/mp4", source: audio)
        ])
    }
}
