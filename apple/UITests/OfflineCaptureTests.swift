import XCTest
import UIKit

final class OfflineCaptureTests: XCTestCase {
    private func tab(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        if UIDevice.current.userInterfaceIdiom == .phone {
            return app.tabBars.buttons[name]
        }
        // iPad's floating tabs are exposed as cells/other elements, not a TabBar,
        // and each tab appears twice: firstMatch can be the copy that is never
        // hittable. Prefer the one that is, once it shows up.
        let matches = app.descendants(matching: .any).matching(identifier: name)
        let deadline = Date().addingTimeInterval(10)
        repeat {
            if let visible = matches.allElementsBoundByIndex.first(where: { $0.isHittable }) { return visible }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        } while Date() < deadline && matches.count > 0
        return matches.firstMatch
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

    // A tap while a list is still settling can leave the field unfocused, and
    // typeText then fails outright. Retry the tap until the keyboard is there.
    private func focus(_ field: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        let focused = NSPredicate(format: "hasKeyboardFocus == true")
        for _ in 0..<3 {
            field.tap()
            if XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: focused, object: field)],
                              timeout: 3) == .completed { return }
        }
        XCTFail("\(field) did not take keyboard focus", file: file, line: line)
    }

    // Library and Settings live behind the More tab. Re-selecting
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
        // The menu stays in the tree under a pushed screen, so wait for the row
        // to be tappable, not merely present. As in selectTab, retry a tap that
        // the previous animation swallowed.
        let reachable = XCTNSPredicateExpectation(predicate: hittable, object: row)
        for _ in 0..<2 where !row.isHittable {
            more.tap()
            let back = app.navigationBars.buttons["More"]
            if back.waitForExistence(timeout: 2) { back.tap() }
            _ = XCTWaiter.wait(for: [reachable], timeout: 3)
        }
        XCTAssertTrue(row.isHittable, "No \(name) in More", file: file, line: line)
        // The row turns hittable while the pop back to the menu is still
        // animating, and a tap then is dropped (seen on CI's slower iPad
        // simulator). Retry until the screen opens, as selectTab does.
        for _ in 0..<3 {
            row.tap()
            if app.navigationBars[name].waitForExistence(timeout: 5) { return }
        }
        XCTFail("\(name) did not open from More", file: file, line: line)
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
        XCTAssertTrue(app.textFields["chat-input"].waitForExistence(timeout: 5))
        selectTab(app, "Todo")
        XCTAssertTrue(app.buttons["todo-add"].waitForExistence(timeout: 5))
        openMore(app, "Library")
        // The workout log is Capture's Workout page, not a More row.
        XCTAssertFalse(app.buttons["more-Workout log"].exists)
        openMore(app, "Settings")
        XCTAssertTrue(app.buttons["Library downloads"].waitForExistence(timeout: 5))
    }

    func testDailyLogsWeightAndCaloriesWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        // Entry is the page the tab opens on.
        XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 10))
        let daily = app.segmentedControls.buttons["Daily"]
        XCTAssertTrue(daily.exists)
        daily.tap()
        // Weather heads the page, whether or not this simulator has a forecast cached.
        XCTAssertTrue(app.staticTexts["Weather"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Take selfie"].waitForExistence(timeout: 5)
                      || app.buttons["Retake selfie"].exists)

        // The simulator keeps earlier runs' logs, so use values only this run wrote.
        // A non-zero tenth: the app shows 70.0 as "70".
        let weight = "\(Int.random(in: 50..<99)).\(Int.random(in: 1...9))"
        // Letters only: a name ending in digits would rightly parse as its count.
        let meal = "Oats " + String((0..<6).map { _ in "ABCDEFGHJKMNPQRSTVWXYZ".randomElement()! })
        let field = app.textFields["Body weight"]
        field.tap()
        field.typeText(weight)
        // A tap while the number pad is still rising can be dropped; retry once.
        let log = app.buttons["Log weight"]
        log.tap()
        if !app.staticTexts[weight].waitForExistence(timeout: 3), log.isEnabled { log.tap() }
        XCTAssertTrue(app.staticTexts[weight].waitForExistence(timeout: 5))

        // One line, as on the desktop: no count at the end is refused, and a
        // trailing count becomes the calories.
        // Below the fold under the weather card, and a lazy Form leaves
        // off-screen rows out of the tree until they are scrolled to.
        let line = app.textFields["Calories"]
        for _ in 0..<5 where !line.exists { app.swipeUp() }
        line.tap()
        // The line is kept as a draft, so an earlier run may have left text in it.
        let leftover = (line.value as? String) ?? ""
        if leftover != line.placeholderValue, !leftover.isEmpty {
            line.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: leftover.count))
        }
        // Return submits like Add does. (A tap on Add straight after typing is
        // sometimes dropped on iPad while the keyboard settles.)
        line.typeText(meal + "\n")
        let refusal = app.staticTexts["End the line with a calorie count, e.g. \"rice and chicken 600\""]
        // CI's simulator sometimes drops the Return, so fall back to Add as the weight row does.
        let add = app.buttons["Add calories"]
        if !refusal.waitForExistence(timeout: 3), add.isEnabled { add.tap() }
        XCTAssertTrue(refusal.waitForExistence(timeout: 5))
        line.tap()
        line.typeText(", ~321")
        XCTAssertTrue(app.staticTexts["\(meal) — 321 kcal"].waitForExistence(timeout: 5))
        app.buttons["Add calories"].tap()
        XCTAssertTrue(app.staticTexts[meal].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["321 kcal"].exists)

        app.terminate()
        app.launch()
        XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 10))
        app.segmentedControls.buttons["Daily"].tap()
        XCTAssertTrue(app.staticTexts[weight].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Saved on this device · Waiting to sync"].exists)
        for _ in 0..<5 where !app.staticTexts[meal].exists { app.swipeUp() }
        XCTAssertTrue(app.staticTexts[meal].exists)
        app.segmentedControls.buttons["Entry"].tap()
        XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 5))
    }

    func testThePageSwitchStaysPutWithTheWeatherOnEveryPage() {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 10))
        let pages = app.segmentedControls.firstMatch
        let frame = pages.frame
        for page in ["Daily", "Workout", "Daily", "Entry"] {
            pages.buttons[page].tap()
            XCTAssertTrue(app.descendants(matching: .any)["current-weather"].waitForExistence(timeout: 5), page)
            XCTAssertEqual(pages.frame, frame, page)
        }
        // Only the chosen page is on screen.
        XCTAssertFalse(app.textFields["Exercise entry"].exists)
        XCTAssertFalse(app.staticTexts["Body weight"].exists)
    }

    func testWorkoutLogsSetsLikeTheDesktopWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 10))
        app.segmentedControls.buttons["Workout"].tap()
        let line = app.textFields["Exercise entry"]
        XCTAssertTrue(line.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Walking"].exists && app.buttons["Cycling"].exists)

        // The server's own refusal, before anything is queued.
        line.tap()
        line.typeText("curls 20 kg, 10\n")
        XCTAssertTrue(app.staticTexts["Use weight, reps (20, 10) or bodyweight reps (10)."].waitForExistence(timeout: 5))
        line.tap()
        line.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 20))

        // A named set, then a bare count that means the same exercise.
        let name = "lunge " + String((0..<5).map { _ in "abcdefghjkmnpqrstvwxyz".randomElement()! })
        line.typeText(name + " 10\n")
        XCTAssertTrue(app.buttons[name.capitalized].waitForExistence(timeout: 5))
        line.tap()
        line.typeText("12\n")
        for _ in 0..<5 where !app.staticTexts["\(name) · 12"].exists { app.swipeUp() }
        XCTAssertTrue(app.staticTexts["\(name) · 12"].exists)

        app.terminate()
        app.launch()
        XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 10))
        app.segmentedControls.buttons["Workout"].tap()
        for _ in 0..<5 where !app.staticTexts["\(name) 10"].exists { app.swipeUp() }
        XCTAssertTrue(app.staticTexts["\(name) 10"].exists)
        XCTAssertTrue(app.staticTexts["\(name) · 12"].exists)
        XCTAssertTrue(app.staticTexts["Waiting to sync"].exists)
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

    func testLibrarySwitchesToFoldersAndBackOutOfAFolder() {
        let app = XCUIApplication()
        app.launch()
        openMore(app, "Library")
        // Library mode: the providers, filtered by site.
        XCTAssertTrue(app.buttons["SpaceBattles"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["AO3"].exists)
        let mode = app.segmentedControls["library-mode"]
        XCTAssertTrue(mode.exists)
        mode.buttons["Folders"].tap()
        // Folders mode is a list of folders, with no provider pills.
        let unsorted = app.buttons["folder-unsorted"]
        XCTAssertTrue(unsorted.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["SpaceBattles"].exists)
        unsorted.tap()
        XCTAssertTrue(app.navigationBars["Unsorted"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.searchFields.firstMatch.exists)
        app.navigationBars["Unsorted"].buttons.firstMatch.tap()
        XCTAssertTrue(unsorted.waitForExistence(timeout: 5))
        // The choice is remembered: back to Library mode for the next test.
        app.segmentedControls["library-mode"].buttons["Library"].tap()
        XCTAssertTrue(app.buttons["SpaceBattles"].waitForExistence(timeout: 5))
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

    func testNotebookFillsTheWindowContinuesFromDrawAndSavesToTheJournal() {
        let app = XCUIApplication()
        app.launch()
        let notes = app.buttons["capture-notes"]
        guard UIDevice.current.userInterfaceIdiom == .pad else {
            XCTAssertTrue(app.textViews["Journal text"].waitForExistence(timeout: 10))
            XCTAssertFalse(notes.exists)
            XCTAssertFalse(app.buttons["capture-newspaper"].exists)
            return
        }
        XCTAssertTrue(notes.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["capture-newspaper"].exists)
        notes.tap()
        XCTAssertTrue(app.buttons["notebook-save"].waitForExistence(timeout: 10))
        // Full window: no tab is left to tap.
        let journalTabs = app.descendants(matching: .any).matching(identifier: "Journal").allElementsBoundByIndex
        XCTAssertFalse(journalTabs.contains { $0.isHittable })

        app.buttons["notebook-add-page"].tap()
        XCTAssertEqual(app.buttons["notebook-page"].label, "2 / 2")
        app.buttons["notebook-youtube"].tap()
        let link = app.alerts.textFields["YouTube video URL"]
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        link.typeText("https://youtu.be/M7lc1UVf-VE")
        app.alerts.buttons["Add"].tap()
        XCTAssertTrue(app.buttons["YouTube video added"].waitForExistence(timeout: 5))

        // Back saves it; Draw lists it to continue.
        app.navigationBars.buttons.element(boundBy: 0).tap()
        tab(app, "Draw").tap()
        let row = app.staticTexts.containing(NSPredicate(format: "label CONTAINS '2 pages'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Not in the journal yet"].firstMatch.exists)
        row.tap()
        XCTAssertTrue(app.buttons["notebook-save"].waitForExistence(timeout: 10))
        XCTAssertEqual(app.buttons["notebook-page"].label, "1 / 2")
        app.buttons["notebook-save"].tap()
        // Saving goes back to Draw, where it now reads as filed.
        XCTAssertTrue(app.staticTexts["Saved to journal"].firstMatch.waitForExistence(timeout: 10)
                      || app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'Saved to journal'")).firstMatch.waitForExistence(timeout: 5))

        tab(app, "Journal").tap()
        XCTAssertTrue(app.staticTexts["https://www.youtube.com/watch?v=M7lc1UVf-VE"].firstMatch.waitForExistence(timeout: 10))
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
        // On a freshly booted CI simulator the Photos service can take well over 10 s to start.
        guard photo.waitForExistence(timeout: 30) else { return XCTFail("Photo picker did not open") }
        // Out-of-process cells report themselves unhittable; tap where one is drawn.
        photo.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let done = app.buttons["Done"]
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true"),
                                                                      object: done)], timeout: 5) == .completed)
        done.tap()
        // ...and to hand the picked photo over (CI has stalled ~40 s on Add).
        XCTAssertTrue(app.staticTexts["Attachments"].waitForExistence(timeout: 60))
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

    func testTodoWorksWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        selectTab(app, "Todo")
        // No server, said beside the title rather than above the lists.
        let status = app.buttons["todo-status"]
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        XCTAssertTrue(status.label.contains("No server"), status.label)
        XCTAssertTrue(app.navigationBars["Todo"].buttons["todo-status"].exists, "On the title's line")
        // The badge's number is covered by Core's TodoTests: XCUITest reads a
        // tab's badge as its value on some simulators and not on others.
        // Earlier runs on this simulator may have left changes waiting.
        func waiting() -> Int {
            Int(status.label.components(separatedBy: " · ").last?.components(separatedBy: " ").first ?? "") ?? 0
        }
        let dailyRows = app.buttons.matching(identifier: "todo-daily-row")
        func deleteADailyTask() {
            let before = dailyRows.count
            dailyRows.firstMatch.swipeLeft()
            app.buttons["Delete"].tap()
            XCTAssertEqual(dailyRows.count, before - 1)
        }
        if !app.textFields["todo-daily-input"].exists { deleteADailyTask() }
        let queued = waiting()

        // A daily task goes on the list at once and waits for the server.
        let daily = app.textFields["todo-daily-input"]
        let task = "Stretch \(Int.random(in: 1000...9999))"
        focus(daily)
        daily.typeText(task)
        app.buttons["todo-daily-add"].tap()
        XCTAssertTrue(app.buttons[task].waitForExistence(timeout: 5))
        XCTAssertEqual(daily.value as? String ?? "", "Add a daily task", "The field clears")

        // So does a to-do due today.
        let name = "Call the dentist \(Int.random(in: 1000...9999))"
        app.buttons["todo-add"].tap()
        let title = app.textFields["todo-editor-title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        focus(title)
        title.typeText(name)
        // A tap on the switch's middle lands on its label; flip the toggle itself.
        app.switches["Due date"].coordinate(withNormalizedOffset: CGVector(dx: 0.93, dy: 0.5)).tap()
        XCTAssertTrue(app.datePickers.firstMatch.waitForExistence(timeout: 3), "Due today")
        app.buttons["todo-editor-save"].tap()
        let row = app.buttons.matching(NSPredicate(format: "identifier == 'todo-row' AND label BEGINSWITH %@", name)).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        XCTAssertEqual(waiting(), queued + 2, status.label)

        // Both survive a relaunch, still waiting.
        app.terminate()
        app.launch()
        selectTab(app, "Todo")
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons[task].exists)
        XCTAssertEqual(waiting(), queued + 2, status.label)

        // Ticking it off takes it off the list: its checkbox is the one beside it.
        let check = app.buttons.matching(identifier: "todo-check").allElementsBoundByIndex
            .min { abs($0.frame.midY - row.frame.minY) < abs($1.frame.midY - row.frame.minY) }
        check?.tap()
        XCTAssertTrue(row.waitForNonExistence(timeout: 5))

        // Deleting leaves room for the next run under the four-task cap.
        app.buttons[task].swipeLeft()
        app.buttons["Delete"].tap()
        XCTAssertTrue(app.buttons[task].waitForNonExistence(timeout: 5))
    }

    func testChatKeepsAVoiceMessageForTheServerWithoutOne() {
        let app = XCUIApplication()
        addUIInterruptionMonitor(withDescription: "Microphone") { alert in
            let allow = alert.buttons["Allow"]
            guard allow.exists else { return false }
            allow.tap()
            return true
        }
        app.launch()
        selectTab(app, "Chat")
        // No server: it says so, and a typed message has nowhere to go yet.
        XCTAssertTrue(app.staticTexts["chat-problem"].waitForExistence(timeout: 10))
        let input = app.textFields["chat-input"]
        input.tap()
        input.typeText("what did I eat")
        app.buttons["chat-send"].tap()
        XCTAssertTrue(app.staticTexts["Connect to your server to send a typed message."].waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "what did I eat", "The words stay in the box")

        // A voice message records anyway and waits on the phone.
        let record = app.buttons["chat-record"]
        record.tap()
        let stop = app.buttons["Stop and send"]
        if !stop.waitForExistence(timeout: 3) { app.tap() }
        XCTAssertTrue(stop.waitForExistence(timeout: 10))
        Thread.sleep(forTimeInterval: 1.5)
        stop.tap()
        let pending = app.staticTexts["chat-recording-pending"]
        XCTAssertTrue(pending.waitForExistence(timeout: 5))
        // The typed words went with the clip.
        XCTAssertNotEqual(input.value as? String, "what did I eat")

        app.terminate()
        app.launch()
        selectTab(app, "Chat")
        XCTAssertTrue(app.staticTexts["chat-recording-pending"].waitForExistence(timeout: 10), "The clip survives a relaunch")
    }
}
