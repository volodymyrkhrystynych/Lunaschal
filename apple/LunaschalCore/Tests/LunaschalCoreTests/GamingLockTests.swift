import XCTest
@testable import LunaschalCore

final class GamingLockTests: XCTestCase {
    private func status(_ json: String) throws -> GamingStatus {
        try JSONDecoder().decode(GamingStatus.self, from: Data(json.utf8))
    }

    private let settings = #""settings": {"enabled": true, "days": [0,1,2,3,4], "patterns": ["steam"]}"#

    func testDecodesTheServersShape() throws {
        let s = try status(#"{"day": "2026-11-11", "applies": true, "locked": true, "unlockedAt": null, "method": null, "reason": null, "ratingAtUnlock": null, "workEnd": "17:30", "# + settings + "}")
        XCTAssertTrue(s.locked)
        XCTAssertEqual(s.settings.days, [0, 1, 2, 3, 4])
        XCTAssertEqual(s.summary(), "Locked until you tell the chat you're done with work (work ends 17:30)")
    }

    func testSummaryNamesHowItWasUnlocked() throws {
        let utc = TimeZone(identifier: "UTC")!
        let review = try status(#"{"day": "2026-11-11", "applies": true, "locked": false, "unlockedAt": 1794420000, "method": "review", "ratingAtUnlock": 4, "workEnd": "17:00", "# + settings + "}")
        XCTAssertEqual(review.summary(timeZone: utc), "Unlocked after the review at 18:00 · work ended 17:00")
        let override = try status(#"{"day": "2026-11-11", "applies": true, "locked": false, "unlockedAt": 1794420000, "method": "override", "reason": "Raid night", "# + settings + "}")
        XCTAssertEqual(override.summary(timeZone: utc), "Unlocked without the review at 18:00")
    }

    func testSummaryWhenOffOrNotALockDay() throws {
        let off = try status(#"{"day": "2026-11-14", "applies": false, "locked": false, "settings": {"enabled": false, "days": [], "patterns": []}}"#)
        XCTAssertEqual(off.summary(), "Off")
        let weekend = try status(#"{"day": "2026-11-14", "applies": false, "locked": false, "# + settings + "}")
        XCTAssertEqual(weekend.summary(), "Not a lock day — games are open")
    }

    func testOverrideReasonNeedsTenCharacters() {
        XCTAssertFalse(GamingStatus.acceptableReason("  bored   "))
        XCTAssertTrue(GamingStatus.acceptableReason("Friends online for a raid"))
    }

    func testUnlockCardLabels() {
        let pending = ChatProposal(JSONValue.parse(#"{"id": "1", "kind": "unlock_gaming", "data": {"rating": 4}}"#)!)!
        XCTAssertEqual(pending.headline, "Done with work — unlock games?")
        XCTAssertEqual(pending.acceptLabel, "Unlock gaming")
        let done = ChatProposal(JSONValue.parse(#"{"id": "1", "kind": "unlock_gaming", "data": {"rating": 4}, "status": "accepted", "result": {"rating": 3, "pausedInference": true}}"#)!)!
        XCTAssertEqual(done.resolvedLabel, "Games unlocked · today ★★★ · GPU freed")
    }

    func testRefusalCarriesTheServersMessage() {
        XCTAssertEqual(GamingLockRefusal.message(from: Data(#"{"error": "Games are not locked today"}"#.utf8)),
                       "Games are not locked today")
        XCTAssertNil(GamingLockRefusal.message(from: Data("<html>Bad Gateway</html>".utf8)))
        XCTAssertNil(GamingLockRefusal.message(from: Data(#"{"error": ""}"#.utf8)))
    }
}
