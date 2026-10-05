import Foundation
import XCTest
@testable import LunaschalCore

/// Reading commentary rides the ordinary capture pipeline, carrying the
/// chapter it was spoken or typed about.
final class FicCommentaryTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func testTheChapterSurvivesARestart() throws {
        let fic = ULID.make(), chapter = ULID.make()
        let capture = Capture(text: "Great twist", ficID: fic, chapterID: chapter)
        try store.save(capture)
        let restored = try CaptureStore(root: root).load(capture.id)
        XCTAssertEqual(restored.ficID, fic)
        XCTAssertEqual(restored.chapterID, chapter)
        // A chapter alone means nothing: commentary is on a fic first.
        XCTAssertNil(Capture(text: "x", chapterID: chapter).chapterID)
    }

    func testACaptureSavedBeforeCommentaryExistedStillLoads() throws {
        let capture = Capture(text: "Old")
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(capture)) as? [String: Any])
        json.removeValue(forKey: "ficID")
        json.removeValue(forKey: "chapterID")
        let decoded = try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertEqual(decoded, capture)
        XCTAssertNil(decoded.ficID)
    }

    func testARecordingNamesItsFicAndChapterSoTheServerLinksIt() throws {
        let fic = ULID.make(), chapter = ULID.make()
        for (capture, linked) in [(Capture(mode: .transcribe, ficID: fic, chapterID: chapter), true),
                                  (Capture(mode: .transcribe), false)] {
            try Data([1, 2, 3]).write(to: store.audioURL(capture))
            let body = try RecordingMultipart(capture: capture, audioURL: store.audioURL(capture))
            defer { try? FileManager.default.removeItem(at: body.url) }
            let text = String(decoding: try Data(contentsOf: body.url), as: UTF8.self)
            XCTAssertEqual(text.contains("name=\"ficId\"\r\n\r\n\(fic)\r\n"), linked)
            XCTAssertEqual(text.contains("name=\"chapterId\"\r\n\r\n\(chapter)\r\n"), linked)
            XCTAssertTrue(text.contains("name=\"transcribe\"\r\n\r\ntrue\r\n"))
        }
    }

    func testAStagedUploadKeepsTheLink() throws {
        let uploads = try RecordingUploadStore(root: root.appendingPathComponent("uploads"),
                                               hash: { try Data(contentsOf: $0).base64EncodedString() })
        var capture = Capture(mode: .record, ficID: ULID.make(), chapterID: ULID.make())
        capture.state = .pending
        try Data([1, 2, 3]).write(to: store.audioURL(capture))
        let staged = try uploads.prepare(capture, audioURL: store.audioURL(capture),
                                         server: URL(string: "https://example.com")!)
        let text = String(decoding: try Data(contentsOf: uploads.bodyURL(staged)), as: UTF8.self)
        XCTAssertTrue(text.contains(capture.ficID!))
        XCTAssertTrue(text.contains(capture.chapterID!))
    }
}
