import Foundation
import XCTest
@testable import LunaschalCore

final class CaptureTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func testSavedTextSurvivesRestartWithSameIdentityAndCaptureTime() throws {
        let capture = Capture(text: "Offline thought", now: Date(timeIntervalSince1970: 1_790_000_000))
        try store.save(capture)
        let reopened = try CaptureStore(root: root)
        XCTAssertEqual(try reopened.list(), [capture])
        XCTAssertTrue(ULID.isValid(capture.id))
    }

    func testInterruptedRecordingIsKeptButNotAutomaticallyUploaded() throws {
        let capture = Capture(mode: .transcribe)
        try store.save(capture)
        let bytes = Data("original audio".utf8)
        try bytes.write(to: store.audioURL(capture))
        try store.recoverInterruptedRecordings()
        XCTAssertEqual(try store.load(capture.id).state, .interrupted)
        XCTAssertEqual(try Data(contentsOf: store.audioURL(capture)), bytes)
        try store.finishRecording(capture.id)
        XCTAssertEqual(try store.load(capture.id).state, .pending)
    }

    func testEmptyAudioCannotBecomeUploadable() throws {
        let capture = Capture(mode: .record)
        try store.save(capture)
        try Data().write(to: store.audioURL(capture))
        XCTAssertThrowsError(try store.finishRecording(capture.id))
        XCTAssertEqual(try store.load(capture.id).state, .recording)
    }

    func testCorruptManifestDoesNotResetOtherCaptures() throws {
        let capture = Capture(text: "Keep this")
        try store.save(capture)
        try Data("broken".utf8).write(to: root.appendingPathComponent("damaged.json"))
        XCTAssertThrowsError(try store.list())
        XCTAssertEqual(try store.load(capture.id), capture)
    }

    func testServerBindingSurvivesRestartAndRejectsDestinationChange() throws {
        let address = try ServerAddress.parse("https://SERVER.tailnet.ts.net:443/")
        try store.bind(to: address)
        let reopened = try CaptureStore(root: root)
        XCTAssertEqual(try reopened.server?.absoluteString, "https://server.tailnet.ts.net")
        XCTAssertNoThrow(try reopened.bind(to: address))
        XCTAssertThrowsError(try reopened.bind(to: URL(string: "https://other.example")!))
    }

    func testServerRejectsInsecureOrAmbiguousAddresses() {
        for value in ["http://server", "https://user:pass@server", "https://server/api", "https://server?q=a", "https://server#fragment", "not a URL"] {
            XCTAssertThrowsError(try ServerAddress.parse(value), value)
        }
    }

    func testULIDsAreUniqueAndContainTheCaptureTimestamp() {
        let date = Date(timeIntervalSince1970: 1_790_000_000)
        let ids = (0..<1000).map { _ in ULID.make(now: date) }
        XCTAssertEqual(Set(ids).count, 1000)
        XCTAssertTrue(ids.allSatisfy(ULID.isValid))
        let alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        let stamp = ids[0].prefix(10).reduce(UInt64(0)) { $0 * 32 + UInt64(alphabet.firstIndex(of: $1)!) }
        XCTAssertEqual(stamp, 1_790_000_000_000)
        XCTAssertFalse(ULID.isValid("../../outside"))
        XCTAssertFalse(ULID.isValid(""))
    }

    func testMultipartContainsOriginalBytesIDsTimeAndTranscriptionChoice() throws {
        for mode in [CaptureMode.record, .transcribe] {
            let capture = Capture(mode: mode, now: Date(timeIntervalSince1970: 1_790_000_000))
            let bytes = Data([0, 255, 13, 10, 1, 2, 3])
            try bytes.write(to: store.audioURL(capture))
            let body = try RecordingMultipart(capture: capture, audioURL: store.audioURL(capture))
            defer { try? FileManager.default.removeItem(at: body.url) }
            let data = try Data(contentsOf: body.url)
            let text = String(decoding: data, as: UTF8.self)
            XCTAssertNotNil(data.range(of: bytes))
            XCTAssertTrue(text.contains(capture.id))
            XCTAssertTrue(text.contains(capture.attachmentID!))
            XCTAssertTrue(text.contains(ISO8601DateFormatter().string(from: capture.createdAt)))
            XCTAssertTrue(text.contains("name=\"transcribe\"\r\n\r\n\(mode == .transcribe ? "true" : "false")\r\n"))
            XCTAssertTrue(text.hasSuffix("--\(body.boundary)--\r\n"))
        }
    }

    func testAcknowledgementMustMatchBothIDs() throws {
        let capture = Capture(mode: .record)
        let good = Data("{\"id\":\"\(capture.id)\",\"attachment\":{\"id\":\"\(capture.attachmentID!)\"}}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateAcknowledgement(good, for: capture))
        for body in ["{}", "<html>login</html>", "{\"id\":\"\(capture.id)\"}", "{\"id\":\"wrong\"}"] {
            XCTAssertThrowsError(try JournalAPI.validateAcknowledgement(Data(body.utf8), for: capture))
        }
    }
}

