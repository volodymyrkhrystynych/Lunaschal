import Foundation
import XCTest
@testable import LunaschalCore

final class TranscriptTests: XCTestCase {
    func testOriginalDictationAndPolishedTextStayDistinctAndTranscriptUsesRecordingIdentity() throws {
        var capture = Capture(mode: .transcribe)
        let payload: [String: Any] = [
            "id": capture.id, "content": "Polished journal entry", "rawContent": "Original spoken words",
            "attachments": [
                ["id": ULID.make(), "transcriptStatus": "running", "transcript": "Another attachment"],
                ["id": capture.attachmentID!, "transcriptStatus": "done", "transcript": "Original spoken words"],
            ],
        ]
        capture.snapshot = try JSONDecoder().decode(JournalSnapshot.self, from: JSONSerialization.data(withJSONObject: payload))
        let reopened = try JSONDecoder().decode(Capture.self, from: JSONEncoder().encode(capture))
        XCTAssertEqual(reopened.snapshot?.content, "Polished journal entry")
        XCTAssertEqual(reopened.snapshot?.rawContent, "Original spoken words")
        XCTAssertEqual(reopened.recordingTranscript?.transcript, "Original spoken words")
        XCTAssertEqual(reopened.recordingTranscript?.transcriptStatus, "done")
    }

    func testLegacySnapshotsAndMissingRecordingNeverSelectUnrelatedTranscript() throws {
        var capture = Capture(mode: .transcribe)
        let payload: [String: Any] = [
            "id": capture.id, "content": "Legacy entry",
            "attachments": [["id": ULID.make(), "transcript": "Unrelated"]],
        ]
        capture.snapshot = try JSONDecoder().decode(JournalSnapshot.self, from: JSONSerialization.data(withJSONObject: payload))
        XCTAssertNil(capture.snapshot?.rawContent)
        XCTAssertNil(capture.recordingTranscript)
    }
}
