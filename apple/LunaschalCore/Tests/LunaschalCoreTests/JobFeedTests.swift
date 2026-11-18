import Foundation
import XCTest
@testable import LunaschalCore

final class JobFeedTests: XCTestCase {
    private var root: URL!
    private var store: JobStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try JobStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private final class Transport: JobTransport {
        var sent: [(JobDecision, String)] = []
        var failures: [String: Error] = [:]
        func send(_ decision: JobDecision, jobID: String) async throws {
            if let failure = failures[jobID] { throw failure }
            sent.append((decision, jobID))
        }
    }

    // The shape `_feed_item` sends: SQLite's 0/1 for remote, nulls, and
    // camelCased columns the phone doesn't read.
    func testDecodesTheServersFeedItem() throws {
        let json = """
        [{"id": "01J0", "source": "greenhouse", "url": "https://x.test/1", "company": "Acme", "title": "Engineer",
          "location": "Toronto, ON", "remote": 1, "salaryMin": 90000, "salaryMax": 120000.0, "salaryCurrency": "CAD",
          "description": "Build things", "matchScore": 0.4, "dismissed": 0, "postedAt": null,
          "createdAt": "2026-10-01T12:00:00+00:00", "matchReasons": {"matched": ["swift"], "missing": ["go", "rust"], "coverage": 0.3},
          "triageState": "kept", "triageFit": "possible", "triageSummary": "A summary.",
          "triageFlags": [{"kind": "contract_only", "detail": "12 months"}], "distanceKm": null,
          "distancePrecision": "", "workLocation": "hybrid", "triageError": null},
         {"id": "01J1", "title": "Bare", "company": "", "remote": false, "matchReasons": null, "triageFlags": []}]
        """
        let jobs = try JSONDecoder().decode([FeedJob].self, from: Data(json.utf8))
        XCTAssertEqual(jobs.count, 2)
        XCTAssertTrue(jobs[0].remote)
        XCTAssertEqual(jobs[0].salaryText, "90k–120k CAD")
        XCTAssertEqual(jobs[0].matchPercent, 33)
        XCTAssertEqual(jobs[0].triageFlags, [FeedJob.Flag(kind: "contract_only", detail: "12 months")])
        XCTAssertEqual(jobs[0].fitLabel, "Worth a look")
        XCTAssertFalse(jobs[1].remote)
        XCTAssertNil(jobs[1].matchPercent)
        XCTAssertEqual(jobs[1].salaryText, "")
        XCTAssertNil(jobs[1].fitLabel)
    }

    // The cases `distanceLabel` in src/lib/jobs.ts answers.
    func testDistanceReadsLikeTheWebFeed() {
        XCTAssertEqual(FeedJob(id: "a", title: "", company: "", remote: true).distanceText, "Remote")
        XCTAssertNil(FeedJob(id: "b", title: "", company: "").distanceText)
        XCTAssertEqual(FeedJob(id: "c", title: "", company: "", distanceKm: 4.26, distancePrecision: "exact").distanceText,
                       "4.3 km from Union Station")
        XCTAssertEqual(FeedJob(id: "d", title: "", company: "", distanceKm: 31.6, distancePrecision: "city").distanceText,
                       "~32 km from Union Station")
        // The board said remote; the body says two days in the office.
        XCTAssertEqual(FeedJob(id: "e", title: "", company: "", remote: true, distanceKm: 12,
                               distancePrecision: "city", workLocation: "hybrid").distanceText,
                       "Hybrid · ~12 km from Union Station")
    }

    func testSplitsByFitThenByKeywordScore() {
        let reasons = { (hit: Int, miss: Int) in
            FeedJob.MatchReasons(matched: Array(repeating: "x", count: hit), missing: Array(repeating: "y", count: miss))
        }
        let jobs = [
            FeedJob(id: "strong", title: "", company: "", triageFit: "strong"),
            FeedJob(id: "stretch", title: "", company: "", matchReasons: reasons(9, 1), triageFit: "stretch"),
            FeedJob(id: "untriaged-good", title: "", company: "", matchReasons: reasons(1, 1)),
            FeedJob(id: "untriaged-weak", title: "", company: "", matchReasons: reasons(1, 3)),
            FeedJob(id: "possible", title: "", company: "", triageFit: "possible"),
            FeedJob(id: "unscored", title: "", company: ""),
        ]
        let (promising, rest) = JobFeed.split(jobs)
        XCTAssertEqual(promising.map(\.id), ["strong", "untriaged-good", "possible"])
        XCTAssertEqual(rest.map(\.id), ["stretch", "untriaged-weak", "unscored"])
    }