private final class FakeServer: JournalTransport {
    var received = Set<String>()
    var attempts: [String] = []
    var loseFirstResponse = false
    var reject: [String: Int] = [:]
    var deleted = Set<String>()

    func send(_ capture: Capture, audioURL: URL?) async throws {
        attempts.append(capture.id)
        if let status = reject[capture.id] { throw HTTPFailure(status: status) }
        received.insert(capture.id)
        if loseFirstResponse {
            loseFirstResponse = false
            throw URLError(.networkConnectionLost)
        }
    }

    func fetch(_ id: String) async throws -> JournalSnapshot {
        if deleted.contains(id) { throw HTTPFailure(status: 404) }
        return try JSONDecoder().decode(JournalSnapshot.self, from: Data("{\"id\":\"\(id)\",\"content\":\"Server transcript\",\"title\":\"A thought\"}".utf8))
    }
}

final class SyncTests: XCTestCase {
    @MainActor
    func testLostResponseThenRestartRetriesSameCaptureAndKeepsAudio() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        var capture = Capture(mode: .transcribe)
        capture.state = .pending
        try store.save(capture)
        let bytes = Data("audio survives".utf8)
        try bytes.write(to: store.audioURL(capture))
        let server = FakeServer()
        server.loseFirstResponse = true
        do {
            try await CaptureSync(store: store).run(using: server)
            XCTFail("Should report the lost response")
        } catch {}
        XCTAssertEqual(try store.load(capture.id).state, .pending)
        let reopened = try CaptureStore(root: root)
        try await CaptureSync(store: reopened).run(using: server)
        XCTAssertEqual(server.received, [capture.id])
        XCTAssertEqual(server.attempts, [capture.id, capture.id])
        XCTAssertEqual(try reopened.load(capture.id).state, .synced)
        XCTAssertEqual(try reopened.load(capture.id).snapshot?.content, "Server transcript")
        XCTAssertEqual(try Data(contentsOf: store.audioURL(capture)), bytes)
    }

    @MainActor
    func testRejectedEntryDoesNotBlockOthersOrRetryForever() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        let first = Capture(text: "Rejected", now: Date(timeIntervalSince1970: 100))
        let second = Capture(text: "Accepted", now: Date(timeIntervalSince1970: 200))
        try store.save(first)
        try store.save(second)
        let server = FakeServer()
        server.reject[first.id] = 400
        let sync = CaptureSync(store: store)
        try await sync.run(using: server)
        try await sync.run(using: server)
        XCTAssertEqual(try store.load(first.id).state, .failed)
        XCTAssertEqual(try store.load(second.id).state, .synced)
        XCTAssertEqual(server.attempts, [first.id, second.id])
    }

    @MainActor
    func testExpiredLoginKeepsPendingEntriesAndStopsUploads() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        let first = Capture(text: "First", now: Date(timeIntervalSince1970: 100))
        let second = Capture(text: "Second", now: Date(timeIntervalSince1970: 200))
        try store.save(first)
        try store.save(second)
        let server = FakeServer()
        server.reject[first.id] = 401
        do { try await CaptureSync(store: store).run(using: server); XCTFail("Expected login failure") }
        catch let error as HTTPFailure { XCTAssertEqual(error.status, 401) }
        XCTAssertEqual(server.attempts, [first.id])
        XCTAssertTrue(try store.list().allSatisfy { $0.state == .pending })
    }

    @MainActor
    func testServerDeletionDoesNotRecreateSyncedCapture() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        var capture = Capture(text: "Original")
        capture.state = .synced
        try store.save(capture)
        let server = FakeServer()
        server.deleted.insert(capture.id)
        try await CaptureSync(store: store).run(using: server)
        XCTAssertTrue(server.attempts.isEmpty)
        XCTAssertEqual(try store.load(capture.id).state, .synced)
        XCTAssertNotNil(try store.load(capture.id).lastError)
    }
}
