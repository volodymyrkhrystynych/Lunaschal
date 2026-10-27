import Foundation
import XCTest
@testable import LunaschalCore

/// Live Photos, videos and the date they were taken: staging both halves of a
/// Live Photo as one file, sending the capture date and the clip with the
/// upload, dating a pictures-only entry by its pictures, and handing videos to
/// the background session without losing the outbox's confirmation.
final class LiveVideoCaptureTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func movie(_ bytes: String = "mov bytes") throws -> URL {
        let url = root.appendingPathComponent(UUID().uuidString + ".mov")
        try Data(bytes.utf8).write(to: url)
        return url
    }

    // MARK: Live Photos

    func testALivePhotoIsOneFileWithBothHalvesStaged() throws {
        let file = try store.stageLivePhoto(still: Data("heic".utf8), stillType: "image/heic",
                                            motion: movie(), motionType: "video/quicktime", name: "IMG_1.HEIC")
        XCTAssertTrue(file.isLivePhoto)
        XCTAssertTrue(file.isImage)
        XCTAssertEqual(try store.draft().files, [file])
        XCTAssertEqual(try Data(contentsOf: store.motionURL(file)), Data("mov bytes".utf8))
        let capture = try store.commitDraft(text: "", youtubeURLs: [])
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id).files, [file])
    }

    func testDiscardingALivePhotoTakesItsClipToo() throws {
        let file = try store.stageLivePhoto(still: Data("heic".utf8), stillType: "image/heic",
                                            motion: movie(), motionType: "video/quicktime", name: "IMG_1.HEIC")
        try store.discardStaged(file)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.motionURL(file).path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.fileURL(file).path))
    }

    func testALivePhotoWithoutItsClipCannotBeSaved() throws {
        let file = try store.stageLivePhoto(still: Data("heic".utf8), stillType: "image/heic",
                                            motion: movie(), motionType: "video/quicktime", name: "IMG_1.HEIC")
        try FileManager.default.removeItem(at: store.motionURL(file))
        XCTAssertThrowsError(try store.save(Capture(files: [file])))
    }

    func testAnEmptyClipIsRefused() throws {
        XCTAssertThrowsError(try store.stageLivePhoto(still: Data("heic".utf8), stillType: "image/heic",
                                                      motion: movie(""), motionType: "video/quicktime", name: "x"))
        XCTAssertEqual(try store.draft().files, [])
    }

    func testAManifestFromBeforeLivePhotosStillDecodes() throws {
        let file = CaptureFile(name: "a.jpg", contentType: "image/jpeg")
        var old = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(file)) as? [String: Any])
        old.removeValue(forKey: "motionContentType")
        let decoded = try JSONDecoder().decode(CaptureFile.self, from: JSONSerialization.data(withJSONObject: old))
        XCTAssertEqual(decoded, file)
        XCTAssertFalse(decoded.isLivePhoto)
    }

    // MARK: The upload

    func testTheUploadCarriesWhenItWasTakenAndTheClip() throws {
        let taken = Date(timeIntervalSince1970: 1_719_873_000)
        let source = try movie("still")
        let still = try store.stageFile(from: source, name: "IMG_1.jpg", contentType: "image/jpeg", capturedAt: taken)
        let live = try store.stageLivePhoto(still: Data("heic".utf8), stillType: "image/heic",
                                            motion: movie("CLIP"), motionType: "video/quicktime", name: "IMG_2.HEIC")
        let capture = Capture(text: "x", files: [still, live])

        let plain = try AttachmentMultipart(capture: capture, file: still, fileURL: store.fileURL(still))
        defer { try? FileManager.default.removeItem(at: plain.url) }
        let plainText = try XCTUnwrap(String(data: Data(contentsOf: plain.url), encoding: .utf8))
        XCTAssertTrue(plainText.contains("name=\"capturedAt\"\r\n\r\n2024-07-01T22:30:00Z\r\n"))
        XCTAssertFalse(plainText.contains("name=\"motion\""))

        let both = try AttachmentMultipart(capture: capture, file: live, fileURL: store.fileURL(live))
        defer { try? FileManager.default.removeItem(at: both.url) }
        let text = try XCTUnwrap(String(data: Data(contentsOf: both.url), encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"file\"; filename=\"IMG_2.HEIC\"\r\nContent-Type: image/heic\r\n\r\nheic\r\n"))
        XCTAssertTrue(text.contains("name=\"motion\"; filename=\"motion.mov\"\r\nContent-Type: video/quicktime\r\n\r\nCLIP\r\n"))
    }

    // MARK: When the entry is dated

    func testAPicturesOnlyEntryIsDatedByTheEarliestPicture() throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let older = Date(timeIntervalSince1970: 1_700_000_000)
        let newer = Date(timeIntervalSince1970: 1_750_000_000)
        _ = try store.stageFile(from: movie(), name: "clip.mov", contentType: "video/quicktime", capturedAt: newer)
        _ = try store.stageFile(from: movie("p"), name: "a.jpg", contentType: "image/jpeg", capturedAt: older)
        let capture = try store.commitDraft(text: "  ", youtubeURLs: [], now: now)
        XCTAssertEqual(capture.createdAt, older)
    }

    func testWordsKeepTheTimeTheyWereWritten() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let photo = CaptureFile(name: "a.jpg", contentType: "image/jpeg",
                                capturedAt: Date(timeIntervalSince1970: 1_700_000_000))
        XCTAssertEqual(CaptureStore.entryDate(kind: .journal, text: "A day", files: [photo], clips: [],
                                              links: [], now: now), now)
        XCTAssertEqual(CaptureStore.entryDate(kind: .journal, text: "", files: [photo],
                                              clips: [CaptureClip(transcribe: true)], links: [], now: now), now)
        XCTAssertEqual(CaptureStore.entryDate(kind: .journal, text: "", files: [photo], clips: [],
                                              links: ["https://www.youtube.com/watch?v=aircAruvnKk"], now: now), now)
    }

    func testAnUndatedOrFutureFileDoesNotMoveTheEntry() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let undated = CaptureFile(name: "a.jpg", contentType: "image/jpeg")
        let future = CaptureFile(name: "b.jpg", contentType: "image/jpeg", capturedAt: now.addingTimeInterval(86_400))
        let pdf = CaptureFile(name: "c.pdf", contentType: "application/pdf",
                              capturedAt: Date(timeIntervalSince1970: 1_000))
        XCTAssertEqual(CaptureStore.entryDate(kind: .journal, text: "", files: [undated], clips: [],
                                              links: [], now: now), now)
        XCTAssertEqual(CaptureStore.entryDate(kind: .journal, text: "", files: [future], clips: [],
                                              links: [], now: now), now)
        XCTAssertEqual(CaptureStore.entryDate(kind: .journal, text: "", files: [pdf], clips: [],
                                              links: [], now: now), now)
    }

    // MARK: Background upload

    func testWhatGoesToTheBackgroundSession() {
        XCTAssertTrue(BackgroundUploads.shouldHandOff(CaptureFile(name: "v", contentType: "video/mp4"), size: 10))
        XCTAssertTrue(BackgroundUploads.shouldHandOff(
            CaptureFile(name: "l", contentType: "image/heic", motionContentType: "video/quicktime"), size: 10))
        XCTAssertTrue(BackgroundUploads.shouldHandOff(CaptureFile(name: "z", contentType: "application/zip"),
                                                      size: BackgroundUploads.threshold + 1))
        XCTAssertFalse(BackgroundUploads.shouldHandOff(CaptureFile(name: "p", contentType: "image/jpeg"), size: 10))
    }

    @MainActor
    func testAVideoIsHandedOffAndConfirmedOnlyByTheServersAnswer() async throws {
        let entry = ULID.make()
        let video = try store.stageFile(from: movie(), name: "clip.mov", contentType: "video/quicktime", into: entry)
        let addition = try XCTUnwrap(store.commitAdditions(to: entry, kind: .journal))
        var pending = addition
        pending.state = .pending
        try store.save(pending)

        let ledger = try BackgroundUploads(root: root.appendingPathComponent("background"))
        let uploader = FakeUploader()
        let api = try JournalAPI(server: URL(string: "https://lunaschal.example")!, token: "tok", allowCellular: false)
        api.background = (ledger, uploader)
        let transfers = try TransferStore(root: root.appendingPathComponent("transfers"))
        let sync = CaptureSync(store: store, transfers: transfers)
        sync.backgroundUploads = ledger

        try await sync.run(using: api)
        XCTAssertEqual(uploader.handed.map(\.taskID), [video.attachmentID])
        let request = try XCTUnwrap(uploader.handed.first?.request)
        XCTAssertEqual(request.url?.path, "/api/journal/\(entry)/attachments")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "lunaschal_token=tok")
        XCTAssertTrue(request.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data") == true)
        XCTAssertEqual(try ledger.state(video.attachmentID), .inFlight)
        // Waiting is not failing.
        var waiting = try store.load(addition.id)
        XCTAssertEqual(waiting.state, .pending)
        XCTAssertNil(waiting.lastError)

        // Another pass while it is in flight sends nothing again.
        try await sync.run(using: api)
        XCTAssertEqual(uploader.handed.count, 1)

        // An answer about some other attachment is not a confirmation.
        try ledger.finish(video.attachmentID, status: 201,
                          data: Data("{\"id\":\"\(ULID.make())\",\"entryId\":\"\(entry)\"}".utf8))
        _ = try? await sync.run(using: api)
        XCTAssertEqual(try store.load(addition.id).state, .pending)
        XCTAssertNotNil(try store.load(addition.id).lastError)

        // Handed off again, and this time the server's answer names it.
        try ledger.reset(video.attachmentID)
        try await runIgnoringBackoff(sync, api, transfers, addition.id)
        XCTAssertEqual(uploader.handed.count, 2)
        try ledger.finish(video.attachmentID, status: 201,
                          data: Data("{\"id\":\"\(video.attachmentID)\",\"entryId\":\"\(entry)\"}".utf8))
        try await runIgnoringBackoff(sync, api, transfers, addition.id)
        XCTAssertEqual(try store.load(addition.id).state, .synced)
        XCTAssertEqual(try ledger.state(video.attachmentID), .none)
    }

    func testARejectedBackgroundUploadIsReportedByStatus() throws {
        let ledger = try BackgroundUploads(root: root.appendingPathComponent("background"))
        let file = try store.stageFile(from: movie(), name: "clip.mov", contentType: "video/quicktime")
        _ = try ledger.prepare(Capture(text: "x", files: [file]), file: file, fileURL: store.fileURL(file))
        try ledger.finish(file.attachmentID, status: 413, data: Data())
        XCTAssertEqual(try ledger.state(file.attachmentID), .failed(status: 413))
    }

    func testATransportErrorOrALostTaskHandsTheFileOffAgain() throws {
        let ledger = try BackgroundUploads(root: root.appendingPathComponent("background"))
        let file = try store.stageFile(from: movie(), name: "clip.mov", contentType: "video/quicktime")
        let other = try store.stageFile(from: movie("2"), name: "two.mov", contentType: "video/quicktime")
        let capture = Capture(text: "x", files: [file, other])
        _ = try ledger.prepare(capture, file: file, fileURL: store.fileURL(file))
        _ = try ledger.prepare(capture, file: other, fileURL: store.fileURL(other))
        try ledger.finish(file.attachmentID, status: nil, data: Data())
        XCTAssertEqual(try ledger.state(file.attachmentID), .none)
        // Force-quitting cancels background uploads; the session then reports
        // no such task, and the record must not wait forever.
        try ledger.reconcile(live: [])
        XCTAssertEqual(try ledger.state(other.attachmentID), .none)
    }

    func testAKnownTaskSurvivesReconcile() throws {
        let ledger = try BackgroundUploads(root: root.appendingPathComponent("background"))
        let file = try store.stageFile(from: movie(), name: "clip.mov", contentType: "video/quicktime")
        _ = try ledger.prepare(Capture(text: "x", files: [file]), file: file, fileURL: store.fileURL(file))
        try ledger.reconcile(live: [file.attachmentID])
        XCTAssertEqual(try ledger.state(file.attachmentID), .inFlight)
    }

    /// The failed pass above put the capture on a backoff; this one is about
    /// what the next pass does, not when it is allowed to.
    @MainActor
    private func runIgnoringBackoff(_ sync: CaptureSync, _ api: JournalAPI, _ transfers: TransferStore,
                                    _ id: String) async throws {
        try? FileManager.default.removeItem(at: transfers.root.appendingPathComponent(id + ".json"))
        try await sync.run(using: api)
    }
}

private final class FakeUploader: BackgroundUploading {
    var handed: [(request: URLRequest, body: URL, taskID: String)] = []
    func handOff(_ request: URLRequest, body: URL, taskID: String) throws {
        XCTAssertTrue(FileManager.default.fileExists(atPath: body.path))
        handed.append((request, body, taskID))
    }
}