    func testADecisionHidesItsCardAndALaterOneReplacesIt() throws {
        let jobs = [FeedJob(id: "a", title: "A", company: ""), FeedJob(id: "b", title: "B", company: "")]
        try store.decide(jobs[0], .queue)
        try store.decide(jobs[0], .dismiss)
        let pending = try store.pending()
        XCTAssertEqual(pending.map(\.decision), [.dismiss])
        XCTAssertEqual(JobFeed.hidingDecided(jobs, pending).map(\.id), ["b"])
    }

    func testTheLastFeedSurvivesARestart() throws {
        let jobs = [FeedJob(id: "a", title: "A", company: "Acme", triageSummary: "Two sentences.")]
        try store.saveFeed(jobs)
        XCTAssertEqual(try JobStore(root: root).cachedFeed(), jobs)
    }

    func testSendsInOrderAndKeepsTheRestWhenOffline() async throws {
        try store.decide(FeedJob(id: "a", title: "A", company: ""), .queue)
        try store.decide(FeedJob(id: "b", title: "B", company: ""), .dismiss)
        try store.decide(FeedJob(id: "c", title: "C", company: ""), .queue)
        let transport = Transport()
        transport.failures["b"] = URLError(.notConnectedToInternet)
        do {
            _ = try await JobSync(store: store).run(using: transport)
            XCTFail("an offline pass should stop")
        } catch is URLError {}
        XCTAssertEqual(transport.sent.map(\.1), ["a"])
        XCTAssertEqual(try store.pending().map(\.jobID), ["b", "c"])

        transport.failures = [:]
        let refused = try await JobSync(store: store).run(using: transport)
        XCTAssertEqual(refused, [])
        XCTAssertEqual(transport.sent.map(\.1), ["a", "b", "c"])
        XCTAssertEqual(transport.sent.map(\.0), [.queue, .dismiss, .queue])
        XCTAssertEqual(try store.pending(), [])
    }

    func testARefusalIsReportedAndDropped() async throws {
        try store.decide(FeedJob(id: "gone", title: "Deleted", company: ""), .queue)
        try store.decide(FeedJob(id: "also-gone", title: "Also deleted", company: ""), .dismiss)
        let transport = Transport()
        transport.failures["gone"] = JobRefusal(status: 404, message: "Not found")
        transport.failures["also-gone"] = JobRefusal(status: 404, message: "Not found")
        let refused = try await JobSync(store: store).run(using: transport)
        // A dismissed posting that no longer exists is what was wanted.
        XCTAssertEqual(refused, ["The server didn't queue “Deleted”: Not found"])
        XCTAssertEqual(try store.pending(), [])
    }

    // MARK: The swipe deck

    func testOnlyAFarOrFastSidewaysDragDecides() {
        let width = 400.0
        XCTAssertEqual(JobDeck.decision(dx: 160, dy: 10, width: width), .queue)
        XCTAssertEqual(JobDeck.decision(dx: -160, dy: -10, width: width), .dismiss)
        // Short and slow: snaps back.
        XCTAssertNil(JobDeck.decision(dx: 100, dy: 0, velocityX: 200, width: width))
        // Short but flicked.
        XCTAssertEqual(JobDeck.decision(dx: 60, dy: 0, velocityX: 1200, width: width), .queue)
        // A flick back against the drag is a change of mind, not a decision.
        XCTAssertNil(JobDeck.decision(dx: 60, dy: 0, velocityX: -1200, width: width))
        // Scrolling the card: mostly vertical never decides, however far.
        XCTAssertNil(JobDeck.decision(dx: 180, dy: 300, velocityX: 1500, width: width))
    }

