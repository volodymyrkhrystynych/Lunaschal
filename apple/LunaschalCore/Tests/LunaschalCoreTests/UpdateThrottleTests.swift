import XCTest
@testable import LunaschalCore

final class UpdateThrottleTests: XCTestCase {
    func testFirstPieceGoesStraightThrough() {
        var stream = StreamCoalescer(interval: 0.1)
        XCTAssertEqual(stream.add("Hel", now: 10), "Hel")
    }

    func testPiecesInsideTheIntervalAreGatheredAndHandedOverTogether() {
        var stream = StreamCoalescer(interval: 0.1)
        XCTAssertEqual(stream.add("a", now: 0), "a")
        XCTAssertNil(stream.add("b", now: 0.02))
        XCTAssertNil(stream.add("c", now: 0.05))
        XCTAssertEqual(stream.add("d", now: 0.11), "bcd")
    }

    func testNothingIsLostWhenTheStreamEnds() {
        var stream = StreamCoalescer(interval: 0.1)
        var shown = ""
        // A hundred tokens a second for two seconds, as a fast reply streams.
        for index in 0..<200 {
            if let piece = stream.add("t\(index) ", now: Double(index) * 0.01) { shown += piece }
        }
        if let rest = stream.drain() { shown += rest }
        XCTAssertEqual(shown, (0..<200).map { "t\($0) " }.joined())
    }

    func testAFastStreamIsPublishedAboutTenTimesASecondNotPerToken() {
        var stream = StreamCoalescer(interval: 0.1)
        var publishes = 0
        for index in 0..<200 where stream.add("x", now: Double(index) * 0.01) != nil { publishes += 1 }
        XCTAssertLessThanOrEqual(publishes, 21)
        XCTAssertGreaterThanOrEqual(publishes, 19)
    }

    func testDrainIsEmptyOnceTaken() {
        var stream = StreamCoalescer(interval: 0.1)
        _ = stream.add("a", now: 0)
        _ = stream.add("b", now: 0.01)
        XCTAssertEqual(stream.drain(), "b")
        XCTAssertNil(stream.drain())
    }

    func testProgressPassesOncePerInterval() {
        var gate = ProgressThrottle(interval: 0.5)
        XCTAssertTrue(gate.ready(now: 0))
        XCTAssertFalse(gate.ready(now: 0.2))
        XCTAssertFalse(gate.ready(now: 0.49))
        XCTAssertTrue(gate.ready(now: 0.5))
    }

    func testProgressResetLetsTheNextReportThrough() {
        var gate = ProgressThrottle(interval: 0.5)
        XCTAssertTrue(gate.ready(now: 0))
        gate.reset()
        XCTAssertTrue(gate.ready(now: 0.1))
    }
}
