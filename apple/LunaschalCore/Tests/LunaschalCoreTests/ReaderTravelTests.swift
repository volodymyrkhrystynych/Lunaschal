import XCTest
@testable import LunaschalCore

final class ReaderTravelTests: XCTestCase {
    func testNothingCountsBeforeTheReaderScrolls() {
        XCTAssertFalse(ReaderTravel().coveredScreen(offset: 5_000, screen: 800))
    }

    func testAResumeNearTheEndDoesNotCountAsReadingToIt() {
        var travel = ReaderTravel()
        travel.scrolled(offset: 9_000)
        travel.scrolled(offset: 9_040)
        XCTAssertFalse(travel.coveredScreen(offset: 9_040, screen: 800))
    }

    func testAScreenOfScrollingCounts() {
        var travel = ReaderTravel()
        travel.scrolled(offset: 1_000)
        travel.scrolled(offset: 1_799)
        XCTAssertFalse(travel.coveredScreen(offset: 1_799, screen: 800))
        travel.scrolled(offset: 1_800)
        XCTAssertTrue(travel.coveredScreen(offset: 1_800, screen: 800))
    }

    func testScrollingBackUpToRereadCounts() {
        var travel = ReaderTravel()
        travel.scrolled(offset: 9_000)
        travel.scrolled(offset: 8_000)
        travel.scrolled(offset: 9_000)
        XCTAssertTrue(travel.coveredScreen(offset: 9_000, screen: 800))
    }

    func testAnUnmeasuredScreenCountsForNothing() {
        var travel = ReaderTravel()
        travel.scrolled(offset: 0)
        XCTAssertFalse(travel.coveredScreen(offset: 500, screen: 0))
    }
}
