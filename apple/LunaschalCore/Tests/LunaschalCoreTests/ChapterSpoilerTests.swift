import XCTest
@testable import LunaschalCore

/// A forum post's [SPOILER]s stay closed until opened, its [ISPOILER]s stay
/// hidden in the line, and a quote keeps its "Name said:". The HTML is what
/// the server stores for `backend/tests/fixtures/fanfic/reader_styled.html`
/// (`restyle.render_forum_post`).
final class ChapterSpoilerTests: XCTestCase {
    private let serverHTML = """
    <b>Bold opening.</b> <span style="color:#b30000">A red voice.</span><br>
    <span style="font-family:&quot;Courier New&quot;">[SYSTEM MESSAGE]</span><br>
    <br>
    <details><summary>Status Sheet</summary><b>STR:</b> 12<br>
    <details><summary>Spoiler</summary>Hidden perk.<blockquote><cite>Narrator said:</cite><div><div>Quoted inside a spoiler.</div></div></blockquote></details></details>
    <blockquote>
    <cite>Bob said:</cite>
    <div>
    <div>
    \t\t\tAn earlier post.
    \t\t</div>

    </div>
    </blockquote>
    The twist was <span class="spoiler">the butler</span>.
    """

    private var blocks: [ChapterBlock] { ChapterText.blocks(html: serverHTML, text: nil) }

    private func block(_ text: String) throws -> ChapterBlock {
        try XCTUnwrap(blocks.first { $0.text.contains(text) }, "no block containing \(text)")
    }

    func testASpoilerIsAToggleAndTheBlocksItHides() throws {
        let toggle = try block("Status Sheet")
        XCTAssertEqual(toggle.kind, .spoilerToggle(0))
        XCTAssertEqual(toggle.spoilers, [], "the outer toggle is always visible")
        XCTAssertEqual(try block("STR:").spoilers, [0])
        XCTAssertFalse(blocks.contains { $0.text.contains("Spoiler: ") }, "no leftover button text")
    }

    func testANestedSpoilerShowsOnlyOnceBothAreOpen() throws {
        let inner = try block("Spoiler")
        XCTAssertEqual(inner.kind, .spoilerToggle(1))
        XCTAssertEqual(inner.spoilers, [0])
        let perk = try block("Hidden perk.")
        XCTAssertEqual(perk.spoilers, [0, 1])
        XCTAssertFalse(perk.isShown(revealed: []))
        XCTAssertFalse(perk.isShown(revealed: [0]))
        XCTAssertFalse(perk.isShown(revealed: [1]))
        XCTAssertTrue(perk.isShown(revealed: [0, 1]))
        XCTAssertTrue(inner.isShown(revealed: [0]))
    }

    func testBlocksAfterASpoilerAreOutsideIt() throws {
        XCTAssertEqual(try block("An earlier post.").spoilers, [])
        XCTAssertEqual(try block("The twist was").spoilers, [])
        XCTAssertEqual(try block("Bold opening.").spoilers, [])
    }

    func testAQuoteTitleIsItsOwnItalicLineInTheQuote() throws {
        let cite = try block("Bob said:")
        XCTAssertEqual(cite.text, "Bob said:")
        XCTAssertEqual(cite.quoteDepth, 1)
        XCTAssertTrue(cite.runs.allSatisfy { $0.style.contains(.italic) })
        let quoted = try block("An earlier post.")
        XCTAssertEqual(quoted.quoteDepth, 1)
        XCTAssertFalse(quoted.runs.contains { $0.style.contains(.italic) })

        let inSpoiler = try block("Narrator said:")
        XCTAssertEqual(inSpoiler.spoilers, [0, 1])
        XCTAssertEqual(inSpoiler.quoteDepth, 1)
    }

    func testAnInlineSpoilerIsMarkedOnItsOwnWords() throws {
        let line = try block("The twist was")
        let hidden = line.runs.filter { $0.style.contains(.spoiler) }
        XCTAssertEqual(hidden.map(\.text), ["the butler"])
        XCTAssertFalse(line.runs.first!.style.contains(.spoiler))
    }

    func testBoldColourAndFontSurviveAroundSpoilers() throws {
        let opening = try block("Bold opening.")
        XCTAssertTrue(opening.runs.first { $0.text.contains("Bold opening.") }!.style.contains(.bold))
        let red = try XCTUnwrap(opening.runs.first { $0.text.contains("A red voice.") })
        XCTAssertEqual(red.look.color, ChapterColor.parse("#b30000"))
        let typed = try XCTUnwrap(opening.runs.first { $0.text.contains("[SYSTEM MESSAGE]") })
        XCTAssertEqual(typed.look.font, ChapterFont(family: "Courier New", design: .monospaced))
        XCTAssertTrue(try block("STR:").runs.first!.style.contains(.bold))
    }
}
