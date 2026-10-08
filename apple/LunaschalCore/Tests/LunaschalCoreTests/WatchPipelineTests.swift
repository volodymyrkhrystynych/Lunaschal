import Foundation
import XCTest
@testable import LunaschalCore

/// One Watch recording followed end to end, through the same store, envelope,
/// inbox, sync and receipt calls the Watch app, `WatchReceiver` and `CaptureSync`
/// make. Only the WatchConnectivity hop and the socket are stood in for.
final class WatchPipelineTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    #if canImport(CryptoKit)
    private let digest: (URL) throws -> String = MediaStore.sha256
    #else
    private let digest: (URL) throws -> String = { _ in String(repeating: "a", count: 64) }
    #endif

    @MainActor
    func testRecordAndTranscribeTravelFromWatchToPhoneToServerAndBack() async throws {
        for mode in [CaptureMode.record, .transcribe] {
            try await runPipeline(mode)
        }
    }

    @MainActor
    private func runPipeline(_ mode: CaptureMode) async throws {
        let base = root.appendingPathComponent(mode.rawValue)
        let watch = try CaptureStore(root: base.appendingPathComponent("watch"))
        let phone = try CaptureStore(root: base.appendingPathComponent("phone"))
        let inbox = try WatchInbox(root: phone.root.appendingPathComponent("watch-inbox"))
        let audio = Data("watch audio \(mode.rawValue)".utf8)

        // Watch, Recorder.start(mode:): the manifest is written before any audio.
        var capture = Capture(mode: mode, now: Date(timeIntervalSince1970: 1_790_000_000))
        try watch.save(capture)
        try audio.write(to: watch.audioURL(capture))
        // Recorder.stop(): saved locally and ready to hand off.
        try watch.finishRecording(capture.id)
        capture = try watch.load(capture.id)
        XCTAssertEqual(capture.state, .pending)
        // A relaunch must leave a finished recording alone.
        try CaptureStore(root: watch.root).recoverInterruptedRecordings()
        XCTAssertEqual(try watch.list().map(\.id), [capture.id])
        XCTAssertEqual(try watch.load(capture.id).state, .pending)

        // WatchModel.sendPending(): the file plus its envelope as metadata.
        let source = try watch.audioURL(capture)
        let bytes = Int64(try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
        let metadata = try JSONEncoder().encode(WatchEnvelope(capture: capture, bytes: bytes, sha256: digest(source)))

        // The phone is mid-way through its own Capture-tab draft; a Watch
        // recording must neither join it nor wait for its Save entry.
        let draftClip = try phone.beginClip(transcribe: true)
        try Data("draft clip".utf8).write(to: phone.clipURL(draftClip))
        try phone.finishClip(draftClip.attachmentID)

        // Phone, WatchReceiver: WatchConnectivity hands over a temporary copy
        // and deletes it once the delegate returns.
        let delivered = base.appendingPathComponent("wc-temp.m4a")
        try FileManager.default.copyItem(at: source, to: delivered)
        try inbox.stage(file: delivered, metadata: metadata)
        try FileManager.default.removeItem(at: delivered)
        XCTAssertEqual(try inbox.drain(into: phone, hash: digest), [capture.id]) // → "phoneStored"
        XCTAssertEqual(try Data(contentsOf: phone.audioURL(capture)), audio)
        XCTAssertEqual(try phone.load(capture.id).state, .pending)
        XCTAssertEqual(try phone.load(capture.id).mode, mode)
        XCTAssertTrue(try WatchReceipts(store: phone).pendingServerReceipts().isEmpty,
                      "Saved on phone is not a server receipt")

        // Phone, CaptureSync → the recordings route.
        let server = RecordingsRoute()
        try await CaptureSync(store: phone).run(using: server)
        XCTAssertEqual(server.uploads.count, 1)
        let upload = try XCTUnwrap(server.uploads.first)
        XCTAssertEqual(upload.fields["id"], capture.id)
        XCTAssertEqual(upload.fields["attachmentId"], capture.attachmentID)
        XCTAssertEqual(upload.fields["transcribe"], mode == .transcribe ? "true" : "false")
        XCTAssertEqual(upload.fields["capturedAt"], ISO8601DateFormatter().string(from: capture.createdAt))
        XCTAssertNotNil(upload.body.range(of: audio))
        XCTAssertEqual(try phone.load(capture.id).state, .synced)
        XCTAssertEqual(try phone.draft().clips.map(\.attachmentID), [draftClip.attachmentID])

        // Phone → Watch: the receipt, persisted on the Watch, then confirmed back.
        let phoneReceipts = WatchReceipts(store: phone)
        let receipt = try XCTUnwrap(phoneReceipts.pendingServerReceipts().first)
        XCTAssertEqual(receipt.captureID, capture.id)
        let watchReceipts = WatchReceipts(store: try CaptureStore(root: watch.root))
        XCTAssertFalse(try watchReceipts.serverReceived(capture))
        try watchReceipts.acceptServerReceipt(receipt)
        XCTAssertTrue(try watchReceipts.serverReceived(capture)) // "Uploaded to server"
        try phoneReceipts.confirmDelivery(receipt)
        XCTAssertTrue(try phoneReceipts.pendingServerReceipts().isEmpty)

        // A late duplicate transfer cannot reset the upload or send it again.
        try FileManager.default.copyItem(at: source, to: delivered)
        try inbox.stage(file: delivered, metadata: metadata)
        XCTAssertEqual(try inbox.drain(into: phone, hash: digest), [capture.id])
        try await CaptureSync(store: phone).run(using: server)
        XCTAssertEqual(server.uploads.count, 1)
        XCTAssertEqual(try phone.load(capture.id).state, .synced)

        // Only then may the user free the Watch; the phone keeps its original.
        try watchReceipts.removeWatchCopy(capture.id)
        XCTAssertTrue(try watch.list().isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path))
        XCTAssertEqual(try Data(contentsOf: phone.audioURL(capture)), audio)
    }
}

