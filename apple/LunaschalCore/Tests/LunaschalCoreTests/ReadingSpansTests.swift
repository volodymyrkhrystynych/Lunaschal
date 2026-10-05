import Foundation
import XCTest
@testable import LunaschalCore

/// The cases from src/lib/readingSpans.test.ts, so the two readers record
/// the same spans for the same scrolling.
final class ReadingSpansTests: XCTestCase {
    private var counter = 0

    private func scroll(_ state: inout ReadingSpanState, _ now: Int, _ fraction: Double = 0.5,
                        chapter: String = "ch1") -> ReadingSpan? {
        state.recordScroll(now: now, fraction: fraction, ficId: "fic", chapterId: chapter,
                           newId: { self.counter += 1; return "span\(self.counter)" })
    }

    func testAccumulatesActiveTimeBetweenScrolls() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000, 0.1)
        _ = scroll(&state, 1030, 0.2)
        _ = scroll(&state, 1100, 0.3)
        XCTAssertEqual(state.span?.startedAt, 1000)
        XCTAssertEqual(state.span?.endedAt, 1100)
        XCTAssertEqual(state.span?.activeSeconds, 100)
        XCTAssertEqual(state.span?.startFraction, 0.1)
        XCTAssertEqual(state.span?.endFraction, 0.3)
    }

    func testCapsTheCreditForALongPause() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000)
        _ = scroll(&state, 1000 + ReadingSpans.idleGapSeconds)
        XCTAssertEqual(state.span?.activeSeconds, ReadingSpans.gapCapSeconds)
    }

    func testIdleGapStartsANewSpanAndHandsBackTheOld() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000)
        _ = scroll(&state, 1060)
        let first = state.span!.id
        let closed = scroll(&state, 1060 + ReadingSpans.idleGapSeconds + 1)
        XCTAssertEqual(closed?.id, first)
        XCTAssertEqual(closed?.activeSeconds, 60)
        XCTAssertEqual(state.span?.startedAt, 1060 + ReadingSpans.idleGapSeconds + 1)
        XCTAssertEqual(state.span?.activeSeconds, 0)
        XCTAssertNotEqual(state.span?.id, first)
    }

    func testAChapterChangeStartsANewSpan() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000)
        _ = scroll(&state, 1060)
        let closed = scroll(&state, 1070, 0, chapter: "ch2")
        XCTAssertEqual(closed?.chapterId, "ch1")
        XCTAssertEqual(state.span?.chapterId, "ch2")
    }

    func testASingleScrollIsNeverSent() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000)
        var copy = state
        XCTAssertNil(copy.takeFlush(now: 5000, force: true))
        copy = state
        XCTAssertNil(copy.close())
        XCTAssertNil(scroll(&state, 9000))
    }

    func testFlushesOnTheIntervalOrWhenForcedAndOnlyWhenChanged() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000)
        _ = scroll(&state, 1030)
        XCTAssertNil(state.takeFlush(now: 1030))
        var forced = state
        XCTAssertNotNil(forced.takeFlush(now: 1030, force: true))
        XCTAssertEqual(state.takeFlush(now: 1000 + ReadingSpans.flushIntervalSeconds)?.activeSeconds, 30)
        // Nothing changed since: nothing to send, even forced.
        var unchanged = state
        XCTAssertNil(unchanged.takeFlush(now: 9999, force: true))
        _ = scroll(&state, 1100)
        XCTAssertEqual(state.close()?.activeSeconds, 100)
        XCTAssertNil(state.span)
    }

    func testAClockThatWentBackwardsStartsFresh() {
        var state = ReadingSpanState()
        _ = scroll(&state, 1000)
        _ = scroll(&state, 900)
        XCTAssertEqual(state.span?.startedAt, 900)
        XCTAssertEqual(state.span?.activeSeconds, 0)
    }

    func testTheSpanEncodesTheFieldsTheServerReads() throws {
        let span = ReadingSpan(id: ULID.make(), ficId: ULID.make(), chapterId: ULID.make(), startedAt: 10,
                               endedAt: 70, activeSeconds: 60, startFraction: 0, endFraction: 0.25)
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(span)) as! [String: Any]
        for key in ["chapterId", "startedAt", "endedAt", "activeSeconds", "startFraction", "endFraction"] {
            XCTAssertNotNil(body[key], key)
        }
    }
}

final class FicActivityTests: XCTestCase {
    private var root: URL!
    private var store: FicActivityStore!
    private let fic = ULID.make(), chapter = ULID.make()

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try FicActivityStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func span(_ id: String, endedAt: Int) -> FicActivity {
        .span(ReadingSpan(id: id, ficId: fic, chapterId: chapter, startedAt: 10, endedAt: endedAt,
                          activeSeconds: endedAt - 10, startFraction: 0, endFraction: 0.5))
    }

    func testOnlyTheNewestHeartbeatAndLastChapterAreKeptAcrossRestart() throws {
        let id = ULID.make()
        try store.enqueue(span(id, endedAt: 70))
        try store.enqueue(span(id, endedAt: 130))
        try store.enqueue(span(id, endedAt: 100)) // late, older heartbeat
        try store.enqueue(.progress(ficId: fic, chapterId: ULID.make()))
        try store.enqueue(.progress(ficId: fic, chapterId: chapter))
        let reopened = try FicActivityStore(root: root).list()
        XCTAssertEqual(reopened.count, 2)
        XCTAssertTrue(reopened.contains(span(id, endedAt: 130)))
        XCTAssertTrue(reopened.contains(.progress(ficId: fic, chapterId: chapter)))
        XCTAssertThrowsError(try store.enqueue(.progress(ficId: "../x", chapterId: chapter)))
    }

    @MainActor
    func testSentAndRefusedItemsLeaveRetryableOnesStay() async throws {
        let sent = ULID.make(), refused = ULID.make()
        try store.enqueue(span(sent, endedAt: 70))
        try store.enqueue(span(refused, endedAt: 70))
        let server = FakeReader(statuses: [refused: 404])
        try await FicActivitySync(store: store).run(using: server)
        XCTAssertEqual(try store.list(), [])
        XCTAssertEqual(Set(server.sent), [sent, refused])

        let offline = ULID.make()
        try store.enqueue(span(offline, endedAt: 70))
        do {
            try await FicActivitySync(store: store).run(using: FakeReader(statuses: [offline: 503]))
            XCTFail("a retryable failure should stop the pass")
        } catch {}
        XCTAssertEqual(try store.list(), [span(offline, endedAt: 70)])
    }

    func testANewerHeartbeatQueuedWhileSendingSurvivesTheAcknowledgement() throws {
        let id = ULID.make()
        try store.enqueue(span(id, endedAt: 70))
        let inFlight = try XCTUnwrap(store.list().first)
        try store.enqueue(span(id, endedAt: 130))
        try store.remove(inFlight)
        XCTAssertEqual(try store.list(), [span(id, endedAt: 130)])
    }
}

private final class FakeReader: FicActivityTransport {
    let statuses: [String: Int]
    var sent: [String] = []
    init(statuses: [String: Int]) { self.statuses = statuses }

    func sendFicActivity(_ item: FicActivity) async throws {
        guard case .span(let span) = item else { return }
        sent.append(span.id)
        if let status = statuses[span.id] { throw HTTPFailure(status: status) }
    }
}
