import Foundation
import XCTest
@testable import LunaschalCore

final class ShortRecordingTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    private func recording(_ mode: CaptureMode = .record) throws -> Capture {
        let capture = Capture(mode: mode)
        try store.save(capture)
        try Data("audio".utf8).write(to: store.audioURL(capture))
        return capture
    }

    func testOnlyAClipUnderTheMinimumIsDiscarded() {
        XCTAssertTrue(ShortRecording.shouldDiscard(duration: 0))
        XCTAssertTrue(ShortRecording.shouldDiscard(duration: 4.99))
        XCTAssertFalse(ShortRecording.shouldDiscard(duration: 5))
        XCTAssertFalse(ShortRecording.shouldDiscard(duration: 600))
    }

    /// The phone shares `Recorder` and sets no minimum.
    func testNoMinimumKeepsEverything() {
        XCTAssertFalse(ShortRecording.shouldDiscard(duration: 0, minimum: nil))
    }

    func testTheWarningNamesWhatWasLost() {
        XCTAssertEqual(ShortRecording.warning(.record), "Recording under 5 seconds discarded")
        XCTAssertEqual(ShortRecording.warning(.transcribe), "Transcription under 5 seconds discarded")
    }

    func testDiscardLeavesNothingToSendOrRecover() throws {
        for mode in [CaptureMode.record, .transcribe] {
            let capture = try recording(mode)
            let audio = try store.audioURL(capture)
            try store.discardRecording(capture.id)

            XCTAssertFalse(FileManager.default.fileExists(atPath: audio.path))
            XCTAssertEqual(try store.list().map(\.id), [])
            // A relaunch must not bring it back as an interrupted recording.
            try CaptureStore(root: root).recoverInterruptedRecordings()
            XCTAssertEqual(try store.list().map(\.id), [])
        }
    }

    func testDiscardSurvivesAudioThatNeverReachedDisk() throws {
        let capture = Capture(mode: .record)
        try store.save(capture)
        try store.discardRecording(capture.id)
        XCTAssertEqual(try store.list().map(\.id), [])
    }

    /// A finished clip may be with the phone already, and an interrupted one
    /// is kept for the user to recover: neither may be deleted this way.
    func testDiscardRefusesAnythingThatHasStoppedRecording() throws {
        let finished = try recording()
        try store.finishRecording(finished.id)
        XCTAssertThrowsError(try store.discardRecording(finished.id))

        let interrupted = try recording()
        try store.recoverInterruptedRecordings()
        XCTAssertThrowsError(try store.discardRecording(interrupted.id))

        XCTAssertEqual(Set(try store.list().map(\.id)), [finished.id, interrupted.id])
        XCTAssertTrue(FileManager.default.fileExists(atPath: try store.audioURL(finished).path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: try store.audioURL(interrupted).path))
    }

    func testDiscardLeavesOtherRecordingsAlone() throws {
        let kept = try recording()
        try store.finishRecording(kept.id)
        let stray = try recording()
        try store.discardRecording(stray.id)
        XCTAssertEqual(try store.list().map(\.id), [kept.id])
        XCTAssertEqual(try store.load(kept.id).state, .pending)
    }
}