/// `POST /api/journal/recordings` as `backend/routes/journal.py` answers it:
/// it reads the multipart body JournalAPI streams and acknowledges both ids.
private final class RecordingsRoute: JournalTransport {
    struct Upload { let fields: [String: String]; let body: Data }
    var uploads: [Upload] = []
    private var modes: [String: CaptureMode] = [:]

    func transcript(for mode: CaptureMode) -> String { mode == .transcribe ? "Spoken on the Watch" : "" }

    func send(_ capture: Capture, audioURL: URL?) async throws {
        let multipart = try RecordingMultipart(capture: capture, audioURL: XCTUnwrap(audioURL))
        defer { try? FileManager.default.removeItem(at: multipart.url) }
        let body = try Data(contentsOf: multipart.url)
        var fields: [String: String] = [:]
        for part in String(decoding: body, as: UTF8.self).components(separatedBy: "--\(multipart.boundary)") {
            guard let range = part.range(of: "name=\""), !part.contains("filename=") else { continue }
            let rest = part[range.upperBound...]
            guard let close = rest.firstIndex(of: "\""), let start = rest.range(of: "\r\n\r\n") else { continue }
            fields[String(rest[..<close])] = String(rest[start.upperBound...]).trimmingCharacters(in: .newlines)
        }
        uploads.append(Upload(fields: fields, body: body))
        modes[capture.id] = capture.mode
        let ack = Data("{\"id\":\"\(fields["id"] ?? "")\",\"attachment\":{\"id\":\"\(fields["attachmentId"] ?? "")\"}}".utf8)
        try JournalAPI.validateAcknowledgement(ack, for: capture)
    }

    func fetch(_ id: String) async throws -> JournalSnapshot {
        let content = transcript(for: modes[id] ?? .record)
        return try JSONDecoder().decode(JournalSnapshot.self,
            from: Data("{\"id\":\"\(id)\",\"content\":\"\(content)\"}".utf8))
    }
}
