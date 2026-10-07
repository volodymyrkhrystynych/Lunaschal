import Foundation
import XCTest
@testable import LunaschalCore

final class PomodoroTests: XCTestCase {
    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func testWorkRunsTwentyFiveMinutesThenOffersContinueAndBreak() throws {
        var timer = PomodoroTimer()
        XCTAssertTrue(timer.start(.work, now: t0).isEmpty)
        let run = try XCTUnwrap(timer.run)
        XCTAssertEqual(run.endsAt, t0 + 25 * 60)
        XCTAssertTrue(timer.expire(now: t0 + 60).isEmpty, "not up yet")

        let closed = timer.expire(now: t0 + 26 * 60)
        XCTAssertEqual(closed, [PomodoroSession(id: run.id, kind: .work, startedAt: t0, endedAt: t0 + 25 * 60,
                                                plannedSeconds: 1500, completed: true)])
        XCTAssertEqual(timer.state, .finished(.work))
        XCTAssertEqual(timer.choices, [.continue, .takeBreak])
    }

    func testBreakIsFiveMinutesAndContinueGoesBackToWork() {
        var timer = PomodoroTimer()
        timer.start(.work, now: t0)
        timer.expire(now: t0 + 1500)
        XCTAssertTrue(timer.takeBreak(now: t0 + 1510).isEmpty)
        XCTAssertEqual(timer.run?.kind, .break)
        XCTAssertEqual(timer.run?.endsAt, t0 + 1510 + 300)

        let closed = timer.continue(now: t0 + 1900)
        XCTAssertEqual(closed.map(\.kind), [.break])
        XCTAssertEqual(timer.run?.kind, .work)
    }

    func testTimeoutRepeatsAndHasNoBreak() {
        var timer = PomodoroTimer()
        timer.start(.timeout, now: t0)
        XCTAssertEqual(timer.run?.endsAt, t0 + 600)
        timer.expire(now: t0 + 600)
        XCTAssertEqual(timer.choices, [.continue])
        XCTAssertTrue(timer.takeBreak(now: t0 + 601).isEmpty)
        XCTAssertEqual(timer.state, .finished(.timeout), "Break does nothing after a timeout")
        timer.continue(now: t0 + 610)
        XCTAssertEqual(timer.run?.kind, .timeout)
    }

    /// A notification button can arrive before the app noticed the time was up.
    func testContinueFromANotificationClosesTheRunFirst() {
        var timer = PomodoroTimer()
        timer.start(.work, now: t0)
        let closed = timer.takeBreak(now: t0 + 1600)
        XCTAssertEqual(closed.map(\.completed), [true])
        XCTAssertEqual(closed.first?.endedAt, t0 + 1500)
        XCTAssertEqual(timer.run?.kind, .break)
    }

    func testCancelLogsWhatWasDoneUnlessItWasAMisTap() {
        var timer = PomodoroTimer()
        timer.start(.work, now: t0)
        XCTAssertTrue(timer.cancel(now: t0 + 20).isEmpty)
        XCTAssertEqual(timer.state, .idle)

        timer.start(.work, now: t0)
        let closed = timer.cancel(now: t0 + 600)
        XCTAssertEqual(closed.count, 1)
        XCTAssertEqual(closed.first?.completed, false)
        XCTAssertEqual(closed.first?.endedAt, t0 + 600)
        XCTAssertEqual(closed.first?.plannedSeconds, 1500)
        XCTAssertEqual(timer.state, .idle)
    }

    func testCancelAfterTheTimeIsUpIsACompletedRun() {
        var timer = PomodoroTimer()
        timer.start(.timeout, now: t0)
        XCTAssertEqual(timer.cancel(now: t0 + 700).map(\.completed), [true])
        XCTAssertEqual(timer.state, .idle)
        XCTAssertTrue(timer.cancel(now: t0 + 800).isEmpty, "nothing left to close")
    }

    func testStartingOverCancelsTheRunningOne() {
        var timer = PomodoroTimer()
        timer.start(.work, now: t0)
        let closed = timer.start(.timeout, now: t0 + 300)
        XCTAssertEqual(closed.map(\.kind), [.work])
        XCTAssertEqual(closed.first?.completed, false)
        XCTAssertEqual(timer.run?.kind, .timeout)
    }

    func testShortenedLengthsForTheSimulator() {
        var timer = PomodoroTimer(shortenedTo: 10)
        timer.start(.work, now: t0)
        XCTAssertEqual(timer.run?.endsAt, t0 + 10)
    }

    func testTheTimerSurvivesARelaunch() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertNil(PomodoroTimer.load(from: url))
        var timer = PomodoroTimer()
        timer.start(.work, now: t0)
        try timer.save(to: url)
        var restored = try XCTUnwrap(PomodoroTimer.load(from: url))
        XCTAssertEqual(restored, timer)
        // Relaunched after the time ran out: it is simply finished.
        XCTAssertEqual(restored.expire(now: t0 + 3600).map(\.completed), [true])
        XCTAssertEqual(restored.state, .finished(.work))
    }
}

final class PomodoroSyncTests: XCTestCase {
    private var root: URL!
    private var store: PomodoroStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try PomodoroStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func session(_ minutes: Double) -> PomodoroSession {
        let start = Date(timeIntervalSince1970: 1_800_000_000 + minutes * 60)
        return PomodoroSession(id: ULID.make(now: start), kind: .work, startedAt: start,
                               endedAt: start + 1500, plannedSeconds: 1500, completed: true)
    }

    private final class Fake: PomodoroTransport {
        var sent: [String] = []
        var fail: [String: Error] = [:]
        func sendPomodoro(_ session: PomodoroSession) async throws {
            if let error = fail[session.id] { throw error }
            sent.append(session.id)
        }
    }

    @MainActor
    func testUploadsOldestFirstAndEmptiesTheOutbox() async throws {
        let late = session(60), early = session(0)
        try store.save(late); try store.save(early)
        let fake = Fake()
        let refused = try await PomodoroSync(store: store).run(using: fake)
        XCTAssertEqual(refused, 0)
        XCTAssertEqual(fake.sent, [early.id, late.id])
        XCTAssertTrue(try store.list().isEmpty)
    }

    @MainActor
    func testARefusedSessionIsDroppedAndAnOutageKeepsTheRest() async throws {
        let bad = session(0), offline = session(30), later = session(60)
        for item in [bad, offline, later] { try store.save(item) }
        let fake = Fake()
        fake.fail = [bad.id: HTTPFailure(status: 400), offline.id: URLError(.notConnectedToInternet)]
        do {
            _ = try await PomodoroSync(store: store).run(using: fake)
            XCTFail("an outage should stop the pass")
        } catch {}
        XCTAssertEqual(try store.list().map(\.id), [offline.id, later.id])

        fake.fail = [:]
        try await PomodoroSync(store: store).run(using: fake)
        XCTAssertEqual(fake.sent, [offline.id, later.id])
    }

    func testTheStoreRefusesABadID() {
        let bad = PomodoroSession(id: "../x", kind: .work, startedAt: Date(), endedAt: Date(),
                                  plannedSeconds: 1, completed: true)
        XCTAssertThrowsError(try store.save(bad))
    }

    func testTheAcknowledgementMustNameTheSession() throws {
        let item = session(0)
        XCTAssertNoThrow(try JournalAPI.validatePomodoroAcknowledgement(Data("{\"id\":\"\(item.id)\"}".utf8), for: item))
        XCTAssertThrowsError(try JournalAPI.validatePomodoroAcknowledgement(Data("{\"id\":\"other\"}".utf8), for: item))
    }
}
