import XCTest
@testable import LunaschalCore

final class SyncTimingsTests: XCTestCase {
    func testSummaryNamesTheTotalTheStallAndTheSlowestSteps() {
        var timings = SyncTimings()
        timings.record("uploads", seconds: 0.9)
        timings.record("todos", seconds: 0.01)
        timings.record("journal", seconds: 2.1)
        timings.record("weather", seconds: 0.4)
        timings.record("calendar", seconds: 0.2)
        timings.longestStall = 1.3
        XCTAssertEqual(timings.summary(),
                       "Last sync 3.6 s · UI held up to 1.3 s · slowest: journal 2.1 s, uploads 0.9 s, weather 0.4 s")
    }

    func testAQuickPassLeavesOutWhatDidNotTakeTime() {
        var timings = SyncTimings()
        timings.record("uploads", seconds: 0.02)
        timings.longestStall = 0.03
        XCTAssertEqual(timings.summary(), "Last sync 0.0 s")
        timings.record("journal", seconds: -1)
        XCTAssertEqual(timings.total, 0.02, accuracy: 0.0001)
    }

    func testLongPassesRoundToWholeSeconds() {
        var timings = SyncTimings()
        timings.record("library", seconds: 42.4)
        XCTAssertEqual(timings.summary(), "Last sync 42 s · slowest: library 42 s")
    }
}
