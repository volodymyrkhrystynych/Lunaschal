import SwiftUI
import XCTest
import LunaschalCore
@testable import Lunaschal

final class AuthoredTextTests: XCTestCase {
    /// The edited word is painted the user's blue and the rest keeps the
    /// normal text colour, in one attributed string.
    func testEachRunIsPaintedByItsAuthor() {
        let text = AuthoredText("I went to the market today.",
                                spans: .array([.array([.string("a"), .number(14)]),
                                               .array([.string("u"), .number(6)]),
                                               .array([.string("a"), .number(7)])]),
                                fallback: .user)
        let attributed = AuthoredTextView.attributed(text)
        let runs = attributed.runs.map { (String(attributed[$0.range].characters), $0.foregroundColor) }
        XCTAssertEqual(runs.map(\.0), ["I went to the ", "market", " today."])
        XCTAssertEqual(runs.map(\.1), [Color.aiText, Color.userText, Color.aiText])
    }

    /// Markdown loses its `**` when rendered; the painted runs still follow
    /// the source's authors: typed, then speech-to-text of a recording.
    func testMarkdownIsPaintedThroughItsSyntax() throws {
        let source = "**Buy** milk, said aloud"
        let text = AuthoredText(source, spans: .array([.array([.string("u"), .number(12)]),
                                                       .array([.string("s"), .number(12), .string("R")])]),
                                fallback: .ai)
        let styled = try AttributedString(markdown: source)
        let painted = AuthoredTextView.paint(styled, AuthorAligner(text))
        let runs = painted.runs.map { (String(painted[$0.range].characters), $0.foregroundColor) }
        XCTAssertEqual(runs.map(\.0).joined(), "Buy milk, said aloud")
        XCTAssertEqual(runs.first?.1, Color.userText)
        XCTAssertEqual(runs.last?.0, ", said aloud")
        XCTAssertEqual(runs.last?.1, Color.speechText)
    }
}
