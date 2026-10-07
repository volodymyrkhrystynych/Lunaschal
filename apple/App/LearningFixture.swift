#if DEBUG
import Foundation
import LunaschalCore

/// Launched with `-learningFixture`, More → Learning talks to an in-memory
/// stand-in for `/api/learning` instead of the server, so the screen can be
/// seen and UI-tested with no server at all. It starts with folders, tags,
/// due and scheduled cards, and a queue that includes a near-duplicate.
/// Grading is a word match, ready about a second after the answer, so the
/// results pass shows "Checking…" and then the claims, as the real one does.
/// Nothing is saved: a relaunch starts over. Debug builds only.
enum LearningFixture {
    static let argument = "-learningFixture"

    static let server: LearningDemoServer? =
        ProcessInfo.processInfo.arguments.contains(argument) ? LearningDemoServer() : nil
}

actor LearningDemoServer: LearningTransport {
    private struct Open {
        var attempt: LearningAttempt
        var gradeAt: Date
        var speech = false
    }

    private var folders: [LearningFolder] = [
        LearningFolder(id: "fixture-swift", name: "Swift"),
        LearningFolder(id: "fixture-bio", name: "Biology"),
    ]
    private var cards: [LearningCard]
    private var open: [String: Open] = [:]

    init(now: Date = Date()) {
        let iso = ISO8601DateFormatter()
        func card(_ question: String, _ answer: String, folder: String?, tags: [String], state: String = "active",
                  dueIn days: Double = -1, source: String? = "manual") -> LearningCard {
            LearningCard(id: ULID.make(), folderId: folder, question: question, answer: answer, state: state, tags: tags,
                         sourceType: source, due: state == "pending" ? nil : iso.string(from: now.addingTimeInterval(days * 86_400)))
        }
        cards = [
            card("What does `@MainActor` guarantee?",
                 "Code runs on the main thread. The compiler checks calls from other actors are awaited.",
                 folder: "fixture-swift", tags: ["concurrency"]),
            card("What is the difference between a struct and a class in Swift?",
                 "Structs are value types and are copied. Classes are reference types and are shared; only classes support inheritance.",
                 folder: "fixture-swift", tags: ["basics"]),
            card("What does `defer` do?",
                 "It runs a block when the current scope exits, however it exits.",
                 folder: "fixture-swift", tags: ["basics"]),
            card("What do mitochondria do?",
                 "They make ATP through cellular respiration. They have their own DNA.",
                 folder: "fixture-bio", tags: ["cells"]),
            card("What is the function of ribosomes?",
                 "They translate messenger RNA into proteins.",
                 folder: "fixture-bio", tags: ["cells"]),
            card("What does FSRS stand for?",
                 "Free Spaced Repetition Scheduler.",
                 folder: nil, tags: ["memory"]),
            card("What is an actor in Swift?",
                 "A reference type that protects its state by running one task at a time.",
                 folder: "fixture-swift", tags: ["concurrency"], dueIn: 3),
            card("What is osmosis?",
                 "Water moving across a membrane from low to high solute concentration.",
                 folder: "fixture-bio", tags: ["cells"], dueIn: 9),
            card("What is the forgetting curve?",
                 "Memory of something decays over time unless it is reviewed.",
                 folder: nil, tags: ["memory"], dueIn: 25),
            card("What does `async let` do?",
                 "It starts a child task at once and awaits its result later.",
                 folder: "fixture-swift", tags: ["concurrency"], state: "pending", source: "chat"),
            card("Where is chlorophyll found?",
                 "In the chloroplasts of plant cells.",
                 folder: "fixture-bio", tags: ["cells"], state: "pending", source: "journal"),
            // Close enough to the ribosome card to bring up the duplicate prompt.
            card("What do ribosomes do?",
                 "They translate mRNA into proteins.",
                 folder: "fixture-bio", tags: ["cells"], state: "pending", source: "brain-dump"),
        ]
    }

    private func matches(_ card: LearningCard, _ filter: LearningFilter) -> Bool {
        (filter.folderId.map { card.folderId == $0 } ?? true) && (filter.tag.map { card.tags.contains($0) } ?? true)
    }

    private func isDue(_ card: LearningCard) -> Bool { card.state == "active" && card.isDue() }

    // MARK: Review

    func dueCards(_ filter: LearningFilter) -> [LearningCard] {
        let due = cards.filter { isDue($0) && matches($0, filter) }
        // Answered cards first, as `/due` sorts them.
        return Array((due.filter { open[$0.id] != nil } + due.filter { open[$0.id] == nil }).prefix(10))
    }

    func learningAttempts(_ filter: LearningFilter) -> [LearningAttempt] {
        let now = Date()
        for (id, item) in open where item.attempt.gradeStatus == "pending" && item.gradeAt <= now {
            open[id]?.attempt = grade(item.attempt, speech: item.speech)
        }
        return open.values.map(\.attempt)
            .filter { attempt in cards.contains { $0.id == attempt.cardId && matches($0, filter) } }
            .sorted { $0.id < $1.id }
    }

    func learningStats(_ filter: LearningFilter) -> LearningStats {
        let shown = cards.filter { $0.state != "retired" && matches($0, filter) }
        var stats = LearningStats()
        stats.total = shown.filter { $0.state == "active" }.count
        stats.due = shown.filter(isDue).count
        stats.pending = shown.filter { $0.state == "pending" }.count
        // The fixture has no FSRS state; call a card due three weeks out mastered.
        stats.mastered = shown.filter { $0.state == "active" && ($0.dueDate ?? .distantPast) > Date().addingTimeInterval(21 * 86_400) }.count
        stats.learning = stats.total - stats.mastered
        return stats
    }

    func learningTags() -> [LearningTag] {
        var counts: [String: Int] = [:]
        for card in cards where card.state != "retired" { for tag in card.tags { counts[tag, default: 0] += 1 } }
        return counts.map { LearningTag(name: $0.key, count: $0.value) }
            .sorted { ($0.count, $1.name) > ($1.count, $0.name) }
    }

    func learningFolders() -> [LearningFolder] {
        folders.map { folder in
            var folder = folder
            folder.dueCount = cards.filter { $0.folderId == folder.id && isDue($0) }.count
            return folder
        }
    }

    func saveAttempt(_ answer: LearningSession.Answer) {
        open[answer.card.id] = Open(
            attempt: LearningAttempt(id: answer.id, cardId: answer.card.id, mode: answer.skipped ? "skipped" : "answered",
                                     answer: answer.text, answerMode: answer.answerMode,
                                     gradeStatus: answer.skipped ? "skipped" : "pending"),
            gradeAt: Date().addingTimeInterval(1.2), speech: answer.speech)
    }

    /// A claim per sentence of the card's answer, covered when most of its
    /// longer words turn up in the answer given.
    private func grade(_ attempt: LearningAttempt, speech: Bool) -> LearningAttempt {
        guard let card = cards.first(where: { $0.id == attempt.cardId }) else { return attempt }
        func words(_ text: String) -> Set<String> {
            Set(text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init).filter { $0.count > 3 })
        }
        let given = words(attempt.answer ?? "")
        let claims = card.answer.split(separator: ".").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            .enumerated().map { index, claim in
                let needed = words(claim)
                let covered = !needed.isEmpty && Double(needed.intersection(given).count) / Double(needed.count) >= 0.5
                return CoverageClaim(text: claim, essential: index == 0, covered: covered,
                                     note: covered ? "" : "not mentioned")
            }
        let share = claims.isEmpty ? 0 : Double(claims.filter(\.covered).count) / Double(claims.count)
        let rating = share == 1 ? 4 : share >= 0.5 ? 3 : share > 0 ? 2 : 1
        let summary = share == 1 ? "Everything's there." : share > 0 ? "Partly right." : "This misses the answer."
        return LearningAttempt(id: attempt.id, cardId: attempt.cardId, mode: attempt.mode, answer: attempt.answer,
                               answerMode: attempt.answerMode, gradeStatus: "done",
                               coverage: ClaimCoverage(claims: claims, summary: summary, speechSummary: speech
                                   ? (claims.first { !$0.covered }.map { "You missed that \($0.text.lowercased())." } ?? "You got it all.")
                                   : nil),
                               suggestedRating: rating,
                               normalizedAnswer: attempt.answer)
    }

    func rate(_ answer: LearningSession.Answer, _ rating: LearningRating, grade: LearningGrade?) {
        open[answer.card.id] = nil
        guard let index = cards.firstIndex(where: { $0.id == answer.card.id }) else { return }
        let days: Double = [.again: 0.007, .hard: 1, .good: 3, .easy: 7][rating] ?? 1
        cards[index].due = ISO8601DateFormatter().string(from: Date().addingTimeInterval(days * 86_400))
    }

    // MARK: Queue

    func learningQueue() -> [LearningCard] { cards.filter { $0.state == "pending" } }

    func approveQueued(_ id: String, force: Bool) -> LearningApproval {
        guard let index = cards.firstIndex(where: { $0.id == id }) else { return .approved }
        let card = cards[index]
        if !force, let similar = cards.first(where: {
            $0.state == "active" && $0.question.contains("ribosomes") && card.question.contains("ribosomes")
        }) {
            return .duplicate(similarID: similar.id, question: similar.question, answer: similar.answer, score: 0.93)
        }
        cards[index].state = "active"
        cards[index].due = ISO8601DateFormatter().string(from: Date())
        return .approved
    }

    func regenerateCard(_ id: String, direction: String) -> [DraftCard] {
        guard let index = cards.firstIndex(where: { $0.id == id }) else { return [] }
        let new = ULID.make()
        cards[index] = LearningCard(id: new, folderId: cards[index].folderId,
                                    question: "\(cards[index].question) (\(direction))",
                                    answer: cards[index].answer, state: "pending", tags: cards[index].tags,
                                    sourceType: cards[index].sourceType)
        return [DraftCard(id: new, question: cards[index].question, answer: cards[index].answer)]
    }

    func discardCard(_ id: String) { cards.removeAll { $0.id == id } }

    // MARK: Browse

    func learningCards(_ filter: LearningFilter, limit: Int) -> [LearningCard] {
        Array(cards.filter { $0.state == "active" && matches($0, filter) }.prefix(limit))
    }

    func setCardTags(_ id: String, _ tags: [String]) {
        guard let index = cards.firstIndex(where: { $0.id == id }) else { return }
        cards[index].tags = tags
    }

    func reviseCard(_ id: String, question: String, answer: String) -> String {
        guard let index = cards.firstIndex(where: { $0.id == id }) else { return id }
        var revised = cards[index]
        cards[index].state = "retired"
        revised = LearningCard(id: ULID.make(), folderId: revised.folderId, question: question, answer: answer,
                               tags: revised.tags, sourceType: revised.sourceType, revisedFrom: id, due: revised.due)
        cards.insert(revised, at: 0)
        return revised.id
    }

    func deleteCard(_ id: String) {
        cards.removeAll { $0.id == id }
        open[id] = nil
    }

    /// No voice here: a short tone stands in for the read-aloud.
    func speak(_ text: String) async throws -> Data {
        let rate = 22_050, count = rate * 2 / 5
        var pcm = Data(capacity: count * 2)
        for n in 0..<count {
            let fade = min(1, Double(count - n) / 2_000)
            let sample = Int16(sin(Double(n) * 2 * .pi * 660 / Double(rate)) * 8_000 * fade)
            withUnsafeBytes(of: sample.littleEndian) { pcm.append(contentsOf: $0) }
        }
        func le<T: FixedWidthInteger>(_ value: T) -> Data { withUnsafeBytes(of: value.littleEndian) { Data($0) } }
        var wav = Data("RIFF".utf8) + le(UInt32(36 + pcm.count)) + Data("WAVEfmt ".utf8)
        wav += le(UInt32(16)) + le(UInt16(1)) + le(UInt16(1)) + le(UInt32(rate)) + le(UInt32(rate * 2))
        wav += le(UInt16(2)) + le(UInt16(16)) + Data("data".utf8) + le(UInt32(pcm.count)) + pcm
        return wav
    }

    func transcribe(audio: URL) async throws -> String {
        try await Task.sleep(for: .milliseconds(600))
        return "a spoken answer from the fixture"
    }
}
#endif
