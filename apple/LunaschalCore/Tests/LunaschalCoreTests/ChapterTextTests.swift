import XCTest
@testable import LunaschalCore

final class ChapterTextTests: XCTestCase {
    private func texts(_ html: String) -> [String] { ChapterText.blocks(html: html, text: nil).map(\.text) }

    func testParagraphsKeepPunctuationAgainstStyledWords() {
        let blocks = ChapterText.blocks(html: "<p>She said <i>no</i>. Then <b>left</b>,</p><p>Next  \n day.</p>", text: nil)
        XCTAssertEqual(blocks.map(\.text), ["She said no. Then left,", "Next day."])
        XCTAssertEqual(blocks[0].runs.first { $0.text == "no" }?.style, .italic)
        XCTAssertEqual(blocks[0].runs.first { $0.text == "left" }?.style, .bold)
    }

    /// XenForo: lines end in <br>, and an empty line between them is a paragraph.
    func testOneBreakIsANewLineAndTwoAreANewParagraph() {
        XCTAssertEqual(texts("<div>First line<br>\nsame paragraph<br>\n<br>\nSecond paragraph<br></div>"),
                       ["First line\nsame paragraph", "Second paragraph"])
        XCTAssertEqual(texts("One<br /><br /><br />Two"), ["One", "Two"])
    }

    func testSceneBreaksRulesHeadingsQuotesAndLists() {
        let blocks = ChapterText.blocks(html: """
            <h2>Chapter 3</h2><p>Before.</p><p>* * *</p><hr><blockquote><p>Quoted</p></blockquote>
            <ol start="4"><li>four</li><li>five</li></ol><ul><li>dot</li></ul>
            """, text: nil)
        XCTAssertEqual(blocks.map(\.kind), [.heading(2), .paragraph, .sceneBreak, .rule, .paragraph,
                                            .listItem("4."), .listItem("5."), .listItem("•")])
        XCTAssertEqual(blocks[4].quoteDepth, 1)
        XCTAssertEqual(blocks[5].quoteDepth, 0)
    }

    func testEntitiesLinksAndStrayAngleBrackets() {
        let blocks = ChapterText.blocks(html: "<p>Tom &amp; Jerry&#8217;s &lt;3 &nbsp;x &unknown; 1 < 2 <a href=\"https://e.x/a\">here</a></p>",
                                        text: nil)
        XCTAssertEqual(blocks.map(\.text), ["Tom & Jerry’s <3 \u{00A0}x &unknown; 1 < 2 here"])
        XCTAssertEqual(blocks[0].runs.last?.link, "https://e.x/a")
    }

    func testPreformattedKeepsItsLinesAndUnclosedTagsLoseNoWords() {
        XCTAssertEqual(texts("<pre>a  b\nc</pre><p><i>open forever"), ["a  b\nc", "open forever"])
    }

    func testPlainTextIsTheFallback() {
        let blocks = ChapterText.blocks(html: "  ", text: "One\n\n Two \n***")
        XCTAssertEqual(blocks.map(\.text), ["One", "Two", "***"])
        XCTAssertEqual(blocks.last?.kind, .sceneBreak)
        XCTAssertEqual(ChapterText.blocks(html: nil, text: nil), [])
    }
}
