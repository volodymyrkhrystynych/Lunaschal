import XCTest
@testable import LunaschalCore

final class WatchComplicationTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testLinksRoundTrip() {
        for link in [WatchLink.timer, .start(.work), .start(.timeout), .record, .transcribe, .todo, .workout] {
            XCTAssertEqual(WatchLink(url: link.url), link)
        }
    }

    func testRejectsForeignAndMalformedLinks() {
        for text in ["https://timer", "lunaschal-watch://timer/break", "lunaschal-watch://timer/nap",
                     "lunaschal-watch://record/now", "lunaschal-watch://elsewhere", "lunaschal-watch://timer/work/extra",
                     "lunaschal-watch://workout/90"] {
            XCTAssertNil(WatchLink(url: URL(string: text)!), text)
        }
    }

    func testIdleShowsOneGlance() {
        XCTAssertEqual(PomodoroTimer().glances(now: now), [.init(date: now, state: .idle)])
    }

    func testRunningCarriesItsOwnEnd() {
        var timer = PomodoroTimer()
        timer.start(.work, now: now)
        let run = timer.run!
        XCTAssertEqual(timer.glances(now: now + 60), [
            .init(date: now + 60, state: .running(run)),
            .init(date: run.endsAt, state: .finished(.work)),
        ])
    }

    func testARunPastItsEndReadsFinished() {
        var timer = PomodoroTimer()
        timer.start(.timeout, now: now)
        XCTAssertEqual(timer.glances(now: now + 3600), [.init(date: now + 3600, state: .finished(.timeout))])
    }

    func testFinishedStaysFinished() {
        var timer = PomodoroTimer()
        timer.start(.timeout, now: now)
        timer.expire(now: now + 600)
        XCTAssertEqual(timer.glances(now: now + 700), [.init(date: now + 700, state: .finished(.timeout))])
    }

    func testAPressNeverReplacesARun() {
        var timer = PomodoroTimer()
        timer.start(.timeout, now: now)
        let run = timer.run
        let (press, closed) = timer.press(.work, now: now + 120)
        XCTAssertEqual(press, .alreadyGoing)
        XCTAssertEqual(closed, [])
        XCTAssertEqual(timer.run, run)
    }

    func testAPressStartsFromIdle() {
        var timer = PomodoroTimer()
        XCTAssertEqual(timer.press(.work, now: now).0, .started)
        XCTAssertEqual(timer.run?.kind, .work)
    }

    func testAPressAfterTheEndOpensTheChoicesAndLogsTheRun() {
        // Time is up but nobody has chosen: the press shows Continue / Break
        // rather than throwing the choice away.
        var timer = PomodoroTimer()
        timer.start(.work, now: now)
        let (press, closed) = timer.press(.timeout, now: now + 25 * 60 + 5)
        XCTAssertEqual(press, .alreadyGoing)
        XCTAssertEqual(closed.map(\.completed), [true])
        XCTAssertEqual(timer.state, .finished(.work))
    }

    func testRecordingStatusRoundTripsAndClears() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        let status = RecordingStatus(mode: .transcribe, startedAt: now)
        try RecordingStatus.save(status, to: url)
        XCTAssertEqual(RecordingStatus.load(from: url), status)
        try RecordingStatus.save(nil, to: url)
        XCTAssertNil(RecordingStatus.load(from: url))
        try RecordingStatus.save(nil, to: url) // clearing twice is fine
    }

    /// A To-do clip is recorded in Transcribe's mode, so only the flag keeps
    /// the Transcribe button from lighting up for it.
    func testATodoClipLightsOnlyTheTodoButton() throws {
        let todo = RecordingStatus(mode: .transcribe, asTodo: true, startedAt: now)
        XCTAssertTrue(todo.matches(mode: .transcribe, asTodo: true))
        XCTAssertFalse(todo.matches(mode: .transcribe, asTodo: false))
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        defer { try? FileManager.default.removeItem(at: url) }
        try RecordingStatus.save(todo, to: url)
        XCTAssertEqual(RecordingStatus.load(from: url), todo)
    }

    /// A status written before the To-do button existed is a plain clip.
    func testAnOlderStatusIsNotATodo() throws {
        let old = Data(#"{"mode":"transcribe","startedAt":0}"#.utf8)
        let status = try JSONDecoder().decode(RecordingStatus.self, from: old)
        XCTAssertFalse(status.asTodo)
    }

    /// Only a clip that becomes text can become a to-do, and a manifest saved
    /// before the flag existed reads as not one.
    func testOnlyATranscribeCaptureCanBeATodo() throws {
        XCTAssertTrue(Capture(mode: .transcribe, asTodo: true).asTodo)
        XCTAssertFalse(Capture(mode: .record, asTodo: true).asTodo)
        var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(Capture(mode: .transcribe, asTodo: true))) as! [String: Any]
        json.removeValue(forKey: "asTodo")
        let old = try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertFalse(old.asTodo)
    }
}
