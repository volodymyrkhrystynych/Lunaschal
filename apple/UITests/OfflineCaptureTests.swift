import XCTest

final class OfflineCaptureTests: XCTestCase {
    func testDrawingWorkspaceReopensWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        app.tabBars.buttons["Draw"].tap()
        let newDrawing = app.buttons["New drawing"]
        XCTAssertTrue(newDrawing.waitForExistence(timeout: 10))
        newDrawing.tap()
        let page = app.staticTexts["Untitled drawing"].firstMatch
        XCTAssertTrue(page.waitForExistence(timeout: 5))
        page.tap()
        XCTAssertTrue(app.buttons["Save locally"].waitForExistence(timeout: 5))
        app.terminate()
        app.launch()
        app.tabBars.buttons["Draw"].tap()
        XCTAssertTrue(app.staticTexts["Untitled drawing"].firstMatch.waitForExistence(timeout: 5))
    }

    func testCaptureSurvivesTerminationWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        let text = "Offline capture \(UUID().uuidString.prefix(8))"
        let editor = app.textViews["Journal text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.tap()
        editor.typeText(text)
        app.buttons["Save entry"].tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))
        app.terminate()
        app.launch()
        app.tabBars.buttons["Journal"].tap()
        XCTAssertTrue(app.staticTexts[text].waitForExistence(timeout: 10))
        let search = app.searchFields.firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 5))
        search.tap()
        search.typeText(text)
        XCTAssertTrue(app.staticTexts[text].waitForExistence(timeout: 5))
        search.typeText("ZZZZZZ")
        XCTAssertTrue(app.staticTexts["No matching entries"].waitForExistence(timeout: 5))
        search.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 6))
        XCTAssertTrue(app.staticTexts[text].waitForExistence(timeout: 5))
        app.staticTexts[text].tap()
        XCTAssertTrue(app.staticTexts["Saved on device · Waiting to sync"].exists)
        XCTAssertTrue(app.staticTexts["Original text"].exists)
    }
}
