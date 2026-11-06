import Foundation
import XCTest
@testable import LunaschalCore

final class CoachReviewTests: XCTestCase {
    /// A briefing's metadata as `backend/briefing_job.py` writes it.
    let briefing = """
    {"briefing": true,
     "review": {"day": "2026-11-02", "rating": 4, "computedRating": 3, "overrideReason": "Read on the train",
                "done": 2, "missed": 1, "excused": 1,
                "items": [{"kind": "habit", "id": "h1", "title": "Stretch", "done": true, "excused": false, "excuse": null},
                          {"kind": "habit", "id": "h2", "title": "Read", "done": false, "excused": false, "excuse": null},
                          {"kind": "weight", "id": null, "title": "Weigh-in", "done": false, "excused": true,
                           "excuse": "Travelling"},
                          {"kind": "goal", "id": "g", "title": "Pushups", "done": null, "excused": false, "excuse": null}]},
     "proposals": [{"id": "p", "kind": "habit_plan", "status": "pending",
                    "data": {"taskId": "h2", "habitTitle": "Read", "obstacle": "late nights",
                             "cue": "When I get into bed, I will read one page"}}]}
    """

    func testParsesTheReviewOnABriefing() throws {
        let review = try XCTUnwrap(DayReview.parse(metadata: briefing))
        XCTAssertEqual(review.stars, "★★★★☆")
        XCTAssertEqual(review.computedRating, 3)
        XCTAssertEqual(review.tally, "2 of 3 done · 1 excused")
        XCTAssertEqual(review.items.map(\.status), ["done", "missed", "excused", "unknown"])
        XCTAssertEqual(review.explainPrompt, "About yesterday — Read: ")
        XCTAssertNil(DayReview.parse(metadata: #"{"briefing": true}"#))
        XCTAssertNil(DayReview.parse(metadata: nil))
    }

    func testAHabitPlanCardSaysWhatItDoes() throws {
        let proposal = try XCTUnwrap(ChatMeta(briefing).proposals.first)
        XCTAssertEqual(proposal.headline, "Make this the plan for “Read”?")
        XCTAssertEqual(proposal.acceptLabel, "Use as cue")
    }

    func testAnExcusedOrEmptyDayHasNoStars() {
        let excused = DayReview(day: "d", rating: nil, done: 0, missed: 0, excused: 2, items: [])
        XCTAssertEqual(excused.stars, "")
        XCTAssertEqual(excused.tally, "Excused")
        XCTAssertEqual(DayReview(day: "d", rating: nil, done: 0, missed: 0, excused: 0, items: []).tally, "Nothing tracked")
    }

    func testParsesTheCheckinAndRolloverMessages() {
        let checkin = #"{"coach": {"kind": "checkin", "day": "2026-11-03", "open": [{"kind": "weight", "id": null, "title": "Weigh-in"}]}}"#
        XCTAssertEqual(CoachMessage.parse(metadata: checkin),
                       .checkin(day: "2026-11-03", open: [DayReview.Item(kind: "weight", title: "Weigh-in", done: false)]))
        let rollover = """
        {"coach": {"kind": "rollover", "day": "2026-11-03", "horizons": ["week", "month"],
                   "goals": [{"id": "g", "horizon": "week", "title": "Inbox", "rating": 4, "periodLabel": "Week of Oct 26"},
                             {"id": "h", "horizon": "month", "title": "Cook", "rating": null, "periodLabel": "October 2026"}]}}
        """
        guard case let .rollover(_, horizons, graded) = CoachMessage.parse(metadata: rollover) else {
            return XCTFail("not a rollover")
        }
        XCTAssertEqual(horizons, [.week, .month])
        XCTAssertEqual(graded.map(\.rating), [4, nil])
        XCTAssertNil(CoachMessage.parse(metadata: #"{"coach": {"kind": "nudge", "day": "x"}}"#))
    }

    func testTheReviewsRouteDecodes() throws {
        let json = """
        {"today": {"day": "2026-11-03", "status": "draft", "score": null, "computedRating": null, "rating": null,
                   "ratingOverride": null, "overrideReason": null, "items": [], "done": 0, "missed": 0, "excused": 0,
                   "summary": null, "replan": [], "degraded": false, "finalizedAt": null},
         "days": [{"day": "2026-11-02", "status": "final", "score": 0.5, "computedRating": 3, "rating": 3,
                   "ratingOverride": null, "overrideReason": null, "items": [], "done": 1, "missed": 1, "excused": 0,
                   "summary": "Half.", "replan": [], "degraded": false, "finalizedAt": 1}]}
        """
        let reviews = try JSONDecoder().decode(CoachReviews.self, from: Data(json.utf8))
        XCTAssertEqual(reviews.review(of: "2026-11-02")?.summary, "Half.")
        XCTAssertNil(reviews.review(of: "2026-11-01"))
        // The cached lists keep it, and an older cache without one still loads.
        let lists = TodoLists(review: reviews.days.first)
        let round = try JSONDecoder().decode(TodoLists.self, from: JSONEncoder().encode(lists))
        XCTAssertEqual(round.review?.rating, 3)
    }
}
