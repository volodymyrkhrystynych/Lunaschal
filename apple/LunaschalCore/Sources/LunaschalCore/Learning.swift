import Foundation

// The Learning tab on the phone: the desktop's spaced-repetition review,
// approval queue and card browser (src/components/Learning/), over the same
// /api/learning routes. Server-only: grading is the server's model, and a
// rating advances FSRS there.

public struct LearningCard: Codable, Equatable, Identifiable {
    public let id: String
    public var folderId: String?
    public var question: String
    public var answer: String
    /// pending (in the approval queue), active, or retired.
    public var state: String
    public var tags: [String]
    public var sourceType: String?
    public var derivedFrom: String?
    public var revisedFrom: String?
    /// ISO time it next comes due; nil until approved.
    public var due: String?

    public init(id: String, folderId: String? = nil, question: String, answer: String, state: String = "active",
                tags: [String] = [], sourceType: String? = nil, derivedFrom: String? = nil, revisedFrom: String? = nil,
                due: String? = nil) {
        self.id = id; self.folderId = folderId; self.question = question; self.answer = answer; self.state = state
        self.tags = tags; self.sourceType = sourceType; self.derivedFrom = derivedFrom; self.revisedFrom = revisedFrom
        self.due = due
    }

    public var dueDate: Date? { due.flatMap(TodoRules.date(iso:)) }
    public func isDue(at now: Date = Date()) -> Bool { dueDate.map { $0 <= now } ?? false }
}

public struct LearningStats: Codable, Equatable {
    public var total = 0, due = 0, pending = 0, mastered = 0, learning = 0
    public init() {}
}

public struct LearningTag: Codable, Equatable, Identifiable {
    public let name: String
    public let count: Int
    public var id: String { name }
    public init(name: String, count: Int) { self.name = name; self.count = count }
}

public struct LearningFolder: Codable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public var dueCount: Int
    public init(id: String, name: String, dueCount: Int = 0) { self.id = id; self.name = name; self.dueCount = dueCount }
}

/// Narrows review, stats and browsing to one folder and/or one tag.
public struct LearningFilter: Equatable, Hashable {
    public var folderId: String?
    public var tag: String?
    public init(folderId: String? = nil, tag: String? = nil) { self.folderId = folderId; self.tag = tag }

    public var query: [String: String] {
        var query: [String: String] = [:]
        if let folderId { query["folderId"] = folderId }
        if let tag { query["tag"] = tag }
        return query
    }
}

public struct CoverageClaim: Codable, Equatable {
    public let text: String
    public let essential: Bool
    public let covered: Bool
    public let note: String

    enum CodingKeys: String, CodingKey { case text, essential, covered, note }

    public init(text: String, essential: Bool = true, covered: Bool, note: String = "") {
        self.text = text; self.essential = essential; self.covered = covered; self.note = note
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = try c.decode(String.self, forKey: .text)
        essential = try c.decodeIfPresent(Bool.self, forKey: .essential) ?? true
        covered = try c.decodeIfPresent(Bool.self, forKey: .covered) ?? false
        note = try c.decodeIfPresent(String.self, forKey: .note) ?? ""
    }
}

/// The grader's claim-by-claim verdict on a typed answer.
public struct ClaimCoverage: Codable, Equatable {
    public let claims: [CoverageClaim]
    public let summary: String
    /// The answer looked nothing like the stored one, so no claim check ran.
    public let gated: Bool
    /// A sentence or two on what was missed, written to be read aloud. Only
    /// there when the answer was given in speech mode.
    public let speechSummary: String?

    enum CodingKeys: String, CodingKey { case claims, summary, gated, speechSummary }

    public init(claims: [CoverageClaim], summary: String = "", gated: Bool = false, speechSummary: String? = nil) {
        self.claims = claims; self.summary = summary; self.gated = gated; self.speechSummary = speechSummary
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        claims = try c.decodeIfPresent([CoverageClaim].self, forKey: .claims) ?? []
        summary = try c.decodeIfPresent(String.self, forKey: .summary) ?? ""
        gated = try c.decodeIfPresent(Bool.self, forKey: .gated) ?? false
        speechSummary = try c.decodeIfPresent(String.self, forKey: .speechSummary)
            .flatMap { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : $0 }
    }
}

/// A card answered (or flipped past) but not yet rated, as the server keeps
/// it: what makes a session resumable, and where the grade lands.
public struct LearningAttempt: Codable, Equatable, Identifiable {
    public let id: String
    public let cardId: String
    /// answered or skipped.
    public let mode: String
    public let answer: String?
    public let answerMode: String?
    /// pending, done, error or skipped.
    public let gradeStatus: String
    public let coverage: ClaimCoverage?
    public let suggestedRating: Int?
    public let normalizedAnswer: String?

    public init(id: String, cardId: String, mode: String, answer: String? = nil, answerMode: String? = nil,
                gradeStatus: String = "pending", coverage: ClaimCoverage? = nil, suggestedRating: Int? = nil,
                normalizedAnswer: String? = nil) {
        self.id = id; self.cardId = cardId; self.mode = mode; self.answer = answer; self.answerMode = answerMode
        self.gradeStatus = gradeStatus; self.coverage = coverage; self.suggestedRating = suggestedRating
        self.normalizedAnswer = normalizedAnswer
    }
}

