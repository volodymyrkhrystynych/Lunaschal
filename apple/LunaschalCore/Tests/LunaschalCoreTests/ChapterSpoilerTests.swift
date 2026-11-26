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

    // MARK: - Cites

    private func parse(_ html: String) -> [ChapterBlock] { ChapterText.blocks(html: html, text: nil) }

    func testACiteInASentenceStaysInline() {
        for html in ["<p>He read <cite>Moby</cite> all night</p>",
                     "<blockquote><p>He read <cite>Moby</cite> all night</p></blockquote>"] {
            let blocks = parse(html)
            XCTAssertEqual(blocks.map(\.text), ["He read Moby all night"])
            XCTAssertEqual(blocks.first?.runs.map { $0.style.contains(.italic) }, [false, true, false])
        }
        XCTAssertEqual(parse("<blockquote><p><cite>Moby</cite> opens it</p><p><cite>Ahab</cite> ends it</p></blockquote>")
            .map(\.text), ["Moby opens it", "Ahab ends it"])
        XCTAssertEqual(parse("<blockquote>Read <b>on</b> <cite>Moby</cite> now</blockquote>").map(\.text), ["Read on Moby now"])
    }

    func testAQuoteTitleIsALineEvenBeforeBareText() {
        let blocks = parse("<blockquote>\n<cite>Bob said:</cite>An earlier post.</blockquote>")
        XCTAssertEqual(blocks.map(\.text), ["Bob said:", "An earlier post."])
        XCTAssertEqual(blocks.map(\.quoteDepth), [1, 1])
        XCTAssertEqual(parse("<blockquote>Outer.<blockquote><cite>Ann said:</cite>In.</blockquote></blockquote>").map(\.text),
                       ["Outer.", "Ann said:", "In."])
    }

    // MARK: - Every spoiler has its toggle

    private func assertOneToggle(_ html: String, _ title: String, file: StaticString = #filePath, line: UInt = #line) {
        let blocks = parse(html)
        let toggles = blocks.filter { if case .spoilerToggle = $0.kind { return true } else { return false } }
        XCTAssertEqual(toggles.map(\.text), [title], file: file, line: line)
        XCTAssertEqual(toggles.first?.spoilers, [], file: file, line: line)
        XCTAssertEqual(blocks.first { $0.text == "Hidden." }?.spoilers, [0], file: file, line: line)
        XCTAssertEqual(blocks.last?.text, "After.", file: file, line: line)
        XCTAssertEqual(blocks.last?.spoilers, [], file: file, line: line)
    }

    func testASpoilerAlwaysHasExactlyOneToggle() {
        let end = "<p>Hidden.</p></details><p>After.</p>"
        assertOneToggle("<details>" + end, "Spoiler")
        assertOneToggle("<details><summary></summary>" + end, "Spoiler")
        assertOneToggle("<details><summary> &nbsp; </summary>" + end, "Spoiler")
        assertOneToggle("<details><summary><img src=\"https://x.test/a.png\"></summary>" + end, "Spoiler")
        assertOneToggle("<details><summary><p>T</p></summary>" + end, "T")
        assertOneToggle("<details><summary><h3>One</h3><div>two</div><hr></summary>" + end, "One two")
        assertOneToggle("<details><summary><ul><li>T</li></ul></summary>" + end, "T")
        assertOneToggle("<details><summary>One<br><br>two</summary>" + end, "One two")
        assertOneToggle("<details><p>Early.</p><summary>Late</summary>" + end, "Late")
        assertOneToggle("<details><summary>T</summary><summary>Again</summary>" + end, "T")
    }

    func testAPictureInATitleShowsWithIt() {
        let blocks = parse("<details><summary><img src=\"https://x.test/a.png\"></summary>Hidden.</details>")
        XCTAssertEqual(blocks.map(\.kind), [.spoilerToggle(0), .image("https://x.test/a.png"), .paragraph])
        XCTAssertEqual(blocks.map(\.spoilers), [[], [], [0]])
    }

    func testNestedUntitledSpoilersEachHaveAToggle() {
        let blocks = parse("<details><details>Deep.</details></details>")
        XCTAssertEqual(blocks.map(\.kind), [.spoilerToggle(0), .spoilerToggle(1), .paragraph])
        XCTAssertEqual(blocks.map(\.spoilers), [[], [0], [0, 1]])
    }

    func testAPictureInAnInlineSpoilerIsMarked() {
        let hidden = parse("<p>See <span class=\"spoiler\"><img src=\"https://x.test/a.png\"></span> it</p>")
        XCTAssertEqual(hidden.map(\.kind), [.paragraph, .image("https://x.test/a.png"), .paragraph])
        XCTAssertEqual(hidden[1].runs.map(\.style), [.spoiler])
        XCTAssertEqual(parse("<p><img src=\"https://x.test/a.png\" alt=\"A map\"></p>")[0].runs.map(\.style), [[]])
    }

    // MARK: - Reopening at a place

    /// 0 paragraph, 1 toggle, 2-5 hidden (3 a nested toggle, 4 inside it), 6 paragraph.
    private var rows: [ChapterBlock] {
        parse("<p>A</p><details><summary>T</summary><p>B</p><details><summary>N</summary><p>C</p></details><p>D</p></details><p>E</p>")
    }

    func testAFractionCountsOnlyTheRowsShowing() {
        XCTAssertEqual(ChapterRows.closed(rows), [0, 1, 6])
        XCTAssertEqual(ChapterRows.row(at: 0, in: rows), 0)
        XCTAssertEqual(ChapterRows.row(at: 0.5, in: rows), 1)
        XCTAssertEqual(ChapterRows.row(at: 1, in: rows), 6)
        XCTAssertEqual(ChapterRows.row(at: 7, in: rows), 6)
        XCTAssertEqual(ChapterRows.row(at: .nan, in: rows), 0)
        XCTAssertEqual(ChapterRows.row(at: 1, in: []), 0)
    }

    func testARowInASpoilerBecomesItsTitlesFraction() {
        for row in 1...5 { XCTAssertEqual(ChapterRows.fraction(of: row, in: rows), 0.5) }
        XCTAssertEqual(ChapterRows.fraction(of: 6, in: rows), 1)
        XCTAssertEqual(ChapterRows.fraction(of: -1, in: rows), 0)
        XCTAssertEqual(ChapterRows.fraction(of: 0, in: []), 0)
    }

    func testASavedRowOpensTheSpoilersAroundIt() {
        let nested = ChapterRows.restore(saved: 4, in: rows)
        XCTAssertEqual(nested.row, 4)
        XCTAssertEqual(nested.open, [0, 1])
        XCTAssertTrue(rows[4].isShown(revealed: nested.open))
        XCTAssertEqual(ChapterRows.restore(saved: 6, in: rows).open, [])
        XCTAssertEqual(ChapterRows.restore(saved: 99, in: rows).row, 0)
    }
}
