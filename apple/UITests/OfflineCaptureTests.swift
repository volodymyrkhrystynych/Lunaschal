import XCTest
import UIKit

final class OfflineCaptureTests: XCTestCase {
    /// How long to wait for the chrome (tabs, the More menu) after a launch or
    /// a navigation. CI's simulators answer each query in seconds, so a 10 s
    /// wait could give up after two looks; waits return as soon as they're met.
    private let settle: TimeInterval = 30

    /// `isHittable` on an element mid-transition (no frame yet) fails the test
    /// outright with "Activation point invalid" rather than returning false.
    private func reachable(_ element: XCUIElement) -> Bool {
        element.exists && !element.frame.isEmpty && element.isHittable
    }

    private func tab(_ app: XCUIApplication, _ name: String) -> XCUIElement {
        if UIDevice.current.userInterfaceIdiom == .phone {
            return app.tabBars.buttons[name]
        }
        // iPad's floating tabs are exposed as cells/other elements, not a TabBar,
        // and each tab appears twice: firstMatch can be the copy that is never
        // hittable. Prefer the one that is, once it shows up.
        let matches = app.descendants(matching: .any).matching(identifier: name)
        let deadline = Date().addingTimeInterval(settle)
        repeat {
            if let visible = matches.allElementsBoundByIndex.first(where: reachable) { return visible }
            RunLoop.current.run(until: Date().addingTimeInterval(0.5))
        } while Date() < deadline && matches.count > 0
        return matches.firstMatch
    }

