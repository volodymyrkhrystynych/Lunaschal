import Foundation
import XCTest
@testable import LunaschalCore

final class CaptureFileTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func testPhotoOnlyEntrySurvivesRestartWithItsBytes() throws {
        let photo = try store.stageFile(data: Data("jpeg bytes".utf8), name: "Photo.jpg", contentType: "image/jpeg")
        let capture = try store.commitDraft(text: "", youtubeURLs: [])
        XCTAssertEqual(capture.files, [photo])
        let restored = try CaptureStore(root: root).load(capture.id)
        XCTAssertEqual(restored.files, [photo])
        XCTAssertTrue(restored.files[0].isImage)
        XCTAssertEqual(try Data(contentsOf: store.fileURL(photo)), Data("jpeg bytes".utf8))
        XCTAssertTrue(restored.matchesSearch("photo.jpg"))
    }

    func testEntryWithNothingInItIsStillRefused() {
        XCTAssertThrowsError(try store.save(Capture(text: "  "))) {
            XCTAssertEqual($0 as? CaptureError, .emptyText)
        }
    }

    func testMissingOrEmptyFilesCannotBeSaved() throws {
        let photo = try store.stageFile(data: Data("x".utf8), name: "a.png", contentType: "image/png")
        try store.discardStaged(photo)
        XCTAssertThrowsError(try store.save(Capture(files: [photo])))
        XCTAssertThrowsError(try store.stageFile(data: Data(), name: "empty.txt", contentType: "text/plain"))
        let empty = root.appendingPathComponent("empty-source")
        try Data().write(to: empty)
        XCTAssertThrowsError(try store.stageFile(from: empty, name: "empty", contentType: nil))
        // A refused stage leaves nothing behind.
        let staged = (try? FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("files").path)) ?? []
        XCTAssertEqual(staged, [])
    }

    func testPickedJSONFileIsNotMistakenForAManifest() throws {
        let source = root.appendingPathComponent("notes.json")
        try Data("{\"not\":\"a capture\"}".utf8).write(to: source)
        let file = try store.stageFile(from: source, name: "notes.json", contentType: "application/json")
        try FileManager.default.removeItem(at: source)
        let capture = Capture(text: "With notes", files: [file])
        try store.save(capture)
        XCTAssertEqual(try store.list(), [capture])
    }

    func testOldManifestWithoutFilesDecodes() throws {
        let capture = Capture(text: "Before files")
        var old = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(capture)) as? [String: Any])
        old.removeValue(forKey: "files")
        XCTAssertEqual(try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: old)), capture)
    }

    func testMultipartCarriesIdNameTypeAndBytesWithHeaderSafeName() throws {
        let file = try store.stageFile(data: Data("PDFDATA".utf8), name: "Re\"port\r\n.pdf", contentType: "application/pdf")
        let capture = Capture(text: "Report", files: [file])
        let body = try AttachmentMultipart(capture: capture, file: file, fileURL: store.fileURL(file))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = try XCTUnwrap(String(data: Data(contentsOf: body.url), encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"attachmentId\"\r\n\r\n\(file.attachmentID)\r\n"))
        XCTAssertTrue(text.contains("filename=\"Report.pdf\"\r\nContent-Type: application/pdf\r\n\r\nPDFDATA\r\n--\(body.boundary)--"))
    }

    func testFileAcknowledgementMustNameTheFileAndEntry() throws {
        let file = try store.stageFile(data: Data("x".utf8), name: "a.png", contentType: "image/png")
        let capture = Capture(files: [file])
        let good = try JSONEncoder().encode(["id": file.attachmentID, "entryId": capture.id])
        XCTAssertNoThrow(try JournalAPI.validateFileAcknowledgement(good, for: capture, file: file))
        let wrong = try JSONEncoder().encode(["id": ULID.make(), "entryId": capture.id])
        XCTAssertThrowsError(try JournalAPI.validateFileAcknowledgement(wrong, for: capture, file: file))
    }

    @MainActor
    func testSyncHandsTheTransportEachStoredFileInOrder() async throws {
        let first = try store.stageFile(data: Data("one".utf8), name: "1.jpg", contentType: "image/jpeg")
        let second = try store.stageFile(data: Data("two".utf8), name: "2.pdf", contentType: "application/pdf")
        let capture = try store.commitDraft(text: "Two files", youtubeURLs: [])
        XCTAssertEqual(capture.files, [first, second])
        let transport = FileRecordingTransport()
        try await CaptureSync(store: store).run(using: transport)
        XCTAssertEqual(transport.sent.map { try? Data(contentsOf: $0) }, [Data("one".utf8), Data("two".utf8)])
        XCTAssertEqual(try store.load(capture.id).state, .synced)
        // The originals stay on the device after syncing, as recordings do.
        XCTAssertEqual(try Data(contentsOf: store.fileURL(first)), Data("one".utf8))
    }

    // MARK: Draft clips

    private func record(_ clip: CaptureClip, _ bytes: String = "aac") throws {
        try Data(bytes.utf8).write(to: store.clipURL(clip))
    }

    func testDraftKeepsClipsAndFilesAcrossRestartUntilSaved() throws {
        let clip = try store.beginClip(transcribe: true)
        XCTAssertEqual(try store.draft().clips.first?.state, .recording)
        try record(clip)
        try store.finishClip(clip.attachmentID)
        let photo = try store.stageFile(data: Data("jpg".utf8), name: "p.jpg", contentType: "image/jpeg")

        let reopened = try CaptureStore(root: root)
        let draft = try reopened.draft()
        XCTAssertEqual(draft.clips.map(\.attachmentID), [clip.attachmentID])
        XCTAssertEqual(draft.clips.first?.state, .ready)
        XCTAssertEqual(draft.files, [photo])
        // Nothing is queued for sync while it is only a draft.
        XCTAssertEqual(try reopened.list(), [])

        let capture = try reopened.commitDraft(text: "  Thoughts  ", youtubeURLs: ["https://youtu.be/aircAruvnKk"])
        XCTAssertEqual(capture.text, "Thoughts")
        XCTAssertEqual(capture.clips.map(\.attachmentID), [clip.attachmentID])
        XCTAssertEqual(capture.files, [photo])
        XCTAssertEqual(capture.links.map(\.url), ["https://www.youtube.com/watch?v=aircAruvnKk"])
        XCTAssertTrue(try reopened.draft().isEmpty)
        XCTAssertEqual(try reopened.list(), [capture])
        // The audio is referenced where it was recorded, not moved.
        XCTAssertEqual(try Data(contentsOf: reopened.clipURL(capture.clips[0])), Data("aac".utf8))
    }

    func testNotebookPagesSaveWithoutTouchingTheComposerDraft() throws {
        let clip = try store.beginClip(transcribe: true)
        try record(clip)
        try store.finishClip(clip.attachmentID)
        let staged = try store.stageFile(data: Data("staged".utf8), name: "s.jpg", contentType: "image/jpeg")

        let capture = try store.commitImages(text: "Toronto Star, 2026-10-06",
                                             youtubeURLs: ["https://youtu.be/aircAruvnKk"],
                                             images: [(Data("p1".utf8), "Notes p1.jpg"), (Data("p2".utf8), "Notes p2.jpg")])
        XCTAssertEqual(capture.files.map(\.name), ["Notes p1.jpg", "Notes p2.jpg"])
        XCTAssertTrue(capture.files.allSatisfy(\.isImage))
        XCTAssertEqual(capture.clips, [])
        XCTAssertEqual(capture.links.map(\.url), ["https://www.youtube.com/watch?v=aircAruvnKk"])
        XCTAssertEqual(try Data(contentsOf: store.fileURL(capture.files[1])), Data("p2".utf8))
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id), capture)
        // The half-written text entry's clip and photo are still waiting.
        let draft = try store.draft()
        XCTAssertEqual(draft.clips.map(\.attachmentID), [clip.attachmentID])
        XCTAssertEqual(draft.files, [staged])
    }

    func testNotebookSaveWithABadLinkLeavesNoFilesBehind() throws {
        let before = Set((try? FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("files").path)) ?? [])
        XCTAssertThrowsError(try store.commitImages(text: "", youtubeURLs: ["https://example.com"],
                                                    images: [(Data("p1".utf8), "Notes p1.jpg")]))
        let after = Set((try? FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("files").path)) ?? [])
        XCTAssertEqual(after, before)
        XCTAssertEqual(try store.list(), [])
    }

    func testClipOnlyDraftSavesAsAnEntryWithNoText() throws {
        let clip = try store.beginClip(transcribe: false)
        try record(clip)
        try store.finishClip(clip.attachmentID)
        let capture = try store.commitDraft(text: "", youtubeURLs: [])
        XCTAssertEqual(capture.text, "")
        XCTAssertEqual(capture.mode, .text)
        XCTAssertFalse(capture.clips[0].transcribe)
    }

    func testEmptyClipIsDroppedFromTheDraft() throws {
        let clip = try store.beginClip(transcribe: true)
        try Data().write(to: store.clipURL(clip))
        XCTAssertThrowsError(try store.finishClip(clip.attachmentID))
        XCTAssertTrue(try store.draft().isEmpty)
    }

    func testRecordingInterruptedByAKillStaysInTheDraftMarked() throws {
        let clip = try store.beginClip(transcribe: true)
        try record(clip)
        XCTAssertThrowsError(try store.commitDraft(text: "Mid-recording", youtubeURLs: [])) {
            XCTAssertEqual($0 as? CaptureError, .stillRecording)
        }
        XCTAssertFalse(try store.draft().isEmpty, "A refused save keeps the draft")
        try CaptureStore(root: root).recoverInterruptedRecordings()
        XCTAssertEqual(try store.draft().clips.first?.state, .interrupted)
        XCTAssertEqual(try store.commitDraft(text: "", youtubeURLs: []).clips.first?.state, .interrupted)
    }

    func testDiscardedClipLeavesNoAudioBehind() throws {
        let clip = try store.beginClip(transcribe: true)
        try record(clip)
        try store.finishClip(clip.attachmentID)
        try store.discard(clip)
        XCTAssertTrue(try store.draft().isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.clipURL(clip).path))
    }

    func testClipUploadsToTheEntryItBelongsTo() throws {
        let clip = try store.beginClip(transcribe: true)
        try record(clip)
        try store.finishClip(clip.attachmentID)
        let capture = try store.commitDraft(text: "", youtubeURLs: [])
        let body = try RecordingMultipart(entryID: capture.id, attachmentID: clip.attachmentID,
                                          capturedAt: clip.createdAt, transcribe: true, audioURL: store.clipURL(clip))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = try XCTUnwrap(String(data: Data(contentsOf: body.url), encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"id\"\r\n\r\n\(capture.id)\r\n"))
        XCTAssertTrue(text.contains("name=\"attachmentId\"\r\n\r\n\(clip.attachmentID)\r\n"))
        XCTAssertTrue(text.contains("name=\"transcribe\"\r\n\r\ntrue\r\n"))

        let good = Data("{\"id\":\"\(capture.id)\",\"attachment\":{\"id\":\"\(clip.attachmentID)\"}}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateClipAcknowledgement(good, for: capture, clip: clip))
        // A reply naming a different entry means the clip landed somewhere else.
        let elsewhere = Data("{\"id\":\"\(ULID.make())\",\"attachment\":{\"id\":\"\(clip.attachmentID)\"}}".utf8)
        XCTAssertThrowsError(try JournalAPI.validateClipAcknowledgement(elsewhere, for: capture, clip: clip))
    }

    @MainActor
    func testSyncHandsTheTransportEachClipInRecordedOrder() async throws {
        var clips: [CaptureClip] = []
        for word in ["first", "second"] {
            let clip = try store.beginClip(transcribe: true)
            try record(clip, word)
            try store.finishClip(clip.attachmentID)
            clips.append(clip)
        }
        let capture = try store.commitDraft(text: "", youtubeURLs: [])
        let transport = FileRecordingTransport()
        try await CaptureSync(store: store).run(using: transport)
        XCTAssertEqual(transport.sentClips.map { try? Data(contentsOf: $0) }, [Data("first".utf8), Data("second".utf8)])
        XCTAssertEqual(try store.load(capture.id).state, .synced)
    }
}

private final class FileRecordingTransport: JournalTransport {
    var sent: [URL] = []
    func send(_ capture: Capture, audioURL: URL?) async throws { XCTFail("Files must reach the transport") }
    var sentClips: [URL] = []
    func send(_ capture: Capture, audioURL: URL?, files: [URL], clips: [URL]) async throws {
        sent = files; sentClips = clips
    }
    func fetch(_ id: String) async throws -> JournalSnapshot {
        try JSONDecoder().decode(JournalSnapshot.self, from: Data("{\"id\":\"\(id)\",\"content\":\"\"}".utf8))
    }
}
