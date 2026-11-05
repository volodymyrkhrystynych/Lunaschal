import AVFoundation
import Foundation
import LunaschalCore
import SwiftUI
import UIKit

/// The Learning screen: the desktop's review session, approval queue and card
/// browser. Unlike Todo there is no outbox. Grading needs the server's model
/// and a rating advances the server's schedule, so every action waits for it.
/// The server keeps each answer as it's given, though, so a session that is
/// left (or loses the connection) carries on where it stopped.
@MainActor
final class LearningModel: ObservableObject {
    @Published var filter = LearningFilter() {
        didSet { if filter != oldValue { Task { await reload() } } }
    }
    @Published private(set) var stats = LearningStats()
    @Published private(set) var tags: [LearningTag] = []
    @Published private(set) var folders: [LearningFolder] = []
    @Published private(set) var queue: [LearningCard] = []
    @Published private(set) var cards: [LearningCard] = []

    @Published private(set) var session: LearningSession?
    /// The server's open attempts, which carry the grades as they land.
    @Published private(set) var attempts: [LearningAttempt] = []
    /// A save or rating on its way, so the buttons don't send it twice.
    @Published private(set) var sending = false
    /// Spoken answers whose clip is still on its way to the server, by answer
    /// id. The session has already moved past them; they can't be rated until
    /// the server has the attempt, or the clip would land after the rating.
    @Published private(set) var uploading: [String: LearningSession.Answer] = [:]
    /// Why nothing could be loaded: no server, signed out, or unreachable.
    @Published private(set) var problem: String?
    /// What went wrong with the last thing tapped.
    @Published var error: String?

    /// More → Settings → Learning's switch, read when each answer is given.
    static let speechModeKey = "learningSpeechMode"
    /// Fetching a read-aloud from the server.
    @Published private(set) var speaking = false

    let capture: CaptureModel
    private var polling: Task<Void, Never>?
    private var player: AVAudioPlayer?
    /// Answers already read aloud, so a summary plays once on its own.
    private var spoken = Set<String>()
    /// Review card pages fetched so far, by attachment id.
    private var pages: [String: UIImage] = [:]

    init(capture: CaptureModel) { self.capture = capture }

    /// The server, or with `-learningFixture` in a debug build, the in-memory
    /// stand-in that lets the screen be seen (and UI-tested) without one.
    func transport() -> LearningTransport? {
        #if DEBUG
        if let fixture = LearningFixture.server { return fixture }
        #endif
        return capture.chatAPI()
    }

    private func api() -> LearningTransport? {
        guard let api = transport() else {
            problem = capture.server == nil
                ? "Not connected to a server. Learning runs on your server; connect in Settings."
                : "Signed out. Sign in again in Settings to review cards."
            return nil
        }
        return api
    }

    private func failed(_ error: Error) {
        if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
        self.error = error.localizedDescription
    }

    private func unreachable(_ error: Error) {
        if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
        problem = "Can't reach your server. Learning needs it to check answers and schedule reviews."
    }

    // MARK: Loading

    /// The counts, filters and queue: cheap, and what the More row's badge reads.
    func refresh() async {
        guard let api = api() else { return }
        do {
            async let fetchedStats = api.learningStats(filter)
            async let fetchedTags = api.learningTags()
            async let fetchedFolders = api.learningFolders()
            async let fetchedQueue = api.learningQueue()
            (stats, tags, folders, queue) = try await (fetchedStats, fetchedTags, fetchedFolders, fetchedQueue)
            problem = nil
            // A filter whose folder or tag has gone would show nothing.
            if let tag = filter.tag, !tags.contains(where: { $0.name == tag }) { filter.tag = nil }
            if let folder = filter.folderId, !folders.contains(where: { $0.id == folder }) { filter.folderId = nil }
        } catch { unreachable(error) }
    }

    /// Everything the screen shows, for the current filter.
    func reload() async {
        await refresh()
        await loadSession()
        await loadCards()
    }

    /// Rebuilds the deck from the server: due cards, and the answers already
    /// given to them, so a session picks up where it was left.
    func loadSession() async {
        guard let api = api() else { return }
        do {
            async let due = api.dueCards(filter)
            async let open = api.learningAttempts(filter)
            let (cards, attempts) = try await (due, open)
            self.attempts = attempts
            var rebuilt = LearningSession(due: cards, attempts: attempts)
            // A recording still uploading isn't on the server's list yet.
            for answer in uploading.values.sorted(by: { $0.id < $1.id }) { rebuilt.adopt(answer) }
            session = rebuilt
            problem = nil
            pollGrades()
        } catch { unreachable(error) }
    }

    func loadCards() async {
        guard let api = api() else { return }
        do { cards = try await api.learningCards(filter, limit: 200) } catch { unreachable(error) }
    }

