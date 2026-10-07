import XCTest
@testable import LunaschalCore

final class LearningTests: XCTestCase {
    private func card(_ id: String, due: String? = "2026-10-05T12:00:00+00:00") -> LearningCard {
        LearningCard(id: id, question: "Q \(id)", answer: "A \(id)", due: due)
    }

    func testDecodesTheServersRows() throws {
        // As `_card_to_dict` and `_attempt_to_dict` write them: camelCased,
        // timestamps as isoformat(), private columns already dropped.
        let cards = try JSONDecoder().decode([LearningCard].self, from: Data("""
        [{"id":"c1","folderId":null,"question":"What is FSRS?","answer":"A scheduler","state":"active",
          "tags":["memory"],"sourceType":"manual","sourceId":null,"derivedFrom":null,"revisedFrom":"c0",
          "due":"2026-10-05T12:00:00+00:00","createdAt":"2026-10-01T09:00:00+00:00","updatedAt":"2026-10-01T09:00:00+00:00"}]
        """.utf8))
        XCTAssertEqual(cards.first?.tags, ["memory"])
        XCTAssertEqual(cards.first?.revisedFrom, "c0")
        XCTAssertTrue(cards[0].isDue(at: Date(timeIntervalSince1970: 1_800_000_000)))
        XCTAssertFalse(cards[0].isDue(at: Date(timeIntervalSince1970: 1_700_000_000)))
        XCTAssertFalse(card("x", due: nil).isDue())

        let attempts = try JSONDecoder().decode([LearningAttempt].self, from: Data("""
        [{"id":"a1","cardId":"c1","mode":"answered","answer":"spaced","answerMode":"typed","gradeStatus":"done",
          "coverage":{"claims":[{"text":"It schedules reviews","essential":true,"covered":true,"note":""},
                                {"text":"It models stability","essential":false,"covered":false,"note":"missed"}],
                      "summary":"Mostly there"},
          "suggestedRating":3,"normalizedAnswer":"spaced","speechRequested":0,
          "createdAt":"2026-10-05T12:00:00+00:00","updatedAt":"2026-10-05T12:00:01+00:00"},
         {"id":"a2","cardId":"c2","mode":"skipped","answer":null,"answerMode":"self","gradeStatus":"skipped",
          "coverage":null,"suggestedRating":null,"normalizedAnswer":null}]
        """.utf8))
        XCTAssertEqual(attempts[0].coverage?.claims.count, 2)
        XCTAssertFalse(attempts[0].coverage?.gated ?? true)
        XCTAssertFalse(attempts[0].coverage?.claims[1].essential ?? true)
        XCTAssertNil(attempts[1].coverage)

        let stats = try JSONDecoder().decode(LearningStats.self, from: Data(
            #"{"total":12,"due":3,"pending":2,"mastered":4,"learning":8}"#.utf8))
        XCTAssertEqual(stats.due, 3)

        let folders = try JSONDecoder().decode([LearningFolder].self, from: Data("""
        [{"id":"f1","name":"Rust","position":0,"evidenceProviderId":null,"evidenceProviderName":null,
          "activeCount":5,"pendingCount":0,"dueCount":2,"createdAt":"2026-10-01T09:00:00+00:00"}]
        """.utf8))
        XCTAssertEqual(folders.first?.dueCount, 2)
    }

    func testFiltersBecomeTheQueryTheRoutesRead() {
        XCTAssertEqual(LearningFilter().query, [:])
        XCTAssertEqual(LearningFilter(folderId: "f1", tag: "rust").query, ["folderId": "f1", "tag": "rust"])
    }

    func testASessionAnswersThenRatesInOrder() {
        var session = LearningSession(due: [card("1"), card("2"), card("3")], attempts: [])
        XCTAssertFalse(session.inResults)
        XCTAssertEqual(session.card?.id, "1")
        XCTAssertEqual(session.position, 1)

        XCTAssertNil(session.answer("   "), "A blank answer isn't one")
        XCTAssertEqual(session.answer(" spaced ", id: "a1")?.text, "spaced")
        XCTAssertEqual(session.skip(id: "a2")?.skipped, true)
        XCTAssertEqual(session.card?.id, "3")
        XCTAssertEqual(session.position, 3)
        _ = session.answer("third", id: "a3")

        XCTAssertTrue(session.inResults)
        XCTAssertNil(session.card)
        XCTAssertEqual(session.result?.id, "a1")
        XCTAssertFalse(session.advance())
        XCTAssertEqual(session.result?.card.id, "2")
        XCTAssertEqual(session.position, 2)
        XCTAssertFalse(session.advance())
        XCTAssertTrue(session.advance())
        XCTAssertNil(session.result)
    }

    func testAResumedSessionStartsAfterTheAnswersAlreadyGiven() {
        // `/due` puts the answered cards first; the attempts come oldest first.
        let attempts = [LearningAttempt(id: "a2", cardId: "2", mode: "answered", answer: "two"),
                        LearningAttempt(id: "a1", cardId: "1", mode: "skipped", gradeStatus: "skipped"),
                        LearningAttempt(id: "gone", cardId: "elsewhere", mode: "answered", answer: "x")]
        let session = LearningSession(due: [card("2"), card("1"), card("3")], attempts: attempts)
        XCTAssertEqual(session.answers.map(\.id), ["a2", "a1"], "An attempt for a card outside the deck is left out")
        XCTAssertTrue(session.answers[1].skipped)
        XCTAssertEqual(session.card?.id, "3")
        XCTAssertEqual(session.position, 3)
    }

    func testAnEmptyDeckIsNeverInResults() {
        let session = LearningSession(due: [], attempts: [])
        XCTAssertTrue(session.isEmpty)
        XCTAssertFalse(session.inResults)
        XCTAssertNil(session.card)
        XCTAssertNil(session.result)
    }

    func testGradesAreReadFromTheServersAttempts() {
        var session = LearningSession(due: [card("1"), card("2")], attempts: [])
        let typed = session.answer("spaced", id: "a1")!
        let flipped = session.skip(id: "a2")!
        let coverage = ClaimCoverage(claims: [CoverageClaim(text: "c", covered: true)], summary: "ok")

        XCTAssertEqual(LearningSession.grade(of: typed, in: []), .pending, "Not listed yet is still pending")
        XCTAssertNil(LearningSession.grade(of: flipped, in: []), "A flip is self-rated")
        XCTAssertEqual(LearningSession.grade(of: typed, in: [
            LearningAttempt(id: "a1", cardId: "1", mode: "answered", gradeStatus: "error")]), .error)
        XCTAssertEqual(LearningSession.grade(of: typed, in: [
            LearningAttempt(id: "a1", cardId: "1", mode: "answered", answer: "spaced", gradeStatus: "done",
                            coverage: coverage, suggestedRating: 4, normalizedAnswer: "Spaced.")]),
            .done(coverage, suggested: .easy, gradedAs: "Spaced."))
        XCTAssertEqual(LearningSession.grade(of: typed, in: [
            LearningAttempt(id: "a1", cardId: "1", mode: "answered", answer: "spaced", gradeStatus: "done",
                            coverage: coverage)]),
            .done(coverage, suggested: .good, gradedAs: "spaced"), "No suggestion defaults to Good, as on the desktop")

        XCTAssertTrue(LearningSession.gradesOutstanding([LearningAttempt(id: "a1", cardId: "1", mode: "answered")]))
        XCTAssertFalse(LearningSession.gradesOutstanding([
            LearningAttempt(id: "a2", cardId: "2", mode: "skipped", gradeStatus: "skipped")]))
    }

    func testTagsAreNormalizedLikeTheServers() {
        XCTAssertEqual(LearningTags.parse(" Rust, rust,  memory ,, Go "), ["rust", "memory", "go"])
        XCTAssertEqual(LearningTags.parse(""), [])
    }

    func testADictatedAnswerIsSavedAsVoice() {
        var session = LearningSession(due: [card("1"), card("2"), card("3")], attempts: [])
        XCTAssertEqual(session.answer("spoken", voice: true, id: "a1")?.answerMode, "voice")
        XCTAssertEqual(session.answer("typed", id: "a2")?.answerMode, "typed")
        XCTAssertEqual(session.skip(id: "a3")?.answerMode, "self")

        // Resuming keeps how each was answered, so its rating says so too.
        let resumed = LearningSession(due: [card("1"), card("2")], attempts: [
            LearningAttempt(id: "a1", cardId: "1", mode: "answered", answer: "spoken", answerMode: "voice"),
            LearningAttempt(id: "a2", cardId: "2", mode: "answered", answer: "typed", answerMode: "typed")])
        XCTAssertEqual(resumed.answers.map(\.voice), [true, false])
    }

    func testTheClipGoesToTranscribeAsAudioAndNothingElse() throws {
        let clip = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).m4a")
        try Data("not really aac".utf8).write(to: clip)
        defer { try? FileManager.default.removeItem(at: clip) }
        let body = try TranscribeMultipart(audio: clip)
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = String(decoding: try Data(contentsOf: body.url), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"audio\"; filename=\"answer.m4a\""))
        XCTAssertTrue(text.contains("not really aac"))
        XCTAssertFalse(text.contains("name=\"source\""), "A source would log it as a dictation")
        XCTAssertTrue(text.hasSuffix("--\(body.boundary)--\r\n"))

        let empty = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).m4a")
        try Data().write(to: empty)
        defer { try? FileManager.default.removeItem(at: empty) }
        XCTAssertThrowsError(try TranscribeMultipart(audio: empty))
    }

    func testSpeechModeIsStampedOnTheAnswerAndItsSummaryRead() throws {
        var session = LearningSession(due: [card("1"), card("2")], attempts: [])
        XCTAssertTrue(session.answer("on", speech: true, id: "a1")?.speech ?? false)
        XCTAssertFalse(session.answer("off", id: "a2")?.speech ?? true)

        let spoken = try JSONDecoder().decode(ClaimCoverage.self, from: Data(
            #"{"claims":[],"summary":"s","speechSummary":"You missed the compiler check."}"#.utf8))
        XCTAssertEqual(spoken.speechSummary, "You missed the compiler check.")
        let blank = try JSONDecoder().decode(ClaimCoverage.self, from: Data(#"{"claims":[],"speechSummary":"  "}"#.utf8))
        XCTAssertNil(blank.speechSummary, "Nothing to say is no summary")
    }

    func testTheTextToSpeakIsFormEncoded() {
        XCTAssertEqual(String(decoding: JournalAPI.ttsForm("You missed A & B = 2?"), as: UTF8.self),
                       "text=You%20missed%20A%20%26%20B%20%3D%202%3F")
    }
}
