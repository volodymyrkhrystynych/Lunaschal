import XCTest
import UIKit

final class OfflineCaptureTests: XCTestCase {
    private func tab(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        if UIDevice.current.userInterfaceIdiom == .phone {
            return app.tabBars.buttons[name]
        }
        // iPad's floating tabs are exposed as cells/other elements, not a TabBar.
        return app.descendants(matching: .any).matching(identifier: name).firstMatch
    }

    private func selectTab(_ app: XCUIApplication, _ name: String,
                           file: StaticString = #filePath, line: UInt = #line) {
        let item = tab(app, name)
        let hittable = NSPredicate(format: "exists == true AND hittable == true")
        guard XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: hittable, object: item)],
                            timeout: 10) == .completed else {
            XCTFail("Tab \(name) did not become tappable", file: file, line: line)
            return
        }
        // A tap during the previous navigation animation can be dropped by UIKit.
        // Retry once only when the requested screen has not appeared.
        for _ in 0..<2 {
            item.tap()
            if app.navigationBars[name].waitForExistence(timeout: 5) { return }
        }
        XCTFail("Tab \(name) did not open", file: file, line: line)
    }

    func testLibraryCategoriesAndDownloadSettingsAreSeparate() {
        let app = XCUIApplication()
        app.launch()
        XCTAssertEqual(tab(app, "Draw").exists, UIDevice.current.userInterfaceIdiom == .pad)
        selectTab(app, "Library")
        XCTAssertTrue(app.searchFields.firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Filter books"].exists)
        XCTAssertFalse(app.buttons["library-study_sources"].exists)
        XCTAssertFalse(app.buttons["library-papers"].exists)
        XCTAssertFalse(app.buttons["Download library over Wi-Fi"].exists)
        selectTab(app, "Study")
        let categories = [
            ("study_sources", "Documents"),
            ("papers", "Paper documents"),
            ("newspaper_frontpages", "Newspaper front pages"),
            ("wiki_articles", "Knowledge articles"),
        ]
        for (collection, title) in categories {
            let link = app.buttons["library-\(collection)"]
            XCTAssertTrue(link.waitForExistence(timeout: 5))
            link.tap()
            XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 5))
            XCTAssertTrue(app.searchFields.firstMatch.exists)
            app.navigationBars.buttons["Study"].tap()
            XCTAssertTrue(app.buttons["library-study_sources"].waitForExistence(timeout: 5))
        }
        selectTab(app, "Settings")
        let downloads = app.buttons["Library downloads"]
        XCTAssertTrue(downloads.waitForExistence(timeout: 5))
        downloads.tap()
        XCTAssertTrue(app.buttons["Download library over Wi-Fi"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Remove downloaded media"].exists)
        XCTAssertTrue(app.switches["PDF books"].exists)
    }

    func testDrawingWorkspaceReopensWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        guard UIDevice.current.userInterfaceIdiom == .pad else {
            XCTAssertFalse(tab(app, "Draw").exists)
            return
        }
        tab(app, "Draw").tap()
        let newDrawing = app.buttons["New drawing"]
        XCTAssertTrue(newDrawing.waitForExistence(timeout: 10))
        newDrawing.tap()
        let page = app.staticTexts["Untitled drawing"].firstMatch
        XCTAssertTrue(page.waitForExistence(timeout: 5))
        page.tap()
        XCTAssertTrue(app.buttons["Save locally"].waitForExistence(timeout: 5))
        app.terminate()
        app.launch()
        tab(app, "Draw").tap()
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
        tab(app, "Journal").tap()
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
