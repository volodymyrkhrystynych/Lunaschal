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

    // Library, Workout log and Settings live behind the More tab. Re-selecting
    // More keeps whatever was pushed, so step back to the menu before choosing.
    private func openMore(_ app: XCUIApplication, _ name: String,
                          file: StaticString = #filePath, line: UInt = #line) {
        let more = tab(app, "More")
        let hittable = NSPredicate(format: "exists == true AND hittable == true")
        guard XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: hittable, object: more)],
                            timeout: 10) == .completed else {
            XCTFail("More did not become tappable", file: file, line: line)
            return
        }
        let row = app.buttons["more-\(name)"]
        // As in selectTab, a tap during the previous animation can be dropped.
        for _ in 0..<2 where !row.exists {
            more.tap()
            let back = app.navigationBars.buttons["More"]
            if back.waitForExistence(timeout: 2) { back.tap() }
            _ = row.waitForExistence(timeout: 3)
        }
        XCTAssertTrue(row.exists, "No \(name) in More", file: file, line: line)
        row.tap()
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 5), file: file, line: line)
    }

    func testBottomBarIsCaptureJournalChatTodoMore() {
        let app = XCUIApplication()
        app.launch()
        for name in ["Capture", "Journal", "Chat", "Todo", "More"] {
            XCTAssertTrue(tab(app, name).waitForExistence(timeout: 10), "Missing tab \(name)")
        }
        XCTAssertFalse(tab(app, "Library").exists)
        XCTAssertFalse(tab(app, "Settings").exists)
        selectTab(app, "Chat")
        XCTAssertTrue(app.staticTexts["Chat isn't in the app yet"].exists)
        selectTab(app, "Todo")
        XCTAssertTrue(app.staticTexts["Todo isn't in the app yet"].exists)
        openMore(app, "Workout log")
        XCTAssertTrue(app.staticTexts["Workout log isn't in the app yet"].exists)
        openMore(app, "Library")
        openMore(app, "Settings")
        XCTAssertTrue(app.buttons["Library downloads"].waitForExistence(timeout: 5))
    }

    func testLibraryCategoriesAndDownloadSettingsAreSeparate() {
        let app = XCUIApplication()
        app.launch()
        let isPad = UIDevice.current.userInterfaceIdiom == .pad
        XCTAssertEqual(tab(app, "Draw").exists, isPad)
        XCTAssertEqual(tab(app, "Study").exists, isPad)
        openMore(app, "Library")
        XCTAssertTrue(app.searchFields.firstMatch.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Filter books"].exists)
        XCTAssertFalse(app.buttons["library-study_sources"].exists)
        XCTAssertFalse(app.buttons["library-papers"].exists)
        XCTAssertFalse(app.buttons["Download library over Wi-Fi"].exists)
        if isPad {
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
        }
        openMore(app, "Settings")
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

    func testYouTubeLinksAttachToTheEntryAndSaveStaysPinned() {
        let app = XCUIApplication()
        app.launch()
        let save = app.buttons["Save entry"]
        XCTAssertTrue(save.waitForExistence(timeout: 10))
        let window = app.windows.firstMatch.frame
        XCTAssertGreaterThan(save.frame.midX, window.midX, "Save sits on the right")
        XCTAssertGreaterThan(save.frame.midY, window.midY, "Save sits at the bottom")
        XCTAssertFalse(save.isEnabled)

        let text = "Watched \(UUID().uuidString.prefix(8))"
        let editor = app.textViews["Journal text"]
        editor.tap()
        editor.typeText(text)
        let field = app.textFields["YouTube video URL"]
        field.tap()
        field.typeText("https://youtu.be/aircAruvnKk")
        app.buttons["Add link"].tap()
        XCTAssertTrue(app.staticTexts["https://www.youtube.com/watch?v=aircAruvnKk"].waitForExistence(timeout: 5))
        // A second URL left typed but not added is attached on save too.
        field.tap()
        field.typeText("https://youtube.com/shorts/dQw4w9WgXcQ")
        save.tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["https://www.youtube.com/watch?v=aircAruvnKk"].exists)

        tab(app, "Journal").tap()
        XCTAssertTrue(app.staticTexts[text].waitForExistence(timeout: 10))
        app.staticTexts[text].tap()
        XCTAssertTrue(app.staticTexts["Saved YouTube links"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.links["https://www.youtube.com/watch?v=aircAruvnKk"].exists
                      || app.buttons["https://www.youtube.com/watch?v=aircAruvnKk"].exists)
        XCTAssertTrue(app.links["https://www.youtube.com/watch?v=dQw4w9WgXcQ"].exists
                      || app.buttons["https://www.youtube.com/watch?v=dQw4w9WgXcQ"].exists)
    }

    func testSaveFoodEntrySitsLeftOfSaveEntryAndLeavesTheLinks() {
        let app = XCUIApplication()
        app.launch()
        let food = app.buttons["Save food entry"]
        let save = app.buttons["Save entry"]
        XCTAssertTrue(food.waitForExistence(timeout: 10))
        let window = app.windows.firstMatch.frame
        XCTAssertLessThan(food.frame.midX, window.midX, "Save food entry sits on the left")
        XCTAssertGreaterThan(save.frame.midX, window.midX, "Save entry sits on the right")
        XCTAssertEqual(food.frame.midY, save.frame.midY, accuracy: 4)
        XCTAssertGreaterThan(food.frame.midY, window.midY, "Both sit at the bottom")
        XCTAssertFalse(food.isEnabled)

        let link = "https://www.youtube.com/watch?v=aircAruvnKk"
        let field = app.textFields["YouTube video URL"]
        field.tap()
        field.typeText("https://youtu.be/aircAruvnKk")
        app.buttons["Add link"].tap()
        XCTAssertTrue(app.staticTexts[link].waitForExistence(timeout: 5))
        XCTAssertFalse(food.isEnabled, "A link alone is not a meal")

        let meal = "Ramen \(UUID().uuidString.prefix(8))"
        // Typing the link scrolled the form; bring the editor back into view.
        app.swipeDown()
        let editor = app.textViews["Journal text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        editor.tap()
        editor.typeText(meal)
        food.tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts[link].exists, "The link waits for the next journal entry")

        selectTab(app, "Journal")
        XCTAssertTrue(app.staticTexts[meal].waitForExistence(timeout: 10))
        app.staticTexts[meal].tap()
        XCTAssertTrue(app.staticTexts["Food log entry"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Saved YouTube link"].exists)

        // Leave the draft empty for the next test: the link goes in a journal entry.
        selectTab(app, "Capture")
        app.buttons["Save entry"].tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts[link].exists)
    }

    func testCaptureActionsShareOneLineAndAPhotoAttachesToTheEntry() {
        let app = XCUIApplication()
        app.launch()
        let names = ["Transcribe", "Record", "Take photo", "Choose photo", "Attach file"]
        let first = app.buttons[names[0]]
        XCTAssertTrue(first.waitForExistence(timeout: 10))
        for name in names {
            let button = app.buttons[name]
            XCTAssertTrue(button.exists, name)
            XCTAssertEqual(button.frame.midY, first.frame.midY, accuracy: 4, "\(name) sits in the same line")
        }
        XCTAssertFalse(app.staticTexts["Speak"].exists)
        XCTAssertFalse(app.staticTexts["Capture works offline. Sign in under Settings to sync."].exists)

        app.buttons["Choose photo"].tap()
        // The system picker runs out of process; its grid cells carry this id.
        let photo = app.images.matching(identifier: "PXGGridLayout-Info").firstMatch
        guard photo.waitForExistence(timeout: 10) else { return XCTFail("Photo picker did not open") }
        // Out-of-process cells report themselves unhittable; tap where one is drawn.
        photo.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let done = app.buttons["Done"]
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true"),
                                                                      object: done)], timeout: 5) == .completed)
        done.tap()
        XCTAssertTrue(app.staticTexts["Attachments"].waitForExistence(timeout: 10))
        app.buttons["Save entry"].tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Attachments"].exists)
        tab(app, "Journal").tap()
        let entry = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH 'Photo '")).firstMatch
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        XCTAssertTrue(app.staticTexts["Attachments"].waitForExistence(timeout: 5))
    }

    func testStoppedRecordingStaysInTheDraftUntilSaveEntry() {
        let app = XCUIApplication()
        // The microphone prompt is a system alert, outside the app.
        addUIInterruptionMonitor(withDescription: "Microphone") { alert in
            let allow = alert.buttons["Allow"]
            guard allow.exists else { return false }
            allow.tap()
            return true
        }
        app.launch()
        let transcribe = app.buttons["Transcribe"]
        XCTAssertTrue(transcribe.waitForExistence(timeout: 10))
        transcribe.tap()
        let stop = app.buttons["Stop recording"]
        if !stop.waitForExistence(timeout: 3) {
            app.tap() // lets the interruption monitor answer the prompt
        }
        XCTAssertTrue(stop.waitForExistence(timeout: 10))
        Thread.sleep(forTimeInterval: 1.5)
        stop.tap()

        // Stopping keeps the clip here instead of saving an entry.
        let clip = app.staticTexts["Transcription"]
        XCTAssertTrue(clip.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Saved on this device"].exists)
        XCTAssertTrue(app.buttons["Save entry"].isEnabled)

        app.terminate()
        app.launch()
        XCTAssertTrue(app.staticTexts["Transcription"].waitForExistence(timeout: 10), "The draft survives a relaunch")

        app.buttons["Save entry"].tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Transcription"].exists)
    }
}
