import XCTest
@testable import LunaschalCore

final class ReaderTopJumpTests: XCTestCase {
    /// Feeds one scroll at 60 frames a second from `from` to `to`.
    private func scroll(_ jump: inout ReaderTopJump, from: Double, to: Double, pointsPerSecond: Double,
                        startingAt start: Double = 0) -> Double {
        let frame = 1.0 / 60
        let step = pointsPerSecond * frame * (to < from ? -1 : 1)
        var offset = from, time = start
        jump.scrolled(offset: offset, at: time)
        while to < from ? offset > to : offset < to {
            offset = to < from ? max(to, offset + step) : min(to, offset + step)
            time += frame
            jump.scrolled(offset: offset, at: time)
        }
        return time
    }

    func testAFastLongFlickUpShowsIt() {
        var jump = ReaderTopJump()
        _ = scroll(&jump, from: 5000, to: 4000, pointsPerSecond: 3000)
        XCTAssertTrue(jump.isShown)
    }

    func testDroppedFramesDoNotLoseAFlick() {
        var jump = ReaderTopJump()
        jump.scrolled(offset: 5000, at: 0)
        jump.scrolled(offset: 4400, at: 0.2)
        XCTAssertTrue(jump.isShown)
    }

    func testAPauseIsNotPartOfAFlick() {
        var jump = ReaderTopJump()
        jump.scrolled(offset: 5000, at: 0)
        jump.scrolled(offset: 4600, at: 2)
        XCTAssertFalse(jump.isShown)
    }

    func testReadingSpeedDoesNot() {
        var jump = ReaderTopJump()
        _ = scroll(&jump, from: 5000, to: 1000, pointsPerSecond: 800)
        XCTAssertFalse(jump.isShown, "however far it goes")
    }

    func testAFastShortMoveDoesNot() {
        var jump = ReaderTopJump()
        _ = scroll(&jump, from: 5000, to: 4850, pointsPerSecond: 4000)
        XCTAssertFalse(jump.isShown, "the bounce off the bottom of a chapter is this")
    }

    func testAFastFlickNearTheTopDoesNot() {
        var jump = ReaderTopJump()
        _ = scroll(&jump, from: 600, to: 250, pointsPerSecond: 5000)
        XCTAssertFalse(jump.isShown)
    }

    func testASlowStartDoesNotHideAFlick() {
        var jump = ReaderTopJump()
        let slowEnd = scroll(&jump, from: 6000, to: 5800, pointsPerSecond: 200)
        XCTAssertFalse(jump.isShown)
        _ = scroll(&jump, from: 5800, to: 4800, pointsPerSecond: 3000, startingAt: slowEnd)
        XCTAssertTrue(jump.isShown, "only the last moments are measured")
    }

    func testReversingRestartsTheRun() {
        var jump = ReaderTopJump()
        let up = scroll(&jump, from: 5000, to: 4850, pointsPerSecond: 3000)
        let down = scroll(&jump, from: 4850, to: 4860, pointsPerSecond: 3000, startingAt: up)
        _ = scroll(&jump, from: 4860, to: 4710, pointsPerSecond: 3000, startingAt: down)
        XCTAssertFalse(jump.isShown, "two short moves are not one long one")
    }

    func testScrollingDownHidesIt() {
        var jump = ReaderTopJump()
        let time = scroll(&jump, from: 5000, to: 4000, pointsPerSecond: 3000)
        let settled = scroll(&jump, from: 4000, to: 4030, pointsPerSecond: 100, startingAt: time)
        XCTAssertTrue(jump.isShown, "a nudge isn't scrolling on")
        _ = scroll(&jump, from: 4030, to: 4050, pointsPerSecond: 100, startingAt: settled)
        XCTAssertFalse(jump.isShown)
    }

    func testReachingTheTopHidesIt() {
        var jump = ReaderTopJump()
        let time = scroll(&jump, from: 5000, to: 4000, pointsPerSecond: 3000)
        XCTAssertTrue(jump.isShown)
        _ = scroll(&jump, from: 4000, to: 200, pointsPerSecond: 3000, startingAt: time)
        XCTAssertFalse(jump.isShown)
    }

    func testResetForgetsTheRun() {
        var jump = ReaderTopJump()
        _ = scroll(&jump, from: 5000, to: 4000, pointsPerSecond: 3000)
        jump.reset()
        XCTAssertEqual(jump, ReaderTopJump())
    }
}
