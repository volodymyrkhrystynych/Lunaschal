import Foundation
import XCTest
@testable import LunaschalCore

final class RestTimerTests: XCTestCase {
    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func testRunsNinetySecondsThenRingsFromItsEnd() {
        var timer = RestTimer()
        timer.start(now: t0)
        XCTAssertEqual(timer.state, .running(startedAt: t0, endsAt: t0 + 90))
        timer.expire(now: t0 + 89)
        XCTAssertFalse(timer.isRinging, "not up yet")
        timer.expire(now: t0 + 120)
        XCTAssertEqual(timer.state, .ringing(since: t0 + 90))
    }

    func testExpiresExactlyAtTheEnd() {
        var timer = RestTimer()
        timer.start(now: t0)
        timer.expire(now: t0 + 90)
        XCTAssertTrue(timer.isRinging)
    }

    func testRingsUntilStopped() {
        var timer = RestTimer()
        timer.start(now: t0)
        timer.expire(now: t0 + 90)
        timer.expire(now: t0 + 3600)
        XCTAssertEqual(timer.state, .ringing(since: t0 + 90), "ringing never ends on its own")
        timer.stop()
        XCTAssertEqual(timer.state, .idle)
    }

    func testRestartFromRingingAndFromRunning() {
        var timer = RestTimer()
        timer.start(now: t0)
        timer.start(now: t0 + 30)
        XCTAssertEqual(timer.endsAt, t0 + 120, "restart while running gives a fresh 90")
        timer.expire(now: t0 + 200)
        timer.start(now: t0 + 210)
        XCTAssertEqual(timer.state, .running(startedAt: t0 + 210, endsAt: t0 + 300))
    }

    func testStopWhileRunning() {
        var timer = RestTimer()
        timer.start(now: t0)
        timer.stop()
        timer.expire(now: t0 + 200)
        XCTAssertEqual(timer.state, .idle)
    }

    func testShortenedLength() {
        var timer = RestTimer(shortenedTo: 5)
        timer.start(now: t0)
        XCTAssertEqual(timer.endsAt, t0 + 5)
    }

    func testGlancesCarryTheEnd() {
        var timer = RestTimer()
        XCTAssertEqual(timer.glances(now: t0), [.init(date: t0, state: .idle)])
        timer.start(now: t0)
        XCTAssertEqual(timer.glances(now: t0 + 10), [
            .init(date: t0 + 10, state: .running(startedAt: t0, endsAt: t0 + 90)),
            .init(date: t0 + 90, state: .ringing(since: t0 + 90)),
        ])
        XCTAssertEqual(timer.glances(now: t0 + 100), [.init(date: t0 + 100, state: .ringing(since: t0 + 90))])
    }

    func testRoundTrips() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        var timer = RestTimer()
        timer.start(now: t0)
        try timer.save(to: url)
        XCTAssertEqual(RestTimer.load(from: url), timer)
        timer.expire(now: t0 + 100)
        try timer.save(to: url)
        XCTAssertEqual(RestTimer.load(from: url), timer)
    }
}
