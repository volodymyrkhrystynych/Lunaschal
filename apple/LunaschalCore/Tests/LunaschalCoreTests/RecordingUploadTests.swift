import Foundation
import XCTest
@testable import LunaschalCore

final class RecordingUploadTests: XCTestCase {
    private let server = URL(string: "https://server.example")!

    private func fixture() throws -> (CaptureStore, RecordingUploadStore, Capture) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let captures = try CaptureStore(root: root)
        let uploads = try staging(root.appendingPathComponent("uploads"))
        var capture = Capture(mode: .transcribe, now: Date(timeIntervalSince1970: 1_790_000_000))
        capture.state = .pending
        try captures.save(capture)
        try Data("original recording".utf8).write(to: captures.audioURL(capture))
        return (captures, uploads, capture)
    }

    private func staging(_ root: URL) throws -> RecordingUploadStore {
        // Portable identity verifier; production uses streaming CryptoKit SHA256.
        try RecordingUploadStore(root: root, hash: { try Data(contentsOf: $0).base64EncodedString() })
    }

    func testRestartReusesExactBodyBoundaryAndDestinationAfterLostResponse() throws {
        let (captures, uploads, capture) = try fixture()
        let first = try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server)
        let bytes = try Data(contentsOf: uploads.bodyURL(first))
        var retry = capture
        retry.lastError = "Connection lost"
        let reopened = try staging(uploads.root)
        let second = try reopened.prepare(retry, audioURL: captures.audioURL(capture), server: server)
        XCTAssertEqual(first, second)
        XCTAssertEqual(try Data(contentsOf: reopened.bodyURL(second)), bytes)
        XCTAssertNotNil(bytes.range(of: Data("original recording".utf8)))
        XCTAssertThrowsError(try reopened.prepare(capture, audioURL: captures.audioURL(capture),
                                                 server: URL(string: "https://different.example")!))
        XCTAssertEqual(try Data(contentsOf: reopened.bodyURL(second)), bytes)
    }

    func testSameSizeCorruptionAndMissingBodyAreRebuiltFromOriginal() throws {
        let (captures, uploads, capture) = try fixture()
        let first = try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server)
        let body = try uploads.bodyURL(first)
        try Data(repeating: 0, count: first.size).write(to: body)
        let rebuilt = try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server)
        XCTAssertNotEqual(first.boundary, rebuilt.boundary)
        XCTAssertNotNil(try Data(contentsOf: body).range(of: Data("original recording".utf8)))
        try FileManager.default.removeItem(at: body)
        let restored = try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server)
        XCTAssertNotEqual(restored.boundary, rebuilt.boundary)
        XCTAssertTrue(FileManager.default.fileExists(atPath: body.path))
    }

    func testInterruptedPreparationIsReplacedAndFailurePublishesNoManifest() throws {
        let (captures, uploads, capture) = try fixture()
        let folder = uploads.root.appendingPathComponent(capture.id)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try Data([1]).write(to: folder.appendingPathComponent("body.preparing"))
        try Data([2]).write(to: folder.appendingPathComponent("body.multipart"))
        try Data().write(to: captures.audioURL(capture))
        XCTAssertThrowsError(try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server))
        XCTAssertFalse(FileManager.default.fileExists(atPath: folder.appendingPathComponent("request.json").path))
        try Data([3]).write(to: captures.audioURL(capture))
        XCTAssertNoThrow(try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server))
    }

    func testCleanupRequiresDurableSyncedStateAndKeepsOriginalAudio() throws {
        let (captures, uploads, capture) = try fixture()
        let staged = try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server)
        try uploads.discardAfterSync(try captures.load(capture.id))
        XCTAssertTrue(FileManager.default.fileExists(atPath: try uploads.bodyURL(staged).path))
        var acknowledged = capture
        acknowledged.state = .synced
        try captures.save(acknowledged)
        try uploads.discardAfterSync(try captures.load(capture.id))
        XCTAssertFalse(FileManager.default.fileExists(atPath: try uploads.bodyURL(staged).path))
        XCTAssertEqual(try Data(contentsOf: captures.audioURL(capture)), Data("original recording".utf8))
    }

    @MainActor
    func testLostAcknowledgementRetainsStagingUntilRetryStateIsSaved() async throws {
        let (captures, uploads, capture) = try fixture()
        let transport = LostReplyTransport(uploads: uploads, server: server)
        do {
            try await CaptureSync(store: captures, uploads: uploads).run(using: transport)
            XCTFail("Expected the lost acknowledgement")
        } catch {}
        XCTAssertEqual(try captures.load(capture.id).state, .pending)
        let first = try XCTUnwrap(transport.bodies.first)
        XCTAssertTrue(FileManager.default.fileExists(atPath: try uploads.bodyURL(first).path))
        let reopened = try staging(uploads.root)
        transport.uploads = reopened
        try await CaptureSync(store: captures, uploads: reopened).run(using: transport)
        XCTAssertEqual(transport.bodies, [first, first])
        XCTAssertEqual(try captures.load(capture.id).state, .synced)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try reopened.bodyURL(first).path))
        XCTAssertEqual(try Data(contentsOf: captures.audioURL(capture)), Data("original recording".utf8))
    }

    @MainActor
    func testSyncRestartCleansAcknowledgedStagingWithoutUploadingAgain() async throws {
        let (captures, uploads, capture) = try fixture()
        let staged = try uploads.prepare(capture, audioURL: captures.audioURL(capture), server: server)
        var acknowledged = capture
        acknowledged.state = .synced
        try captures.save(acknowledged)
        let transport = AlreadySyncedTransport()
        try await CaptureSync(store: captures, uploads: uploads).run(using: transport)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try uploads.bodyURL(staged).path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: try captures.audioURL(capture).path))
    }
}

private final class LostReplyTransport: JournalTransport {
    var uploads: RecordingUploadStore
    let server: URL
    var bodies: [StagedRecording] = []
    init(uploads: RecordingUploadStore, server: URL) { self.uploads = uploads; self.server = server }
    func send(_ capture: Capture, audioURL: URL?) async throws {
        bodies.append(try uploads.prepare(capture, audioURL: XCTUnwrap(audioURL), server: server))
        if bodies.count == 1 { throw URLError(.networkConnectionLost) }
    }
    func fetch(_ id: String) async throws -> JournalSnapshot {
        try JSONDecoder().decode(JournalSnapshot.self, from: Data("{\"id\":\"\(id)\",\"content\":\"Transcript\"}".utf8))
    }
}

private final class AlreadySyncedTransport: JournalTransport {
    func send(_ capture: Capture, audioURL: URL?) async throws { XCTFail("A durably acknowledged recording must not upload again") }
    func fetch(_ id: String) async throws -> JournalSnapshot {
        try JSONDecoder().decode(JournalSnapshot.self, from: Data("{\"id\":\"\(id)\",\"content\":\"Transcript\"}".utf8))
    }
}
