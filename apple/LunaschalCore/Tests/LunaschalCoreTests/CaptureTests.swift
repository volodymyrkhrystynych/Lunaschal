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

    func testYouTubeLinksRetainTextAndAttachmentIdentityAcrossRestart() throws {
        let url = try YouTubeLink.canonical("https://youtu.be/aircAruvnKk?list=ignored")
        XCTAssertEqual(url, "https://www.youtube.com/watch?v=aircAruvnKk")
        let other = try YouTubeLink.canonical("https://youtube.com/shorts/dQw4w9WgXcQ")
        let capture = Capture(text: "My thoughts", youtubeURLs: [url, other])
        try store.save(capture)
        let restored = try CaptureStore(root: root).load(capture.id)
        XCTAssertEqual(restored, capture)
        XCTAssertNil(restored.attachmentID)
        XCTAssertEqual(restored.links.map(\.url), [url, other])
        XCTAssertNotEqual(restored.links[0].attachmentID, restored.links[1].attachmentID)
        XCTAssertTrue(restored.links.allSatisfy { ULID.isValid($0.attachmentID) })
        for link in restored.links {
            let ack = try JSONEncoder().encode(["id": link.attachmentID, "entryId": restored.id])
            XCTAssertNoThrow(try JournalAPI.validateLinkAcknowledgement(ack, for: restored, link: link))
            let wrong = try JSONEncoder().encode(["id": link.attachmentID, "entryId": ULID.make()])
            XCTAssertThrowsError(try JournalAPI.validateLinkAcknowledgement(wrong, for: restored, link: link))
        }
        let swapped = try JSONEncoder().encode(["id": restored.links[1].attachmentID, "entryId": restored.id])
        XCTAssertThrowsError(try JournalAPI.validateLinkAcknowledgement(swapped, for: restored, link: restored.links[0]))
    }

    func testNonCanonicalLinkIsRejectedOnSave() {
        let capture = Capture(text: "Thoughts", youtubeURLs: ["https://youtu.be/aircAruvnKk"])
        XCTAssertThrowsError(try store.save(capture))
    }

    func testLinkOnlyEntryKeepsItsBodyEmptyAcrossRestart() throws {
        let capture = try store.commitDraft(text: " \n ", youtubeURLs: [
            "https://youtu.be/aircAruvnKk", "https://youtube.com/shorts/dQw4w9WgXcQ"
        ])
        XCTAssertEqual(capture.text, "")
        let reopened = try CaptureStore(root: root)
        let restored = try reopened.load(capture.id)
        XCTAssertEqual(restored, capture)
        XCTAssertEqual(restored.links.map(\.url), [
            "https://www.youtube.com/watch?v=aircAruvnKk", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        ])
        XCTAssertTrue(restored.matchesSearch("aircAruvnKk"), "Attachments remain searchable without body text")
        // Sync updates and re-saves the capture before and after uploading it.
        XCTAssertNoThrow(try reopened.save(restored))
    }

    func testAttachedLinksNeverReplaceOrAppendToCommentary() throws {
        let url = "https://www.youtube.com/watch?v=aircAruvnKk"
        for text in ["My thoughts", "I wrote this URL myself: \(url)"] {
            let capture = try store.commitDraft(text: text, youtubeURLs: [url])
            XCTAssertEqual(capture.text, text)
            XCTAssertEqual(capture.links.map(\.url), [url])
        }
    }

    func testYouTubeURLValidationAndOldManifestCompatibility() throws {
        for url in ["https://youtube.com/shorts/aircAruvnKk", "https://m.youtube.com/watch?v=aircAruvnKk", "https://youtube.com/live/aircAruvnKk"] {
            XCTAssertEqual(try YouTubeLink.canonical(url), "https://www.youtube.com/watch?v=aircAruvnKk")
        }
        for url in ["https://youtube.com.evil/watch?v=aircAruvnKk", "https://youtube.com/playlist?list=123", "file:///aircAruvnKk", "https://user@youtube.com/watch?v=aircAruvnKk"] {
            XCTAssertThrowsError(try YouTubeLink.canonical(url))
        }
        // A manifest from before link support has no link keys at all.
        let capture = Capture(text: "Before link support")
        var old = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(capture)) as? [String: Any])
        old.removeValue(forKey: "links")
        let restored = try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: old))
        XCTAssertEqual(restored, capture)

        // One from the single-link era keeps its attachment id, so a pending
        // send still replays against the row the server may already have.
        let url = "https://www.youtube.com/watch?v=aircAruvnKk", linkID = ULID.make()
        old["youtubeURL"] = url; old["linkAttachmentID"] = linkID
        let migrated = try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: old))
        XCTAssertEqual(migrated.links, [CaptureLink(url: url, attachmentID: linkID)])
        try store.save(migrated)
        XCTAssertEqual(try store.load(migrated.id), migrated)
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
    var fetches = 0

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
        fetches += 1
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
        // The entry's text comes back through the replica, not a fetch per capture.
        XCTAssertEqual(server.fetches, 0)
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
        XCTAssertEqual(server.fetches, 0, "the replica says it was deleted, not a request")
        // The replica's tombstone for the entry marks the capture, which stays.
        XCTAssertEqual(try store.tidySynced(entry: { _ in .removed }), 1)
        XCTAssertEqual(try store.load(capture.id).lastError, CaptureStore.removedOnServer)
        XCTAssertEqual(try store.tidySynced(entry: { _ in .removed }), 0, "marked once")
    }
}
