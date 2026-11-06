import Foundation
import XCTest
@testable import LunaschalCore

final class CoachNotificationTests: XCTestCase {
    let today = "2026-11-03"

    func payload(_ kind: String, habit: String? = nil, day: String? = nil) -> CoachPayload {
        CoachPayload(kind: kind, day: day, messageID: "m", habitID: habit)
    }

    func testReadsTheServersPayload() throws {
        let userInfo: [AnyHashable: Any] = [
            "aps": ["alert": ["title": "T", "body": "B"], "category": "COACH_HABIT"],
            "coach": ["kind": "habit", "day": "2026-11-02", "messageId": "m1", "conversationId": "c1",
                      "habitId": "h1", "decisionId": "d1", "horizons": ["week", "decade"]],
        ]
        let read = try XCTUnwrap(CoachPayload(userInfo: userInfo))
        XCTAssertEqual(read, CoachPayload(kind: "habit", day: "2026-11-02", messageID: "m1", conversationID: "c1",
                                          habitID: "h1", decisionID: "d1", horizons: [.week]))
        XCTAssertNil(CoachPayload(userInfo: ["aps": [:]]))
    }

    func testEveryCategoryHasItsButtons() {
        XCTAssertEqual(CoachCategory.habit.actions, [.done, .snooze])
        XCTAssertEqual(CoachCategory.checkin.actions, [.reply])
        XCTAssertTrue(CoachAction.logWeight.takesText)
        XCTAssertFalse(CoachAction.done.takesText)
    }

    func testDoneTicksTheDayTheReminderWasFor() {
        // Answered after 4am: the tick still belongs to yesterday's reminder.
        XCTAssertEqual(CoachRoute.resolve(payload("habit", habit: "h", day: "2026-11-02"), action: "COACH_DONE",
                                          today: today), .tickHabit(id: "h", day: "2026-11-02"))
        XCTAssertEqual(CoachRoute.resolve(payload("habit", habit: "h"), action: "COACH_DONE", today: today),
                       .tickHabit(id: "h", day: today))
        XCTAssertEqual(CoachRoute.resolve(payload("habit"), action: "COACH_DONE", today: today), .openToday)
    }

    func testTypedValuesAreRead() {
        let weight = payload("weight")
        XCTAssertEqual(CoachRoute.resolve(weight, action: "COACH_LOG_WEIGHT", text: " 81,4 kg", today: today),
                       .logWeight(kg: 81.4))
        XCTAssertEqual(CoachRoute.resolve(weight, action: "COACH_LOG_WEIGHT", text: "heavy", today: today),
                       .retype(.logWeight))
        XCTAssertEqual(CoachRoute.resolve(weight, action: "COACH_LOG_WEIGHT", text: "0", today: today),
                       .retype(.logWeight))
        XCTAssertEqual(CoachRoute.resolve(payload("calories"), action: "COACH_LOG_CALORIES", text: "Pasta 650",
                                          today: today),
                       .logCalories(CalorieLine(description: "Pasta", calories: 650)))
        XCTAssertEqual(CoachRoute.resolve(payload("checkin"), action: "COACH_REPLY", text: "Busy day", today: today),
                       .reply("Busy day"))
        XCTAssertEqual(CoachRoute.resolve(payload("checkin"), action: "COACH_REPLY", text: "  ", today: today),
                       .openChat)
        XCTAssertEqual(CoachRoute.resolve(payload("habit"), action: "COACH_SNOOZE", today: today), .snooze)
    }

    func testATapOpensWhereTheThingIsDone() {
        let tap = UNNotificationDefaultActionIdentifierForTests
        XCTAssertEqual(CoachRoute.resolve(payload("weight"), action: tap, today: today), .openDaily)
        XCTAssertEqual(CoachRoute.resolve(payload("rollover"), action: tap, today: today), .openToday)
        XCTAssertEqual(CoachRoute.resolve(payload("checkin"), action: tap, today: today), .openChat)
    }

    func testResponsesReportWhatWasDone() {
        XCTAssertEqual(CoachRoute.snooze.response, "snooze")
        XCTAssertEqual(CoachRoute.logWeight(kg: 80).response, "done")
        XCTAssertEqual(CoachRoute.openChat.response, "opened")
        XCTAssertEqual(CoachRoute.resolve(payload("goals"), action: UNNotificationDefaultActionIdentifierForTests,
                                          today: today), .openToday)
    }

    func testDecodesSettingsAndStats() throws {
        let settings = #"{"enabled": true, "checkinEnabled": false, "checkinHour": 21, "quietStart": 22, "quietEnd": 8,"#
            + #""dailyCap": 3, "noSendRate": 0.25, "behaviors": {"weight": true, "calories": false, "habits": true}}"#
        XCTAssertEqual(try JSONDecoder().decode(CoachSettings.self, from: Data(settings.utf8)).behaviors.calories, false)
        let stats = #"[{"behavior": "weight", "label": "Weigh-in", "sent": 3, "held": 1, "skipped": 0,"#
            + #""doneWhenSent": 0.75, "doneWhenHeld": null, "arms": {"plain": {"mean": 0.4, "n": 2},"#
            + #""identity": {"mean": 0.7, "n": 1}}, "slots": {}}]"#
        let stat = try XCTUnwrap(JSONDecoder().decode([CoachStat].self, from: Data(stats.utf8)).first)
        XCTAssertEqual(stat.leading?.arm, "identity")
        XCTAssertNil(stat.doneWhenHeld)
    }

    func testTokensAreHex() {
        XCTAssertEqual(PushToken.hex(Data([0x00, 0xab, 0xff])), "00abff")
    }
}

/// `UNNotificationDefaultActionIdentifier`, which Linux has no UserNotifications to define.
let UNNotificationDefaultActionIdentifierForTests = "com.apple.UNNotificationDefaultActionIdentifier"
