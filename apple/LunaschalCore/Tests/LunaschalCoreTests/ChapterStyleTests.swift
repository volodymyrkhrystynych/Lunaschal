import XCTest
@testable import LunaschalCore

/// A post's [COLOR], [FONT], [SIZE] and [CENTER] survive into the reader.
final class ChapterStyleTests: XCTestCase {
    private let red = ChapterColor(red: 1, green: 0, blue: 0)

    func testColoursParse() {
        XCTAssertEqual(ChapterColor.parse("#f00"), red)
        XCTAssertEqual(ChapterColor.parse("#FF0000"), red)
        XCTAssertEqual(ChapterColor.parse("rgb(255, 0, 0)"), red)
        XCTAssertEqual(ChapterColor.parse("rgba(255,0,0,0.5)"), red)
        XCTAssertEqual(ChapterColor.parse("Red"), red)
        XCTAssertNil(ChapterColor.parse("rgba(0,0,0,0)"), "transparent is no colour")
        XCTAssertNil(ChapterColor.parse("url(x)"))
        XCTAssertNil(ChapterColor.parse("#12"))
    }

    func testFontsKeepTheirFamilyAndAStandIn() {
        XCTAssertEqual(ChapterFont.parse("'Courier New', monospace"), ChapterFont(family: "Courier New", design: .monospaced))
        XCTAssertEqual(ChapterFont.parse("Verdana"), ChapterFont(family: "Verdana", design: .sansSerif))
        XCTAssertEqual(ChapterFont.parse("\"Comic Sans MS\""), ChapterFont(family: "Comic Sans MS", design: .rounded))
        XCTAssertEqual(ChapterFont.parse("Georgia"), ChapterFont(family: "Georgia", design: .serif))
        XCTAssertEqual(ChapterFont.parse("monospace"), ChapterFont(family: nil, design: .monospaced))
        XCTAssertNil(ChapterFont.parse(" "))
    }

    func testSizesAreRelativeToTheForumsBody() {
        XCTAssertEqual(ChapterCSS.scale("22px")!, 22.0 / 15, accuracy: 0.001)
        XCTAssertEqual(ChapterCSS.scale("150%"), 1.5)
        XCTAssertEqual(ChapterCSS.scale("x-large"), 1.5)
        XCTAssertEqual(ChapterCSS.scale("200px"), 2, "clamped")
        XCTAssertNil(ChapterCSS.scale("15px"), "the body size is no change")
        XCTAssertNil(ChapterCSS.scale("big"))
    }

    func testStylesApplyToTheirOwnTextAndNest() throws {
        let blocks = ChapterText.blocks(html: """
        Plain <span style="color: #ff0000">red <span style="font-family: 'Courier New'; font-size: 30px">typed</span>\
         still red</span> plain.<br><br>\
        <span style="font-weight: bold; font-style: italic; text-decoration: underline">loud</span>
        """, text: nil)
        XCTAssertEqual(blocks.count, 2)
        let runs = blocks[0].runs
        XCTAssertEqual(runs.map(\.text), ["Plain ", "red ", "typed", " still red", " plain."])
        XCTAssertTrue(runs[0].look.isPlain)
        XCTAssertEqual(runs[1].look, ChapterLook(color: red))
        XCTAssertEqual(runs[2].look, ChapterLook(color: red, font: ChapterFont(family: "Courier New", design: .monospaced),
                                                 scale: 2))
        XCTAssertEqual(runs[3].look, ChapterLook(color: red), "the inner span's font ends with it")
        XCTAssertTrue(runs[4].look.isPlain)
        XCTAssertEqual(blocks[1].runs.first?.style, [.bold, .italic, .underline])
    }

    func testTheSanitizersEscapedQuotesAreRead() {
        // Exactly what backend/fanfic/sanitize.py stores.
        let blocks = ChapterText.blocks(
            html: "<span style=\"color:#ff0000;font-family:&quot;Courier New&quot;;font-size:22px\">red</span>", text: nil)
        XCTAssertEqual(blocks.first?.runs.first?.look.font, ChapterFont(family: "Courier New", design: .monospaced))
        XCTAssertEqual(blocks.first?.runs.first?.look.color, red)
    }

    func testCenteredBlocksAndPicturesAreCentered() {
        let blocks = ChapterText.blocks(html: """
        <div style="text-align: center">Chapter One<br><br>A subtitle</div>\
        <p>Back to normal.</p>\
        <div style="text-align:right"><img src="https://x.test/a.png"></div>
        """, text: nil)
        XCTAssertEqual(blocks.map(\.text), ["Chapter One", "A subtitle", "Back to normal.", ""])
        XCTAssertEqual(blocks.map(\.alignment), [.center, .center, .leading, .trailing])
    }

    func testAColourTooDarkForTheDarkPageIsLightenedOnlyAsFarAsNeeded() {
        let navy = ChapterColor.parse("navy")!
        let onDark = navy.readable(onDark: true)
        XCTAssertGreaterThanOrEqual(onDark.contrast(with: ChapterColor(red: 0, green: 0, blue: 0)), 3)
        XCTAssertGreaterThan(onDark.blue, onDark.red, "still blue")
        XCTAssertEqual(navy.readable(onDark: false), navy, "already readable on white")
        let yellow = ChapterColor.parse("yellow")!
        XCTAssertGreaterThanOrEqual(yellow.readable(onDark: false).contrast(with: ChapterColor(red: 1, green: 1, blue: 1)), 3)
        XCTAssertEqual(yellow.readable(onDark: true), yellow)
    }
}
