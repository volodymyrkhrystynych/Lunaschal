import XCTest
@testable import LunaschalCore

final class WatchComplicationTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testLinksRoundTrip() {
        for link in [WatchLink.home, .record, .transcribe, .todo] {
            XCTAssertEqual(WatchLink(url: link.url), link)
        }
    }

    /// Links a face still carries from a build with timers open nothing.
    func testRejectsForeignMalformedAndRetiredLinks() {
        for text in ["https://home", "lunaschal-watch://record/now", "lunaschal-watch://elsewhere",
                     "lunaschal-watch://timer", "lunaschal-watch://timer/work", "lunaschal-watch://workout"] {
            XCTAssertNil(WatchLink(url: URL(string: text)!), text)
        }
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