    private func selectTab(_ app: XCUIApplication, _ name: String,
                           file: StaticString = #filePath, line: UInt = #line) {
        let item = tab(app, name)
        let hittable = NSPredicate(format: "exists == true AND hittable == true")
        guard XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: hittable, object: item)],
                            timeout: settle) == .completed else {
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

    // A form's Save, just after typing: on CI's slow simulator the tap can land
    // before Save enables, or be swallowed, and the form stays open with the
    // keyboard up. Wait for it to enable and retry until the form closes.
    private func saveForm(_ app: XCUIApplication, _ title: String,
                          file: StaticString = #filePath, line: UInt = #line) {
        let bar = app.navigationBars[title]
        let save = bar.buttons["Save"]
        _ = XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: save)],
                           timeout: settle)
        for _ in 0..<3 {
            save.tap()
            if bar.waitForNonExistence(timeout: 5) { return }
        }
        XCTFail("\(title) stayed open after Save", file: file, line: line)
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
                            timeout: settle) == .completed else {
            XCTFail("More did not become tappable", file: file, line: line)
            return
        }
        let row = app.buttons["more-\(name)"]
        // The menu stays in the tree under a pushed screen, so wait for the row
        // to be tappable, not merely present. As in selectTab, retry a tap that
        // the previous animation swallowed. An expectation can be waited on
        // only once, so each retry gets its own.
        for _ in 0..<2 where !reachable(row) {
            more.tap()
            let back = app.navigationBars.buttons["More"]
            if back.waitForExistence(timeout: 2) { back.tap() }
            _ = XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: hittable, object: row)], timeout: 3)
        }
        if !reachable(row) {
            _ = XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: hittable, object: row)], timeout: 5)
        }
        XCTAssertTrue(reachable(row), "No \(name) in More", file: file, line: line)
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

    func testLearningOpensFromMoreAndAsksForTheServer() {
        let app = XCUIApplication()
        app.launch()
        openMore(app, "Learning")
        // Review, Queue and Browse all need the server; without one the
        // screen says so rather than showing an empty deck.
        XCTAssertTrue(app.segmentedControls["learning-page"].waitForExistence(timeout: 5))
        for page in ["Review", "Queue", "Browse"] {
            XCTAssertTrue(app.segmentedControls["learning-page"].buttons[page].exists, "Missing \(page)")
        }
        XCTAssertTrue(app.staticTexts["Learning needs your server"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["learning-check"].exists)
    }

    private func snapshot(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    /// The whole review against `-learningFixture`'s stand-in server: answer
    /// one card, flip the rest, see the grade land, rate them all; then the
    /// queue's duplicate prompt and the browser.
    func testLearningReviewsTheFixtureDeck() {
        let app = XCUIApplication()
        app.launchArguments.append("-learningFixture")
        app.launch()
        openMore(app, "Learning")

        let field = app.textFields["learning-answer"]
        XCTAssertTrue(field.waitForExistence(timeout: settle), "No card to answer")
        XCTAssertTrue(app.staticTexts["Card 1 of 6"].exists)
        snapshot(app, "1-answer")
        focus(field)
        field.typeText("It runs code on the main thread")
        app.buttons["learning-check"].tap()
        XCTAssertTrue(app.staticTexts["Card 2 of 6"].waitForExistence(timeout: 5))
        for _ in 0..<5 { app.buttons["learning-flip"].tap(); RunLoop.current.run(until: Date().addingTimeInterval(0.5)) }

        XCTAssertTrue(app.staticTexts["Result 1 of 6"].waitForExistence(timeout: 5))
        // The stand-in grades about a second after the answer: one of two claims.
        XCTAssertTrue(app.staticTexts["Partly right."].waitForExistence(timeout: 10), "The grade never landed")
        snapshot(app, "2-result")
        app.buttons["learning-rate-Good"].tap()
        for n in 2...6 {
            XCTAssertTrue(app.staticTexts["Result \(n) of 6"].waitForExistence(timeout: 5))
            app.buttons["learning-rate-Easy"].tap()
        }
        XCTAssertTrue(app.staticTexts["All caught up!"].waitForExistence(timeout: 5))

        app.segmentedControls["learning-page"].buttons.element(boundBy: 1).tap()
        let approve = app.buttons["Approve"].firstMatch
        XCTAssertTrue(approve.waitForExistence(timeout: 5))
        snapshot(app, "3-queue")
        app.swipeUp()
        app.buttons.matching(identifier: "Approve").element(boundBy: 2).tap()
        XCTAssertTrue(app.staticTexts["Similar card exists"].waitForExistence(timeout: 5), "No duplicate prompt")
        snapshot(app, "4-duplicate")
        app.buttons["Keep both"].tap()

        // An approved card is due at once, so it's back on Review's count.
        XCTAssertTrue(app.segmentedControls["learning-page"].buttons["Review (1)"].waitForExistence(timeout: 5),
                      "The approved card isn't due")
        app.segmentedControls["learning-page"].buttons["Browse"].tap()
        XCTAssertTrue(app.staticTexts["#concurrency"].waitForExistence(timeout: 5), "Browse is empty")
        snapshot(app, "5-browse")
    }

    /// Speech mode is switched in Settings, and an answer given with it on
    /// comes back with a summary to read aloud and a Replay button.
    func testLearningSpeechModeIsASettingAndOffersReplay() {
        let app = XCUIApplication()
        app.launchArguments.append("-learningFixture")
        app.launch()
        openMore(app, "Settings")
        let toggle = app.switches["settings-learning-speech"]
        for _ in 0..<6 where !reachable(toggle) { app.swipeUp() }
        XCTAssertTrue(toggle.waitForExistence(timeout: 5), "No speech mode switch in Settings")
        if (toggle.value as? String) != "1" { toggle.switches.firstMatch.tap() }
        XCTAssertEqual(toggle.value as? String, "1")

        openMore(app, "Learning")
        let field = app.textFields["learning-answer"]
        XCTAssertTrue(field.waitForExistence(timeout: settle))
        focus(field)
        field.typeText("It runs code on the main thread")
        app.buttons["learning-check"].tap()
        XCTAssertTrue(app.staticTexts["Card 2 of 6"].waitForExistence(timeout: 5))
        for _ in 0..<5 { app.buttons["learning-flip"].tap(); RunLoop.current.run(until: Date().addingTimeInterval(0.5)) }
        XCTAssertTrue(app.buttons["learning-replay"].waitForExistence(timeout: 10), "No read-aloud for a speech-mode answer")
        snapshot(app, "speech-mode-result")
        // A flipped card has nothing to read.
        app.buttons["learning-rate-Good"].tap()
        XCTAssertTrue(app.staticTexts["Result 2 of 6"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["learning-replay"].exists)
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

    func testJournalHasSyncOnTheLeftAndSwitchesToTheCalendar() {
        let app = XCUIApplication()
        app.launch()
        selectTab(app, "Journal")
        let bar = app.navigationBars.firstMatch
        let sync = bar.buttons["Sync"]
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(sync.waitForExistence(timeout: 5))
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        XCTAssertLessThan(sync.frame.midX, bar.frame.midX)
        XCTAssertGreaterThan(pages.frame.midX, bar.frame.midX)
        pages.buttons["Calendar"].tap()
        XCTAssertTrue(app.buttons["calendar-day"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["calendar-day"].label, "Today")
        XCTAssertTrue(app.staticTexts["12am"].exists, "the timeline runs past midnight")
        XCTAssertTrue(sync.exists)
        pages.buttons["Journal"].tap()
        XCTAssertTrue(app.searchFields["Search saved journal"].waitForExistence(timeout: 5))
    }

    /// Delete sits at the foot of the Edit form, as the event's page no longer has it.
    private func deleteFromEdit(_ app: XCUIApplication, title: String) {
        app.navigationBars[title].buttons["Edit"].tap()
        let delete = app.buttons["calendar-event-delete"]
        XCTAssertTrue(app.navigationBars["Edit event"].waitForExistence(timeout: 5))
        for _ in 0..<6 where !delete.isHittable { app.swipeUp() }
        delete.tap()
        let confirm = app.sheets.buttons["Delete event"].exists ? app.sheets.buttons["Delete event"] : app.buttons["Delete event"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(app.buttons["calendar-new-event"].waitForExistence(timeout: 5))
    }

    func testCalendarDayViewCreatesAnEventWithoutAServer() {
        // The simulator keeps what earlier runs queued, so this run's event is its own.
        let name = "Dentist " + UUID().uuidString.prefix(8)
        let app = XCUIApplication()
        app.launch()
        selectTab(app, "Journal")
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        pages.buttons["Calendar"].tap()
        let add = app.buttons["calendar-new-event"]
        XCTAssertTrue(add.waitForExistence(timeout: 5))
        add.tap()
        let title = app.textFields["calendar-event-title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        let save = app.navigationBars["New event"].buttons["Save"]
        XCTAssertFalse(save.isEnabled, "nothing to save without a title")
        focus(title)
        title.typeText(name)
        saveForm(app, "New event")
        // Saved on the device and drawn on today's timeline at once.
        let event = app.buttons.matching(identifier: "calendar-event").matching(NSPredicate(format: "label BEGINSWITH %@", name)).firstMatch
        XCTAssertTrue(event.waitForExistence(timeout: 5))
        // The element's frame includes its label, so the middle of it can be
        // empty space; tap the line's foot, as the overlap test does.
        func tapLine(_ element: XCUIElement) {
            element.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: 14, dy: element.frame.height - 16)).tap()
        }
        tapLine(event)
        XCTAssertTrue(app.navigationBars[name].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Saved on device · Waiting to sync"].exists)
        app.navigationBars[name].buttons.firstMatch.tap()
        // Survives a relaunch: it's in the outbox, not just on screen.
        app.terminate()
        app.launch()
        selectTab(app, "Journal")
        XCTAssertTrue(app.segmentedControls["journal-page"].waitForExistence(timeout: 5))
        if !app.buttons["calendar-new-event"].exists { app.segmentedControls["journal-page"].buttons["Calendar"].tap() }
        XCTAssertTrue(event.waitForExistence(timeout: 5))

        // Edit it, as the web's event details do.
        tapLine(event)
        app.navigationBars[name].buttons["Edit"].tap()
        let field = app.textFields["calendar-event-title"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        XCTAssertEqual(field.value as? String, name, "the form opens on the event")
        focus(field)
        field.typeText(" checkup")
        saveForm(app, "Edit event")
        let edited = app.buttons.matching(identifier: "calendar-event")
            .matching(NSPredicate(format: "label BEGINSWITH %@", name + " checkup")).firstMatch
        XCTAssertTrue(edited.waitForExistence(timeout: 5))

        // And delete it, from inside Edit.
        tapLine(edited)
        XCTAssertFalse(app.buttons["calendar-event-delete"].exists, "Delete lives in Edit now")
        deleteFromEdit(app, title: name + " checkup")
        XCTAssertFalse(app.buttons.matching(identifier: "calendar-event")
            .matching(NSPredicate(format: "label BEGINSWITH %@", name)).firstMatch.exists)
    }

    func testCalendarEventsOverlapTakeCategoriesAndDragWithoutAServer() {
        let tag = String(UUID().uuidString.prefix(6))
        let app = XCUIApplication()
        app.launch()
        selectTab(app, "Journal")
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        pages.buttons["Calendar"].tap()
        // Tomorrow, so "+" puts both at 8am rather than near the clock.
        let next = app.buttons["Next day"]
        XCTAssertTrue(next.waitForExistence(timeout: 5))
        next.tap()

        func create(_ name: String, category: String?) {
            app.buttons["calendar-new-event"].tap()
            let title = app.textFields["calendar-event-title"]
            XCTAssertTrue(title.waitForExistence(timeout: 5))
            focus(title)
            title.typeText(name)
            if let category {
                let box = app.buttons["calendar-category-\(category)"]
                for _ in 0..<6 where !box.isHittable { app.swipeUp() }
                box.tap()
                XCTAssertTrue(box.isSelected)
            }
            saveForm(app, "New event")
        }
        func event(_ name: String) -> XCUIElement {
            app.buttons.matching(identifier: "calendar-event").matching(NSPredicate(format: "label BEGINSWITH %@", name)).firstMatch
        }
        // The line itself, near its foot: an event's frame also takes in its
        // label, which can sit over a neighbouring event.
        func line(_ element: XCUIElement) -> XCUICoordinate {
            element.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: 14, dy: element.frame.height - 16))
        }
        let long = "Long " + tag, short = "Short " + tag
        create(long, category: "work")
        create(short, category: nil)
        XCTAssertTrue(event(long).waitForExistence(timeout: 5))
        XCTAssertTrue(event(short).waitForExistence(timeout: 5))
        // Same hours, side by side: the lines end on the same row, in different lanes.
        XCTAssertEqual(event(long).frame.maxY, event(short).frame.maxY, accuracy: 1)
        XCTAssertNotEqual(event(long).frame.minX, event(short).frame.minX)

        // The category ticked in the New form is on the event, and the
        // event's own page ticks more without opening Edit.
        line(event(long)).tap()
        let work = app.buttons["calendar-category-work"]
        XCTAssertTrue(work.waitForExistence(timeout: 5))
        XCTAssertTrue(work.isSelected)
        let outside = app.buttons["calendar-category-outside"]
        for _ in 0..<4 where !outside.isHittable { app.swipeUp() }
        XCTAssertFalse(outside.isSelected)
        outside.tap()
        XCTAssertTrue(outside.isSelected, "saved on the device at once")
        XCTAssertTrue(app.staticTexts["Saved on device · Waiting to sync"].exists)
        app.navigationBars[long].buttons["Edit"].tap()
        XCTAssertTrue(app.navigationBars["Edit event"].waitForExistence(timeout: 5))
        // The page behind the sheet still has its own; Edit adds none.
        XCTAssertEqual(app.buttons.matching(identifier: "calendar-category-work").count, 1, "categories aren't in Edit")
        app.navigationBars["Edit event"].buttons["Cancel"].tap()
        XCTAssertTrue(outside.waitForExistence(timeout: 5))
        XCTAssertTrue(outside.isSelected)
        app.navigationBars[long].buttons.firstMatch.tap()

        // The toggle at the bottom left makes a drag change the length.
        let mode = app.buttons["calendar-drag-mode"]
        XCTAssertTrue(mode.waitForExistence(timeout: 5))
        mode.tap()
        XCTAssertEqual(mode.value as? String, "changes its length")
        let target = event(long)
        let before = target.frame
        line(target).press(forDuration: 0.3, thenDragTo: line(target).withOffset(CGVector(dx: 0, dy: 120)))
        let longer = event(long)
        XCTAssertTrue(longer.waitForExistence(timeout: 5))
        XCTAssertGreaterThan(longer.frame.maxY, before.maxY + 100, "a length drag moves the end")
        XCTAssertTrue(longer.label.contains("08:00 – 09:"), longer.label)

        // Back to moving: the short one goes an hour later, keeping its length.
        mode.tap()
        XCTAssertEqual(mode.value as? String, "moves it")
        let moving = event(short)
        line(moving).press(forDuration: 0.3, thenDragTo: line(moving).withOffset(CGVector(dx: 0, dy: 120)))
        let moved = app.buttons.matching(identifier: "calendar-event")
            .matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS %@", short, "09:")).firstMatch
        XCTAssertTrue(moved.waitForExistence(timeout: 5), event(short).label)
        XCTAssertGreaterThan(moved.frame.maxY, before.maxY + 100)

        // Wake time set by hand, offline: the morning is shaded at once.
        app.buttons["calendar-sleep"].tap()
        let wake = app.switches["sleep-set-wake"]
        XCTAssertTrue(wake.waitForExistence(timeout: 5))
        if (wake.value as? String) != "1" { wake.switches.firstMatch.exists ? wake.switches.firstMatch.tap() : wake.tap() }
        saveForm(app, "Sleep")
        XCTAssertTrue(app.descendants(matching: .any)["sleep-band-morning"].waitForExistence(timeout: 5))

        // Leave the day as it was found: the simulator keeps what runs queue.
        for name in [long, short] {
            line(event(name)).tap()
            deleteFromEdit(app, title: name)
        }
        app.buttons["calendar-sleep"].tap()
        if (wake.value as? String) == "1" { wake.switches.firstMatch.exists ? wake.switches.firstMatch.tap() : wake.tap() }
        saveForm(app, "Sleep")
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

    // With no server the feed still opens, with the sort and a hint to sign
    // in rather than an error alert. Whether it is empty depends on whether
    // the fixture test ran first in this simulator, so that isn't checked.
    func testJobsFeedOpensFromMoreWithoutAServer() {
        let app = XCUIApplication()
        app.launch()
        openMore(app, "Jobs")
        XCTAssertTrue(app.segmentedControls["jobs-sort"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Sign in under More → Settings to load new postings."].waitForExistence(timeout: 5))
        app.segmentedControls["jobs-sort"].buttons["Nearest"].tap()
        XCTAssertTrue(app.staticTexts["Remote first, then nearest."].waitForExistence(timeout: 5))
    }

    // The fixture's five postings: strong, possible and an untriaged one that
    // scores well above the line; two stretches below it, out of sight. Queue
    // takes a card away at once, and with no server it waits in the outbox.
    func testJobsFeedGroupsCardsAndQueuesOffline() {
        let app = XCUIApplication()
        app.launchArguments.append("-jobsFeedFixture")
        app.launch()
        openMore(app, "Jobs")
        XCTAssertTrue(app.staticTexts["Senior iOS Engineer"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Worth a look (3)"].exists)
        XCTAssertTrue(app.staticTexts["Worth applying"].exists)
        XCTAssertTrue(app.staticTexts["2.4 km from Union Station"].exists)

        app.buttons["job-queue-01K7ZZZZZZZZZZZZZZZZZZZZJ1"].tap()
        XCTAssertTrue(app.staticTexts["Senior iOS Engineer"].waitForNonExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Worth a look (2)"].waitForExistence(timeout: 5))
        // The footer sits under the last card, so it is drawn only once scrolled to.
        let waiting = app.staticTexts["1 decision waiting to reach the server."]
        for _ in 0..<4 where !waiting.exists { app.swipeUp() }
        XCTAssertTrue(waiting.waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["The rest (2)"].exists)
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

    /// The filter menu has no tag picker (fics bring hundreds of user tags),
    /// the server refresh sits beside it, and only a fic from a site can be
    /// asked to update.
    func testLibraryOffersServerUpdatesButNoTagFilter() {
        let app = XCUIApplication()
        app.launchArguments.append("-libraryFixture")
        app.launch()
        openMore(app, "Library")
        XCTAssertTrue(app.buttons["Refresh library on server"].waitForExistence(timeout: 10))
        app.buttons["Filter books"].tap()
        XCTAssertTrue(app.buttons["Reset filters"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["All tags"].exists)
        XCTAssertFalse(app.staticTexts["All tags"].exists)
        app.buttons["Reset filters"].tap()

        let forumFic = app.staticTexts["Ashes of the Old Guard"]
        XCTAssertTrue(forumFic.waitForExistence(timeout: 10))
        forumFic.press(forDuration: 1.2)
        XCTAssertTrue(app.buttons["Check for updates"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Re-read edited chapters"].exists)
    }

    /// Settings → Library downloads imports a fic by its link: a page that
    /// isn't one is turned away on the phone, and a fic link needs a server.
    func testSettingsImportsAFicByLink() {
        let app = XCUIApplication()
        app.launch()
        openMore(app, "Settings")
        let downloads = app.buttons["Library downloads"]
        XCTAssertTrue(downloads.waitForExistence(timeout: 5))
        downloads.tap()
        let field = app.textFields["fic-import-link"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Paste"].exists)
        let importButton = app.buttons["Import"]
        XCTAssertFalse(importButton.isEnabled, "nothing to import yet")

        field.tap()
        field.typeText("https://example.com/threads/1")
        importButton.tap()
        XCTAssertTrue(app.alerts.staticTexts.containing(NSPredicate(format: "label BEGINSWITH %@", "That isn’t a link to a fic"))
            .firstMatch.waitForExistence(timeout: 5))
        app.alerts.buttons["OK"].tap()

        // At the end of the text, so the deletes take all of it.
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.98, dy: 0.5)).tap()
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 40))
        field.typeText("https://archiveofourown.org/works/42")
        importButton.tap()
        XCTAssertTrue(app.alerts.staticTexts["Sign in to import fics."].waitForExistence(timeout: 5))
    }

    /// Opening a fic that isn't on the device downloads it in front of
    /// everything else, saying so, until its chapters are there to read. The
    /// fixture's stand-in server answers slowly so the progress can be seen.
    func testOpeningAFicThatIsNotDownloadedShowsItDownloading() {
        let app = XCUIApplication()
        app.launchArguments.append("-libraryFixture")
        app.launch()
        openMore(app, "Library")
        let book = app.staticTexts["Ashes of the Old Guard"]
        XCTAssertTrue(book.waitForExistence(timeout: 10))
        book.tap()
        let progress = app.descendants(matching: .any)["fic-download-progress"]
        XCTAssertTrue(progress.waitForExistence(timeout: 10), "The book says it is downloading")
        let finished = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: progress)
        XCTAssertEqual(XCTWaiter.wait(for: [finished], timeout: 60), .completed, "The download finishes")
        XCTAssertTrue(app.staticTexts["Chapter 1"].exists, "and its chapters are there to read")

        // Back in the list it is marked as downloaded, as the one already on
        // the device is, and the half-downloaded one isn't.
        app.navigationBars.buttons.firstMatch.tap()
        func badge(_ title: String) -> XCUIElement {
            app.cells.containing(.staticText, identifier: title).descendants(matching: .any)["book-downloaded"]
        }
        XCTAssertTrue(badge("Ashes of the Old Guard").waitForExistence(timeout: 5))
        XCTAssertTrue(badge("The Long Way Round").exists)
        XCTAssertFalse(badge("Field Notes on Dragons").exists)

        // A fic the server can't send says so, with a way to try again.
        let missing = app.staticTexts["Deleted Upstream"]
        if !missing.waitForExistence(timeout: 5) || !missing.isHittable { app.swipeUp() }
        missing.tap()
        XCTAssertTrue(app.buttons["Try again"].waitForExistence(timeout: 15))
    }

    /// The continue point can be moved again before the last move has synced:
    /// with no server, nothing ever syncs, so the second move used to be refused.
    func testContinuePointMovesAgainBeforeItSyncs() {
        let app = XCUIApplication()
        app.launchArguments.append("-libraryFixture")
        app.launch()
        openMore(app, "Library")
        let book = app.staticTexts["The Long Way Round"]
        XCTAssertTrue(book.waitForExistence(timeout: 10))
        book.tap()
        let menu = app.buttons["reader-menu"]
        let saved = app.staticTexts["Continue point saved here · Syncs when connected"]
        for _ in 0..<2 {
            XCTAssertTrue(menu.waitForExistence(timeout: 10))
            menu.tap()
            let item = app.buttons["Continue"]
            XCTAssertTrue(item.waitForExistence(timeout: 5))
            item.tap()
            XCTAssertTrue(saved.waitForExistence(timeout: 5))
            XCTAssertFalse(app.alerts.firstMatch.exists, "a second move isn't refused")
            if app.buttons["Next"].exists { app.buttons["Next"].tap() }
        }
    }

    /// A chapter reads as formatted text, its size is kept on the device,
    /// and the end of the chapter clears the floating menu and the tab bar.
    func testChapterIsFormattedSizedAndClearsTheBottomControls() {
        let app = XCUIApplication()
        app.launchArguments.append("-libraryFixture")
        app.launch()
        openMore(app, "Library")
        let book = app.staticTexts["The Long Way Round"]
        XCTAssertTrue(book.waitForExistence(timeout: 10))
        book.tap()
        let size = app.buttons["reader-text-size"]
        XCTAssertTrue(size.waitForExistence(timeout: 10))
        attach(app, "chapter")
        XCTAssertFalse(app.staticTexts["Reading position stays on this device."].exists, "no status bar")

        size.tap()
        let larger = app.buttons["reader-text-larger"]
        XCTAssertTrue(larger.waitForExistence(timeout: 5))
        larger.tap(); larger.tap()
        app.tap() // close the menu
        XCTAssertEqual(size.value as? String, "23 points")
        attach(app, "larger text")

        app.terminate()
        app.launch()
        openMore(app, "Library")
        XCTAssertTrue(book.waitForExistence(timeout: 10))
        book.tap()
        XCTAssertTrue(size.waitForExistence(timeout: 10))
        XCTAssertEqual(size.value as? String, "23 points", "the size is kept on the device")

        // The scene break in the middle of the chapter is a line of its own.
        let sceneBreak = app.staticTexts["* * *"]
        for _ in 0..<12 where !sceneBreak.exists { app.swipeUp() }
        XCTAssertTrue(sceneBreak.exists)

        let next = app.buttons["Next chapter"]
        for _ in 0..<40 where !(next.exists && next.isHittable) { app.swipeUp() }
        app.swipeUp(); app.swipeUp()
        XCTAssertTrue(next.isHittable)
        let menu = app.buttons["reader-menu"]
        XCTAssertFalse(next.frame.intersects(menu.frame), "Next clears the menu button")
        let previous = app.buttons["Previous"]
        if previous.exists { XCTAssertFalse(previous.frame.intersects(menu.frame), "Previous clears the menu button") }
        if app.tabBars.firstMatch.exists {
            XCTAssertLessThanOrEqual(next.frame.maxY, app.tabBars.firstMatch.frame.minY, "Next clears the tab bar")
        }
        attach(app, "end of chapter")

        size.tap()
        XCTAssertTrue(app.buttons["Default size"].waitForExistence(timeout: 5))
        app.buttons["Default size"].tap()
        app.tap()
        XCTAssertEqual(size.value as? String, "19 points")
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
        detachDraftNotes(app)
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

        // Back saves it; Draw lists it to continue, as the entry draft's notes.
        app.navigationBars.buttons.element(boundBy: 0).tap()
        tab(app, "Draw").tap()
        let row = app.staticTexts.containing(NSPredicate(format: "label CONTAINS '2 pages'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["In the journal draft"].firstMatch.exists)
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

    func testNotesComeBackFromTheEntryDraftAndSaveWithIt() {
        let app = XCUIApplication()
        app.launch()
        let notes = app.buttons["capture-notes"]
        guard UIDevice.current.userInterfaceIdiom == .pad else { return }
        XCTAssertTrue(notes.waitForExistence(timeout: 10))
        detachDraftNotes(app)
        notes.tap()
        XCTAssertTrue(app.buttons["notebook-save"].waitForExistence(timeout: 10))
        app.buttons["notebook-add-page"].tap()
        XCTAssertEqual(app.buttons["notebook-page"].label, "2 / 2")

        // Back, then Notes again: the same two pages, not a fresh notebook.
        app.navigationBars.buttons.element(boundBy: 0).tap()
        let row = app.buttons["capture-draft-notes"]
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        notes.tap()
        XCTAssertTrue(app.buttons["notebook-save"].waitForExistence(timeout: 10))
        XCTAssertEqual(app.buttons["notebook-page"].label.hasSuffix("/ 2"), true)
        XCTAssertEqual(app.buttons["notebook-save"].label, "Save entry")
        app.navigationBars.buttons.element(boundBy: 0).tap()
        // The row opens them too.
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        XCTAssertTrue(app.buttons["notebook-save"].waitForExistence(timeout: 10))
        XCTAssertEqual(app.buttons["notebook-page"].label.hasSuffix("/ 2"), true)
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // Saving the entry takes the notes with it; the next Notes is a new notebook.
        let editor = app.textViews["Journal text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.tap()
        editor.typeText("Evening notes")
        app.buttons["Save entry"].tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 10))
        XCTAssertFalse(row.exists)
        notes.tap()
        XCTAssertTrue(app.buttons["notebook-save"].waitForExistence(timeout: 10))
        XCTAssertEqual(app.buttons["notebook-page"].label, "1 / 1")
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

    /// An entry a sync brings in above where the reader is doesn't move what
    /// they're reading: the feed holds its place by the card at the top.
    func testAnEntryArrivingAboveKeepsTheFeedWhereItWas() {
        let app = XCUIApplication()
        app.launchArguments += ["-journalFeedFixture", "-journalFeedArrival"]
        app.launch()
        selectTab(app, "Journal")
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        pages.buttons["Journal"].tap()
        XCTAssertTrue(app.staticTexts["Fixture later"].waitForExistence(timeout: 10))

        // Down among the older entries; whichever is on screen is the one being read.
        app.swipeUp(); app.swipeUp()
        let filler = NSPredicate(format: "label BEGINSWITH 'Filler '")
        let onScreen = app.staticTexts.matching(filler).allElementsBoundByIndex.filter { $0.isHittable }
        XCTAssertGreaterThan(onScreen.count, 1, "scrolled down to the older entries")
        guard onScreen.count > 1 else { return }
        let reading = app.staticTexts[onScreen[1].label]
        let before = reading.frame.minY

        app.buttons["fixture-arrive"].tap()
        // Give the reload time to land and lay out.
        let moved = NSPredicate(format: "frame.minY != %f", before)
        _ = XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: moved, object: reading)], timeout: 3)
        XCTAssertEqual(reading.frame.minY, before, accuracy: 2, "the card being read stayed put")

        // And the entry really did arrive, at the top.
        for _ in 0..<10 where !app.staticTexts["Fixture arrival"].exists { app.swipeDown() }
        XCTAssertTrue(app.staticTexts["Fixture arrival"].exists)
    }

    /// The feed reads like the desktop's: the event's border around what was
    /// written during it, and the entry's photo, clip and video on its card.
    func testJournalFeedShowsMediaInsideTheCalendarBorder() {
        let app = XCUIApplication()
        app.launchArguments.append("-journalFeedFixture")
        app.launch()
        selectTab(app, "Journal")
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        pages.buttons["Journal"].tap()

        let group = app.descendants(matching: .any).matching(identifier: "journal-event-group")
            .matching(NSPredicate(format: "label BEGINSWITH %@", "Walk by the river")).firstMatch
        XCTAssertTrue(group.waitForExistence(timeout: 10), "the categorised event borders the entry")
        let walk = app.staticTexts["Fixture walk"]
        let later = app.staticTexts["Fixture later"]
        XCTAssertTrue(walk.exists)
        XCTAssertTrue(later.exists)
        XCTAssertLessThan(group.frame.minY, walk.frame.minY, "the event's heading opens its border")
        XCTAssertLessThan(later.frame.maxY, group.frame.minY, "newest first, and outside the border")

        let photo = app.buttons["journal-photo"].firstMatch
        XCTAssertTrue(photo.waitForExistence(timeout: 5))
        photo.tap()
        let done = app.buttons["Done"]
        XCTAssertTrue(done.waitForExistence(timeout: 5), "a photo opens full screen")
        done.tap()

        // Short drags: a full swipe flings a lazily drawn row off the far edge.
        func nudge(until element: XCUIElement) {
            for _ in 0..<8 where !element.isHittable {
                let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
                start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: -200)))
            }
        }
        let play = app.buttons["journal-audio-play"].firstMatch
        nudge(until: play)
        XCTAssertTrue(play.waitForExistence(timeout: 5))
        XCTAssertEqual(play.label, "Play")
        play.tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label ENDSWITH %@", "/ 0:02")).firstMatch
            .waitForExistence(timeout: 5), "the clip plays in place")
        XCTAssertTrue(app.buttons["Transcript"].exists || app.staticTexts["Transcript"].exists)

        let video = app.buttons["journal-video"].firstMatch
        XCTAssertTrue(video.exists)
        XCTAssertTrue(app.links["Open on YouTube"].exists || app.buttons["Open on YouTube"].exists)
        XCTAssertTrue(app.staticTexts["A film about rivers."].exists, "the video's summary shows open")

        for _ in 0..<8 where !later.isHittable {
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.4))
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: 200)))
        }
        later.tap()
        XCTAssertTrue(app.navigationBars["Fixture later"].waitForExistence(timeout: 5), "an entry still opens")
    }

    /// Editing a server entry offers the Capture tab's buttons, and what they
    /// make is saved with the edit, queued under that entry, never into the
    /// composer's draft. A meal's editor offers them too, without YouTube.
    func testEditingAnEntryOrMealOffersTheCaptureButtons() {
        let app = XCUIApplication()
        app.launchArguments.append("-journalFeedFixture")
        app.launch()
        selectTab(app, "Journal")
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        pages.buttons["Journal"].tap()

        let later = app.staticTexts["Fixture later"]
        XCTAssertTrue(later.waitForExistence(timeout: 10))
        later.tap()
        XCTAssertTrue(app.navigationBars["Fixture later"].waitForExistence(timeout: 5))
        // Top right, as everywhere else.
        app.navigationBars["Fixture later"].buttons["Edit"].tap()
        let names = ["Attach file", "Choose photo", "Take photo", "Record", "Transcribe"]
        let first = app.buttons[names[0]]
        XCTAssertTrue(first.waitForExistence(timeout: 5))
        for name in names {
            XCTAssertTrue(app.buttons[name].exists, name)
            XCTAssertEqual(app.buttons[name].frame.midY, first.frame.midY, accuracy: 4, "\(name) sits in the same line")
        }
        for (left, right) in zip(names, names.dropFirst()) {
            XCTAssertLessThan(app.buttons[left].frame.midX, app.buttons[right].frame.midX, "\(left) sits left of \(right)")
        }
        XCTAssertTrue(app.textFields["YouTube video URL"].exists)
        let save = app.buttons["Save edit on this device"]
        XCTAssertFalse(save.isEnabled, "nothing changed yet")

        app.buttons["Choose photo"].tap()
        let photo = app.images.matching(identifier: "PXGGridLayout-Info").firstMatch
        guard photo.waitForExistence(timeout: 30) else { return XCTFail("Photo picker did not open") }
        photo.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        let done = app.buttons["Done"]
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "isEnabled == true"),
                                                                      object: done)], timeout: 5) == .completed)
        done.tap()
        XCTAssertTrue(app.staticTexts["Adding"].waitForExistence(timeout: 60), "the photo waits in this edit")
        XCTAssertTrue(save.isEnabled)
        save.tap()
        XCTAssertTrue(app.navigationBars["Journal"].waitForExistence(timeout: 5) || pages.waitForExistence(timeout: 5))
        let waiting = app.descendants(matching: .any).matching(identifier: "pending-additions").firstMatch
        XCTAssertTrue(waiting.waitForExistence(timeout: 10), "the feed says the photo is on its way")
        XCTAssertTrue(waiting.label.hasSuffix("waiting to upload"), waiting.label)

        let meal = app.staticTexts["Fixture ramen"]
        for _ in 0..<6 where !meal.isHittable {
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6))
            start.press(forDuration: 0.05, thenDragTo: start.withOffset(CGVector(dx: 0, dy: -150)))
        }
        XCTAssertTrue(meal.waitForExistence(timeout: 5))
        meal.tap()
        XCTAssertTrue(app.navigationBars["Fixture ramen"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Rich broth."].exists)
        app.navigationBars["Fixture ramen"].buttons["Edit"].tap()
        XCTAssertTrue(app.textFields["Dish"].waitForExistence(timeout: 5))
        for name in names { XCTAssertTrue(app.buttons[name].exists, "meal: \(name)") }
        XCTAssertFalse(app.textFields["YouTube video URL"].exists, "a meal takes no links")
        app.buttons["Cancel"].tap()
        XCTAssertTrue(app.navigationBars["Fixture ramen"].buttons["Edit"].waitForExistence(timeout: 5))

        // The composer's own draft was never touched.
        selectTab(app, "Capture")
        XCTAssertTrue(app.buttons["Transcribe"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Attachments"].exists)
    }

    /// Delete is part of editing, not reading, and asks before it does anything.
    func testDeletingAnEntryIsInEditAndAsksFirst() {
        let app = XCUIApplication()
        app.launchArguments.append("-journalFeedFixture")
        app.launch()
        selectTab(app, "Journal")
        let pages = app.segmentedControls["journal-page"]
        XCTAssertTrue(pages.waitForExistence(timeout: 5))
        pages.buttons["Journal"].tap()
        let later = app.staticTexts["Fixture later"]
        XCTAssertTrue(later.waitForExistence(timeout: 10))
        later.tap()
        let bar = app.navigationBars["Fixture later"]
        XCTAssertTrue(bar.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Delete entry"].exists, "not while reading")

        bar.buttons["Edit"].tap()
        let delete = app.buttons["Delete entry"]
        for _ in 0..<4 where !delete.isHittable { app.swipeUp() }
        XCTAssertTrue(delete.isHittable)
        delete.tap()
        let alert = app.alerts["Delete this entry?"]
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        XCTAssertTrue(alert.buttons["Delete"].exists)
        // Not confirmed: a queued delete would outlive this test and list the
        // fixture entry twice (as a pending edit) for the tests after it.
        alert.buttons["Cancel"].tap()
        XCTAssertFalse(alert.exists)
        XCTAssertTrue(bar.exists, "Cancel keeps the entry open")
    }

    /// An entry still waiting to sync opens with Edit at the top right, and
    /// the new words are what it will be sent with. UI tests have no server,
    /// so nothing has been sent and it stays editable.
    func testAWaitingEntryCanBeEdited() {
        let app = XCUIApplication()
        app.launch()
        let first = "Waiting \(UUID().uuidString.prefix(8))"
        let editor = app.textViews["Journal text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.tap()
        editor.typeText(first)
        app.buttons["Save entry"].tap()
        XCTAssertTrue(app.staticTexts["Saved on this device"].waitForExistence(timeout: 5))

        selectTab(app, "Journal")
        let card = app.staticTexts[first]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        let bar = app.navigationBars["Capture"]
        XCTAssertTrue(bar.waitForExistence(timeout: 5))
        bar.buttons["Edit"].tap()

        let text = app.textViews["Entry text"]
        XCTAssertTrue(text.waitForExistence(timeout: 5))
        let save = app.buttons["Save edit on this device"]
        XCTAssertFalse(save.isEnabled, "nothing changed yet")
        text.tap()
        text.typeText(" and then some")
        save.tap()

        let edited = first + " and then some"
        XCTAssertTrue(app.staticTexts[edited].waitForExistence(timeout: 5))
        XCTAssertFalse(app.textViews["Entry text"].exists, "back to reading")
        XCTAssertTrue(bar.buttons["Edit"].exists, "still waiting, so still editable")
        bar.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.staticTexts[edited].waitForExistence(timeout: 5), "the feed shows the new words")
        XCTAssertFalse(app.staticTexts[first].exists)
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

    /// The composer's draft outlives the app, and only Discard (after asking)
    /// throws it away: text, links and staged photos together.
    func testTheDraftIsKeptUntilDiscarded() {
        let app = XCUIApplication()
        app.launch()
        let words = "Draft \(UUID().uuidString.prefix(8))"
        let editor = app.textViews["Journal text"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.tap()
        editor.typeText(words)
        let field = app.textFields["YouTube video URL"]
        field.tap()
        field.typeText("https://youtu.be/aircAruvnKk")
        app.buttons["Add link"].tap()
        let link = "https://www.youtube.com/watch?v=aircAruvnKk"
        XCTAssertTrue(app.staticTexts[link].waitForExistence(timeout: 5))

        app.terminate()
        app.launch()
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        XCTAssertEqual(editor.value as? String, words, "kept across a relaunch")
        XCTAssertTrue(app.staticTexts[link].exists)

        let discard = app.buttons["Discard draft"]
        XCTAssertTrue(discard.isEnabled)
        discard.tap()
        let alert = app.alerts["Discard this draft?"]
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        alert.buttons["Cancel"].tap()
        XCTAssertEqual(editor.value as? String, words, "Cancel keeps it")

        discard.tap()
        XCTAssertTrue(alert.waitForExistence(timeout: 5))
        alert.buttons["Discard"].tap()
        XCTAssertTrue(XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"),
                                                                      object: app.staticTexts[link])], timeout: 5) == .completed)
        XCTAssertNotEqual(editor.value as? String, words)
        XCTAssertFalse(discard.isEnabled, "nothing left to discard")
    }

    func testSaveFoodEntrySitsLeftOfSaveEntryAndLeavesTheLinks() {
        let app = XCUIApplication()
        app.launch()
        let food = app.buttons["Save food entry"]
        let save = app.buttons["Save entry"]
        XCTAssertTrue(food.waitForExistence(timeout: 10))
        let window = app.windows.firstMatch.frame
        let discard = app.buttons["Discard draft"]
        XCTAssertTrue(discard.exists)
        // Discard · Save food entry · Save entry, left to right.
        XCTAssertLessThan(discard.frame.maxX, food.frame.minX, "Discard sits left of Save food entry")
        XCTAssertLessThan(food.frame.maxX, save.frame.minX, "Save food entry sits left of Save entry")
        XCTAssertLessThan(discard.frame.midX, window.midX, "Discard sits on the left")
        XCTAssertGreaterThan(save.frame.midX, window.midX, "Save entry sits on the right")
        XCTAssertEqual(food.frame.midY, save.frame.midY, accuracy: 4)
        XCTAssertEqual(discard.frame.midY, save.frame.midY, accuracy: 4)
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
        let names = ["Attach file", "Choose photo", "Take photo", "Record", "Transcribe"]
        let first = app.buttons[names[0]]
        XCTAssertTrue(first.waitForExistence(timeout: 10))
        for name in names {
            let button = app.buttons[name]
            XCTAssertTrue(button.exists, name)
            XCTAssertEqual(button.frame.midY, first.frame.midY, accuracy: 4, "\(name) sits in the same line")
        }
        // Most-used rightmost, under the thumb.
        for (left, right) in zip(names, names.dropFirst()) {
            XCTAssertLessThan(app.buttons[left].frame.midX, app.buttons[right].frame.midX, "\(left) sits left of \(right)")
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

    /// The newspaper is one continuous scroll: no page turning, the page
    /// counter follows the scroll, and the issue's own pages can't be deleted.
    func testNewspaperScrollsAsOneColumn() {
        guard UIDevice.current.userInterfaceIdiom == .pad else { return }
        let app = XCUIApplication()
        app.launchArguments.append("-newspaperFixture")
        app.launch()
        tab(app, "Draw").tap()
        let row = app.staticTexts.containing(NSPredicate(format: "label BEGINSWITH 'Toronto Star'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        let counter = app.buttons["notebook-page"]
        XCTAssertTrue(counter.waitForExistence(timeout: 10))
        XCTAssertEqual(counter.label, "p. 1 / 4")
        attach(app, "newspaper page 1")
        let shown = app.otherElements["notebook-visible-frame"]
        XCTAssertTrue(shown.waitForExistence(timeout: 5))
        // Fitted to the width from the top of page 1: nothing off to either
        // side, nothing hidden under the bar, the rest of the paper below.
        XCTAssertTrue(waitForValue(shown) { frame in
            abs(frame[0]) <= 1 && abs(frame[2] - 1240) <= 1 && abs(frame[1]) <= 1
        }, "the top of page 1, at full width: \(shown.value ?? "nil")")

        // Next scrolls the column down to the top of page 2 rather than
        // swapping the page. The fixture's page 1 is 1.8 times as tall as wide.
        app.buttons["Next page"].tap()
        XCTAssertTrue(waitForLabel(counter, equalTo: "p. 2 / 4"))
        XCTAssertTrue(waitForValue(shown) { frame in
            abs(frame[1] - 2232) <= 2 && abs(frame[0]) <= 1 && abs(frame[2] - 1240) <= 1
        }, "the top of page 2, at full width: \(shown.value ?? "nil")")
        attach(app, "newspaper page 2")

        // Turned sideways it still fills the width, so still nothing scrolls
        // sideways; less of the page fits, and the rest is below.
        XCUIDevice.shared.orientation = .landscapeLeft
        addTeardownBlock { XCUIDevice.shared.orientation = .portrait }
        XCTAssertTrue(waitForValue(shown) { frame in
            abs(frame[0]) <= 1 && abs(frame[2] - 1240) <= 1 && frame[3] < frame[2]
        }, "landscape fills the width: \(shown.value ?? "nil")")
        attach(app, "newspaper landscape")
        XCUIDevice.shared.orientation = .portrait
        XCTAssertTrue(waitForValue(shown) { frame in abs(frame[2] - 1240) <= 1 && frame[3] > frame[2] },
                      "upright again, full width: \(shown.value ?? "nil")")

        counter.tap()
        XCTAssertTrue(app.buttons["Page 4"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Delete this page"].exists, "an issue's pages share one canvas")
        app.buttons["Page 4"].tap()
        XCTAssertTrue(waitForLabel(counter, equalTo: "p. 4 / 4"))
    }

    /// A notes page turns sideways on a swipe, and finishing that swipe on
    /// the last page makes a new one; backwards from the first does nothing.
    func testNotesPagesTurnWithASwipe() {
        guard UIDevice.current.userInterfaceIdiom == .pad else { return }
        let app = XCUIApplication()
        app.launch()
        let notes = app.buttons["capture-notes"]
        XCTAssertTrue(notes.waitForExistence(timeout: 10))
        detachDraftNotes(app)
        notes.tap()
        let counter = app.buttons["notebook-page"]
        XCTAssertTrue(counter.waitForExistence(timeout: 10))
        let start = counter.label
        let total = Int(start.split(separator: "/").last?.trimmingCharacters(in: .whitespaces) ?? "") ?? 1
        // Go to the last page first, if this notebook has several.
        for _ in 1..<max(total, 1) { app.buttons["Next page"].tap() }
        XCTAssertEqual(counter.label, "\(total) / \(total)")

        let window = app.windows.firstMatch
        let shown = app.otherElements["notebook-visible-frame"]
        // Turning happens only from a side of the screen: a finger anywhere
        // else may be a palm, and moves nothing at all.
        let rightEdge = window.coordinate(withNormalizedOffset: CGVector(dx: 0.985, dy: 0.5))
        let leftEdge = window.coordinate(withNormalizedOffset: CGVector(dx: 0.015, dy: 0.5))
        let right = window.coordinate(withNormalizedOffset: CGVector(dx: 0.85, dy: 0.5))
        let left = window.coordinate(withNormalizedOffset: CGVector(dx: 0.15, dy: 0.5))
        let before = shown.value as? String
        right.press(forDuration: 0.05, thenDragTo: left)
        XCTAssertEqual(counter.label, "\(total) / \(total)", "a drag mid-page is not a page turn")
        let middle = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        middle.press(forDuration: 0.05, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15)))
        middle.press(forDuration: 0.05, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.7, dy: 0.8)))
        sleep(1)
        XCTAssertEqual(shown.value as? String, before, "one finger never scrolls a notes page")
        // A short drag from the side springs back.
        rightEdge.press(forDuration: 0.05, thenDragTo: window.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)))
        XCTAssertEqual(counter.label, "\(total) / \(total)")
        // A deliberate one off the last page adds a page.
        rightEdge.press(forDuration: 0.05, thenDragTo: left)
        XCTAssertTrue(waitForLabel(counter, equalTo: "\(total + 1) / \(total + 1)"))
        attach(app, "notes new page")
        // And back, from the other side.
        leftEdge.press(forDuration: 0.05, thenDragTo: right)
        XCTAssertTrue(waitForLabel(counter, equalTo: "\(total) / \(total + 1)"))
        XCTAssertTrue(app.buttons["notebook-save"].exists, "the left edge turns the page, not Back")
        // A page fits whole either way up: no vertical scrolling sideways on.
        XCUIDevice.shared.orientation = .landscapeLeft
        addTeardownBlock { XCUIDevice.shared.orientation = .portrait }
        XCTAssertTrue(waitForValue(shown) { frame in
            frame[1] <= 0.5 && frame[1] + frame[3] >= 1753.5 && frame[0] <= 0.5 && frame[0] + frame[2] >= 1239.5
        }, "the whole A4 page on screen in landscape: \(shown.value ?? "nil")")
        attach(app, "notes landscape")
        XCUIDevice.shared.orientation = .portrait
        XCTAssertTrue(waitForValue(shown) { frame in frame[1] + frame[3] >= 1753.5 && frame[0] + frame[2] >= 1239.5 })

        // Backwards from the first page: nothing.
        for _ in 1..<max(total, 1) { app.buttons["Previous page"].tap() }
        XCTAssertEqual(counter.label, "1 / \(total + 1)")
        leftEdge.press(forDuration: 0.05, thenDragTo: right)
        XCTAssertEqual(counter.label, "1 / \(total + 1)")
        // Leave no notes in the entry draft for the next test.
        app.navigationBars.buttons.element(boundBy: 0).tap()
        detachDraftNotes(app)
    }

    /// Notes reopen the entry draft's notebook until the entry is saved, so a
    /// test that wants a fresh one first takes any left over by an earlier
    /// test (or an earlier try of this one) out of the draft. They stay in Draw.
    private func detachDraftNotes(_ app: XCUIApplication) {
        let row = app.buttons["capture-draft-notes"]
        guard row.waitForExistence(timeout: 3) else { return }
        row.swipeLeft()
        let remove = app.buttons["Remove"]
        if remove.waitForExistence(timeout: 3) { remove.tap() }
        XCTAssertTrue(waitForAbsence(row), "the old notes left the draft")
    }

    private func waitForAbsence(_ element: XCUIElement) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        return XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: element)], timeout: 5) == .completed
    }

    /// Waits for the canvas's visible frame (x, y, width, height, in canvas
    /// units) to satisfy `check`.
    private func waitForValue(_ element: XCUIElement, _ check: @escaping ([Double]) -> Bool) -> Bool {
        let predicate = NSPredicate { object, _ in
            let frame = ((object as? XCUIElement)?.value as? String ?? "").split(separator: ",").compactMap { Double($0) }
            return frame.count == 4 && check(frame)
        }
        return XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: element)], timeout: 5) == .completed
    }

    private func waitForLabel(_ element: XCUIElement, equalTo value: String? = nil, notEqualTo other: String? = nil) -> Bool {
        let format = value != nil ? "label == %@" : "label != %@"
        let predicate = NSPredicate(format: format, value ?? other ?? "")
        return XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: element)], timeout: 5) == .completed
    }

    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
