import XCTest
@testable import LunaschalCore

final class TextAuthorshipTests: XCTestCase {
    struct RunCase: Decodable {
        let name: String, text: String, spans: JSONValue, fallback: String, runs: [[String]]
    }

    /// The server's own slicing cases, read from the backend's fixture, so the
    /// phone, the web and `backend/authorship.py` can't drift apart.
    func testSharedRunCases() throws {
        let fixture = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("backend/tests/fixtures/text_authorship.json")
        struct File: Decodable { let runs: [RunCase] }
        let cases = try JSONDecoder().decode(File.self, from: Data(contentsOf: fixture)).runs
        XCTAssertGreaterThan(cases.count, 5)
        for c in cases {
            let fallback = try XCTUnwrap(TextAuthor(rawValue: c.fallback))
            let got = AuthoredText(c.text, spans: c.spans, fallback: fallback).runs
            XCTAssertEqual(got.map { [$0.author.rawValue, $0.text] }, c.runs, c.name)
        }
    }

    struct AlignCase: Decodable {
        let name: String, source: String, spans: JSONValue, rendered: [String], runs: [[[String]]]
    }

    /// Markdown colouring: each rendered piece, fed in source order, takes the
    /// authors of the characters it was rendered from.
    func testSharedAlignmentCases() throws {
        let fixture = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("backend/tests/fixtures/text_authorship.json")
        struct File: Decodable { let aligned: [AlignCase] }
        let cases = try JSONDecoder().decode(File.self, from: Data(contentsOf: fixture)).aligned
        XCTAssertFalse(cases.isEmpty)
        for c in cases {
            let aligner = AuthorAligner(AuthoredText(c.source, spans: c.spans, fallback: .user))
            let got = c.rendered.map { piece -> [[String]] in
                var runs: [[String]] = []
                for (scalar, author) in zip(piece.unicodeScalars, aligner.authors(for: piece)) {
                    if let last = runs.last, last[0] == author.rawValue {
                        runs[runs.count - 1][1].unicodeScalars.append(scalar)
                    } else {
                        runs.append([author.rawValue, String(scalar)])
                    }
                }
                return runs
            }
            XCTAssertEqual(got, c.runs, c.name)
        }
    }

    func testSpeechRunsKeepTheirRecording() {
        let text = AuthoredText("said, typed", spans: .array([
            .array([.string("s"), .number(5), .string("01JREC")]),
            .array([.string("u"), .number(6)]),
        ]), fallback: .ai)
        XCTAssertEqual(text.runs, [.init(author: .speech, text: "said,", source: "01JREC"),
                                   .init(author: .user, text: " typed")])
        XCTAssertNil(text.soleAuthor)
    }

    private func change(_ collection: String, _ data: [String: JSONValue]) -> SyncChange {
        SyncChange(revision: 1, collection: collection, id: "01J0000000000000000000000A", deleted: false, data: data)
    }

    private func spans(_ pairs: [(String, Int)]) -> JSONValue {
        .array(pairs.map { .array([.string($0.0), .number(Double($0.1))]) })
    }

    func testAJournalEntryEditedAfterPolishShowsTheEditedWordAsTheUsers() throws {
        let record = change("journal_entries", [
            "content": .string("I went to the market today."),
            "authorship": .object(["content": spans([("a", 14), ("u", 6), ("a", 7)])]),
        ])
        let runs = try XCTUnwrap(record.authored("content", fallback: .user)).runs
        XCTAssertEqual(runs.map(\.author), [.ai, .user, .ai])
        XCTAssertEqual(runs[1].text, "market")
    }

    func testAPendingLocalEditIsTheUsersUntilTheServerMergesIt() throws {
        let record = change("journal_entries", [
            "content": .string("Typed on the phone, not synced yet"),
            "authorship": .object(["content": spans([("a", 5)])]),
        ])
        XCTAssertEqual(record.authored("content", fallback: .ai)?.runs.map(\.author), [.user])
    }

    func testAnOlderServerWithNoSpansUsesTheJournalRule() {
        let polished = change("journal_entries", ["content": .string("So, yeah."), "rawContent": .string("so yeah")])
        XCTAssertEqual(polished.journalContentFallback, .ai)
        let typed = change("journal_entries", ["content": .string("my words")])
        XCTAssertEqual(typed.journalContentFallback, .user)
        XCTAssertEqual(typed.authored("content", fallback: typed.journalContentFallback)?.runs.map(\.author), [.user])
        XCTAssertNil(typed.authored("title", fallback: .ai), "an empty field has nothing to colour")
    }

    func testTrimmingKeepsTheAuthorsOfWhatRemains() {
        let text = AuthoredText(runs: [.init(author: .ai, text: "  \n"), .init(author: .user, text: " hi "),
                                       .init(author: .ai, text: "there\n")])
        XCTAssertEqual(text.trimmed().runs, [.init(author: .user, text: "hi "), .init(author: .ai, text: "there")])
    }

    func testFoodNotesCarryTheirAuthorsTrimmedForTheCard() throws {
        let record = change("food_entries", [
            "notes": .string("Ordered the pad thai again, extra lime. "),
            "authorship": .object(["notes": spans([("a", 26), ("u", 14)])]),
        ])
        let meal = try XCTUnwrap(FoodEntryRecord(record: record))
        XCTAssertEqual(meal.authoredBody?.runs, [.init(author: .ai, text: "Ordered the pad thai again"),
                                                 .init(author: .user, text: ", extra lime.")])
        let raw = try XCTUnwrap(FoodEntryRecord(record: change("food_entries", [
            "rawContent": .string("pad thai"),
            "authorship": .object(["rawContent": spans([("s", 8)])]),
        ])))
        XCTAssertEqual(raw.authoredBody?.runs.map(\.author), [.speech], "what was said is speech-to-text")
    }
}
