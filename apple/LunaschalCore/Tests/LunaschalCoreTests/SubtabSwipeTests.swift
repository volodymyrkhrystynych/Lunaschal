import XCTest
@testable import LunaschalCore

final class SubtabSwipeTests: XCTestCase {
    private let pages = ["daily", "entry", "workout"]

    func testSwipingLeftBringsInThePageOnTheRight() {
        XCTAssertEqual(SubtabSwipe.destination(from: "entry", in: pages, dx: -120, dy: 10), "workout")
    }

    func testSwipingRightBringsInThePageOnTheLeft() {
        XCTAssertEqual(SubtabSwipe.destination(from: "entry", in: pages, dx: 120, dy: -10), "daily")
    }

    func testTheEndsDoNotWrapAround() {
        XCTAssertNil(SubtabSwipe.destination(from: "daily", in: pages, dx: 200, dy: 0))
        XCTAssertNil(SubtabSwipe.destination(from: "workout", in: pages, dx: -200, dy: 0))
    }

    func testAShortSwipeStaysPut() {
        XCTAssertNil(SubtabSwipe.destination(from: "entry", in: pages, dx: -79, dy: 0))
        XCTAssertEqual(SubtabSwipe.destination(from: "entry", in: pages, dx: -80, dy: 0), "workout")
    }

    func testASlantedScrollStaysPut() {
        XCTAssertNil(SubtabSwipe.destination(from: "entry", in: pages, dx: -150, dy: 80))
        XCTAssertEqual(SubtabSwipe.destination(from: "entry", in: pages, dx: -150, dy: 70), "workout")
    }

    func testAPageNotInTheListStaysPut() {
        XCTAssertNil(SubtabSwipe.destination(from: "chat", in: pages, dx: -150, dy: 0))
    }
}