    func testTheDeckPutsWorthALookFirstAndSkipsDecided() throws {
        let jobs = [
            FeedJob(id: "weak", title: "", company: "", triageFit: "stretch"),
            FeedJob(id: "strong", title: "S", company: "", triageFit: "strong"),
            FeedJob(id: "possible", title: "", company: "", triageFit: "possible"),
        ]
        try store.decide(jobs[1], .queue)
        let deck = JobDeck.cards(jobs, try store.pending())
        XCTAssertEqual(deck.cards.map(\.id), ["possible", "weak"])
        XCTAssertEqual(deck.promising, 1)
    }

    func testUndoIsTheOppositeAndReplacesAnUnsentDecision() throws {
        let job = FeedJob(id: "a", title: "A", company: "")
        let queued = try store.decide(job, .queue)
        XCTAssertEqual(JobDeck.undo(queued), .unqueue)
        try store.decide(job, JobDeck.undo(queued)!)
        // The queue never leaves the phone; the server is asked to undo nothing.
        XCTAssertEqual(try store.pending().map(\.decision), [.unqueue])
        XCTAssertEqual(JobDeck.undo(JobDecisionOp(jobID: "a", decision: .dismiss, title: "A")), .undismiss)
        // An undo is not itself undone.
        XCTAssertNil(JobDeck.undo(JobDecisionOp(jobID: "a", decision: .undismiss, title: "A")))
    }

    func testAPendingUndoShowsTheCardAgain() throws {
        let jobs = [FeedJob(id: "a", title: "A", company: "")]
        try store.decide(jobs[0], .undismiss)
        XCTAssertEqual(JobFeed.hidingDecided(jobs, try store.pending()).map(\.id), ["a"])
    }

    func testUndoRoutesAndBodies() {
        XCTAssertEqual(JobDecision.undismiss.route, "dismiss")
        XCTAssertEqual(JournalAPI.jobBody(.undismiss) as? [String: Bool], ["dismissed": false])
        XCTAssertEqual(JobDecision.unqueue.route, "unqueue")
        XCTAssertTrue(JournalAPI.jobBody(.unqueue).isEmpty)
    }

    func testARefusedUndoIsWordedAsOne() async throws {
        try store.decide(FeedJob(id: "a", title: "Applied", company: ""), .unqueue)
        let transport = Transport()
        transport.failures["a"] = JobRefusal(status: 409, message: "Already applied")
        let refused = try await JobSync(store: store).run(using: transport)
        XCTAssertEqual(refused, ["The server didn't take back the queue of “Applied”: Already applied"])
    }

    // MARK: The posting body

    // The shapes backend/jobs/posting_text.py writes.
    func testParsesThePostingIntoBlocks() {
        let markdown = "### About the role\n\nWe use **Swift**\ndaily.\n\n- Own sync\n  - SQLite\n- Ship\n\n1. Apply\n2. Chat"
        XCTAssertEqual(PostingBlock.parse(markdown), [
            .heading("About the role"),
            .paragraph("We use **Swift** daily."),
            .item("Own sync", marker: "•", depth: 0),
            .item("SQLite", marker: "•", depth: 1),
            .item("Ship", marker: "•", depth: 0),
            .item("Apply", marker: "1.", depth: 0),
            .item("Chat", marker: "2.", depth: 0),
        ])
        // A sentence that merely starts with a year is not a numbered item.
        XCTAssertEqual(PostingBlock.parse("2026.Plans ahead"), [.paragraph("2026.Plans ahead")])
        XCTAssertEqual(PostingBlock.parse(""), [])
    }

    func testAPostingIsKeptForOfflineAndPrunedWithTheFeed() throws {
        try store.savePosting("01JA", "### Hi")
        try store.savePosting("01JB", "Bye")
        XCTAssertEqual(try JobStore(root: root).cachedPosting("01JA"), "### Hi")
        store.prunePostings(keeping: ["01JA"])
        XCTAssertNil(store.cachedPosting("01JB"))
        XCTAssertEqual(store.cachedPosting("01JA"), "### Hi")
        XCTAssertThrowsError(try store.savePosting("../x", "no"))
    }
}