public enum LearningRating: Int, CaseIterable, Identifiable {
    case again = 1, hard, good, easy
    public var id: Int { rawValue }
    public var label: String {
        switch self { case .again: "Again"; case .hard: "Hard"; case .good: "Good"; case .easy: "Easy" }
    }
}

/// Where an answered card's grade has got to.
public enum LearningGrade: Equatable {
    case pending
    case error
    case done(ClaimCoverage, suggested: LearningRating, gradedAs: String)
}

/// One review sitting, as the desktop's ReviewSession runs it: a deck of due
/// cards, an answering pass where each is answered or flipped past, then a
/// results pass where each is shown with its grade and rated. Every answer is
/// saved on the server as it's made, so the deck is rebuilt from `/due` and
/// `/attempts` on return rather than kept here.
public struct LearningSession: Equatable {
    public struct Answer: Equatable, Identifiable {
        /// Also the review's idempotency key when it is finally rated.
        public let id: String
        public let card: LearningCard
        public let skipped: Bool
        public let text: String?
        /// Spoken through the mic: the server tidies the transcript before
        /// grading it, and says what it graded.
        public var voice = false
        /// Speech mode was on when it was answered: the grade then carries a
        /// summary to read aloud. Fixed at answer time, as on the desktop, so
        /// turning it on mid-session only affects answers given after.
        public var speech = false

        /// What the routes call how it was answered.
        public var answerMode: String { skipped ? "self" : voice ? "voice" : "typed" }
    }

    public private(set) var cards: [LearningCard]
    public private(set) var answers: [Answer]
    public private(set) var resultIndex = 0

    /// `/due` sorts cards with an open attempt first, so the answered ones are
    /// always at the front of the deck and the next card is at their count.
    public init(due: [LearningCard], attempts: [LearningAttempt]) {
        cards = due
        let byID = Dictionary(due.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        answers = attempts.compactMap { attempt in
            byID[attempt.cardId].map {
                Answer(id: attempt.id, card: $0, skipped: attempt.mode == "skipped", text: attempt.answer,
                       voice: attempt.answerMode == "voice")
            }
        }
    }

    public var total: Int { cards.count }
    public var isEmpty: Bool { cards.isEmpty }
    public var inResults: Bool { !cards.isEmpty && answers.count >= cards.count }

    /// The card waiting for an answer, during the answering pass.
    public var card: LearningCard? {
        inResults ? nil : cards.first { card in !answers.contains { $0.card.id == card.id } }
    }

    /// The answer being rated, during the results pass.
    public var result: Answer? { inResults && answers.indices.contains(resultIndex) ? answers[resultIndex] : nil }

    /// 1-based, for "Card 3 of 10".
    public var position: Int { inResults ? resultIndex + 1 : min(answers.count + 1, total) }

    /// Records the current card as answered and returns what to save. `voice`
    /// when any of it was dictated through the mic, as the desktop marks it.
    public mutating func answer(_ text: String, voice: Bool = false, speech: Bool = false,
                                id: String = ULID.make()) -> Answer? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let card, !trimmed.isEmpty else { return nil }
        let answer = Answer(id: id, card: card, skipped: false, text: trimmed, voice: voice, speech: speech)
        answers.append(answer)
        return answer
    }

    /// Flips past the current card; its answer is shown in the results pass.
    public mutating func skip(id: String = ULID.make()) -> Answer? {
        guard let card else { return nil }
        let answer = Answer(id: id, card: card, skipped: true, text: nil)
        answers.append(answer)
        return answer
    }

    /// Moves to the next result once one is rated; true when the pass is over.
    public mutating func advance() -> Bool {
        resultIndex += 1
        return resultIndex >= answers.count
    }

    /// The grade for an answer, read from the server's attempt rows. One the
    /// server hasn't listed yet reads as pending, which is what it is.
    public static func grade(of answer: Answer, in attempts: [LearningAttempt]) -> LearningGrade? {
        guard !answer.skipped else { return nil }
        guard let row = attempts.first(where: { $0.cardId == answer.card.id }) else { return .pending }
        switch row.gradeStatus {
        case "error": return .error
        case "done":
            guard let coverage = row.coverage else { return .pending }
            return .done(coverage, suggested: LearningRating(rawValue: row.suggestedRating ?? 3) ?? .good,
                         gradedAs: row.normalizedAnswer ?? row.answer ?? "")
        default: return .pending
        }
    }

    /// Whether any answer is still waiting on the grader, so worth polling.
    public static func gradesOutstanding(_ attempts: [LearningAttempt]) -> Bool {
        attempts.contains { $0.gradeStatus == "pending" }
    }
}

/// What approving a queued card can come back with.
public enum LearningApproval: Equatable {
    case approved
    /// An active card already says much the same; approving again with
    /// `force` keeps both.
    case duplicate(similarID: String, question: String, answer: String, score: Double)
}

public enum LearningTags {
    /// `parseTagsInput` plus `backend/tags.py`: comma-separated, trimmed,
    /// lower-cased, no repeats.
    public static func parse(_ input: String) -> [String] {
        var seen = Set<String>()
        return input.split(separator: ",").compactMap { part in
            let tag = part.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            guard !tag.isEmpty, seen.insert(tag).inserted else { return nil }
            return tag
        }
    }
}