    /// Grades land on the attempt rows a few seconds after each answer; ask
    /// again every two seconds until none are outstanding.
    private func pollGrades() {
        polling?.cancel()
        guard LearningSession.gradesOutstanding(attempts) else { return }
        polling = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
                guard let self, let api = self.transport() else { return }
                guard let fresh = try? await api.learningAttempts(self.filter) else { continue }
                self.attempts = fresh
                if !LearningSession.gradesOutstanding(fresh) { return }
            }
        }
    }

    func stopPolling() { polling?.cancel() }

    // MARK: Review

    func grade(of answer: LearningSession.Answer) -> LearningGrade? {
        LearningSession.grade(of: answer, in: attempts)
    }

    /// Saves the answer for grading and moves to the next card. Returns false
    /// (and keeps the text) when it couldn't be saved.
    func submit(_ text: String, voice: Bool = false) async -> Bool {
        guard var next = session else { return false }
        let speech = UserDefaults.standard.bool(forKey: Self.speechModeKey)
        guard let answer = next.answer(text, voice: voice, speech: speech) else { return false }
        return await save(answer, into: next)
    }

    /// A recording stopped: the clip is the answer. The next card is up at
    /// once; the clip goes to the server behind it, which transcribes and
    /// grades it. `typed` is anything typed before the mic was tapped.
    func submitRecording(_ clip: URL, typed: String) {
        let speech = UserDefaults.standard.bool(forKey: Self.speechModeKey)
        guard var next = session, let answer = next.answerSpoken(typed: typed, speech: speech) else {
            try? FileManager.default.removeItem(at: clip)
            return
        }
        session = next
        uploading[answer.id] = answer
        Task { await upload(answer, clip) }
    }

    /// Tries a few times, since the phone may be between networks; the attempt
    /// id makes a resend a no-op. A clip that can't be sent is dropped and its
    /// card asked again, rather than shown later with nothing to grade.
    private func upload(_ answer: LearningSession.Answer, _ clip: URL) async {
        defer {
            try? FileManager.default.removeItem(at: clip)
            uploading[answer.id] = nil
        }
        var lastError: Error?
        for retry in 0..<3 {
            if retry > 0 { try? await Task.sleep(for: .seconds(2 * retry)) }
            guard let api = transport() else { break }
            do {
                try await api.saveRecordedAttempt(answer, clip: clip)
                attempts.removeAll { $0.cardId == answer.card.id }
                attempts.append(LearningAttempt(id: answer.id, cardId: answer.card.id, mode: "answered",
                                                answer: answer.text, answerMode: answer.answerMode))
                pollGrades()
                return
            } catch { lastError = error }
        }
        // Retired (or the deck rebuilt) meanwhile: nothing is waiting on it.
        guard session?.answers.contains(where: { $0.id == answer.id }) ?? false else { return }
        session?.unanswer(answer.id)
        error = "Your recording couldn't be sent, so that card is up again. "
            + (lastError?.localizedDescription ?? "Not connected to your server.")
    }

    /// The answer's words: a spoken one's arrive from the server once heard.
    func text(of answer: LearningSession.Answer) -> String? {
        LearningSession.text(of: answer, in: attempts)
    }

    func skip() async {
        guard var next = session, let answer = next.skip() else { return }
        _ = await save(answer, into: next)
    }

    private func save(_ answer: LearningSession.Answer, into next: LearningSession) async -> Bool {
        guard !sending, let api = api() else { return false }
        sending = true
        defer { sending = false }
        do {
            try await api.saveAttempt(answer)
            session = next
            if !answer.skipped {
                attempts.removeAll { $0.cardId == answer.card.id }
                attempts.append(LearningAttempt(id: answer.id, cardId: answer.card.id, mode: "answered",
                                                answer: answer.text, answerMode: answer.answerMode))
                pollGrades()
            }
            return true
        } catch { failed(error); return false }
    }

    /// Rates the answer on screen and moves on; at the end of the results the
    /// deck is fetched again, so whatever has come due since starts a new one.
    func rate(_ rating: LearningRating) async {
        guard !sending, var next = session, let answer = next.result, uploading[answer.id] == nil,
              let api = api() else { return }
        sending = true
        defer { sending = false }
        do {
            try await api.rate(answer, rating, grade: grade(of: answer))
            if next.advance() {
                await loadSession()
                await refresh()
            } else {
                session = next
            }
        } catch { failed(error) }
    }

    /// Rates the review card on screen from its pages and moves on. It was
    /// never answered, so nothing waits on a grade; a deck that was all review
    /// cards is fetched again, as the end of the results pass is.
    func rateReview(_ rating: LearningRating) async {
        guard !sending, var next = session, let card = next.card, card.isReview, let api = api() else { return }
        sending = true
        defer { sending = false }
        do {
            try await api.rateReview(card, rating, reviewID: ULID.make())
            next.markReviewed()
            if next.isFinished {
                await loadSession()
                await refresh()
            } else {
                session = next
            }
        } catch { failed(error) }
    }

    /// A review card's page, kept for the screen's lifetime so scrolling back
    /// up doesn't fetch it again. Nil when it couldn't be fetched.
    func cachedReviewPage(_ page: LearningReviewPages.Page) -> UIImage? { pages[page.id] }

    func reviewPage(_ page: LearningReviewPages.Page) async -> UIImage? {
        if let cached = pages[page.id] { return cached }
        guard let api = transport(), let data = try? await api.reviewPage(page),
              let image = UIImage(data: data) else { return nil }
        pages[page.id] = image
        return image
    }

    /// Takes a card out of review for good, from wherever it is in the
    /// sitting. It keeps its history and stays retired, as a revised card's
    /// old version does.
    func retire(_ card: LearningCard) async {
        guard !sending, let api = api() else { return }
        sending = true
        defer { sending = false }
        do {
            try await api.retireCard(card.id)
            attempts.removeAll { $0.cardId == card.id }
            stopSpeaking()
            if var next = session {
                next.remove(card.id)
                if next.isSpent { await loadSession() } else { session = next }
            }
            await refresh()
        } catch { failed(error) }
    }

    // MARK: Speech mode

    /// Reads an answer's spoken summary aloud the first time it's there:
    /// as the result comes up, or when its grade lands while it's on screen.
    func speakIfNew(_ answer: LearningSession.Answer) {
        guard case let .done(coverage, _, _) = grade(of: answer), let text = coverage.speechSummary,
              spoken.insert(answer.id).inserted else { return }
        Task { await speak(text) }
    }

    /// A failure is silent, as on the desktop: rating never waits on it.
    func speak(_ text: String) async {
        stopSpeaking()
        guard let api = transport() else { return }
        speaking = true
        defer { speaking = false }
        do {
            let audio = try await api.speak(text)
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            let player = try AVAudioPlayer(data: audio)
            guard player.play() else { return }
            self.player = player
        } catch {}
    }

    func stopSpeaking() {
        player?.stop()
        player = nil
    }

    // MARK: Queue

    /// nil once approved; the near-duplicate when the server found one.
    func approve(_ card: LearningCard, force: Bool = false) async -> LearningApproval? {
        guard let api = api() else { return nil }
        do {
            let result = try await api.approveQueued(card.id, force: force)
            if result == .approved { await afterQueueChange() }
            return result
        } catch { failed(error); return nil }
    }

    func deny(_ card: LearningCard) async {
        guard let api = api() else { return }
        do { try await api.discardCard(card.id); await afterQueueChange() } catch { failed(error) }
    }

    /// Approves the new card in place of the old one it duplicates.
    func replace(_ similarID: String, with card: LearningCard) async {
        guard let api = api() else { return }
        do {
            try await api.deleteCard(similarID)
            _ = try await api.approveQueued(card.id, force: true)
            await afterQueueChange()
        } catch { failed(error) }
    }

    func regenerate(_ card: LearningCard, direction: String) async -> Bool {
        let direction = direction.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !direction.isEmpty, let api = api() else { return false }
        do {
            _ = try await api.regenerateCard(card.id, direction: direction)
            await refresh()
            return true
        } catch { failed(error); return false }
    }

    private func afterQueueChange() async {
        await refresh()
        await loadCards()
        // An approved card is due at once, so it may belong in this deck.
        if session?.isEmpty ?? true { await loadSession() }
    }

    // MARK: Browse

    /// Saves the edit form. Tags change in place; new wording becomes a
    /// revision, the server's rule for an active card.
    func save(_ card: LearningCard, question: String, answer: String, tags: String) async -> Bool {
        guard let api = api() else { return false }
        let question = question.trimmingCharacters(in: .whitespacesAndNewlines)
        let answer = answer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !answer.isEmpty else { return false }
        let newTags = LearningTags.parse(tags)
        do {
            if newTags != card.tags { try await api.setCardTags(card.id, newTags) }
            if question != card.question || answer != card.answer {
                _ = try await api.reviseCard(card.id, question: question, answer: answer)
            }
            await loadCards()
            await refresh()
            return true
        } catch { failed(error); return false }
    }

    func delete(_ card: LearningCard) async {
        guard let api = api() else { return }
        do {
            try await api.deleteCard(card.id)
            cards.removeAll { $0.id == card.id }
            await refresh()
        } catch { failed(error) }
    }
}
