import XCTest
@testable import LunaschalCore

final class FicServerActionsTests: XCTestCase {
    func testUpdateReplyDecodesBothQueuedAndCancelled() throws {
        let queued = try JSONDecoder().decode(FicUpdateReply.self, from: Data(#"{"id":"x","queued":true,"deep":true}"#.utf8))
        XCTAssertEqual(queued, FicUpdateReply(queued: true, deep: true))
        XCTAssertTrue(queued.summary(title: "Worm").contains("re-read in full"))

        // The cancelling reply carries no `deep` at all.
        let cancelled = try JSONDecoder().decode(FicUpdateReply.self, from: Data(#"{"id":"x","queued":false}"#.utf8))
        XCTAssertEqual(cancelled, FicUpdateReply(queued: false))
        XCTAssertEqual(cancelled.summary(title: "Worm"), "Stopped waiting for an update to “Worm”.")

        XCTAssertTrue(FicUpdateReply(queued: true, deep: false).summary(title: "Worm").contains("queued for an update"))
    }

    func testRefreshSummaryDecodesTheRouteAndCountsWhatWasQueued() throws {
        let body = #"{"flagged":2,"newImports":1,"skippedActive":3,"alertsSeen":9,"errors":{"forums.spacebattles.com":"HTTP 403"}}"#
        let summary = try JSONDecoder().decode(FicRefreshSummary.self, from: Data(body.utf8))
        XCTAssertEqual(summary, FicRefreshSummary(flagged: 2, newImports: 1, skippedActive: 3, alertsSeen: 9,
                                                  errors: ["forums.spacebattles.com": "HTTP 403"]))
        XCTAssertEqual(summary.summary, """
            Queued on the server: 2 fics to update, 1 new fic to import. They arrive with the next sync.
            3 already queued or downloading.
            forums.spacebattles.com: HTTP 403
            """)
    }

    func testRefreshSummarySaysWhenThereIsNothingNew() {
        XCTAssertEqual(FicRefreshSummary(flagged: 0, newImports: 0, skippedActive: 0, alertsSeen: 0).summary,
                       "No new alerts on the forums.")
        XCTAssertEqual(FicRefreshSummary(flagged: 0, newImports: 0, skippedActive: 1, alertsSeen: 4).summary,
                       "Nothing new to fetch.\n1 already queued or downloading.")
    }

    func testFailureShowsTheServersOwnReason() {
        let busy = FicServerFailure(status: 409, body: Data(#"{"error":"A download is already running for this fic"}"#.utf8))
        XCTAssertEqual(busy.localizedDescription, "A download is already running for this fic")

        // No usable body: the status decides, never the capture wording.
        XCTAssertEqual(FicServerFailure(status: 404, body: Data()).localizedDescription, "The server no longer has this fic.")
        XCTAssertEqual(FicServerFailure(status: 500, body: Data("<html>".utf8)).localizedDescription, "Server returned HTTP 500.")
        XCTAssertEqual(FicServerFailure(status: 400, body: Data(#"{"error":"  "}"#.utf8)).localizedDescription, "Server returned HTTP 400.")
    }

    func testOnlyOnlineSourcesCanBeUpdated() {
        XCTAssertTrue(FicSources.isUpdatable("xenforo"))
        XCTAssertTrue(FicSources.isUpdatable("ao3"))
        XCTAssertFalse(FicSources.isUpdatable("epub"))
        XCTAssertFalse(FicSources.isUpdatable(nil))
    }

    func testCheckRefusesAnIdThatIsNotAULID() async throws {
        let api = try JournalAPI(server: URL(string: "https://host.example")!, token: "t", allowCellular: true)
        do {
            _ = try await api.checkFicForUpdates("../settings")
            XCTFail("expected invalid id")
        } catch CaptureError.invalidID {}
    }
}
