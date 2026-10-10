import Foundation
import XCTest
@testable import LunaschalCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Editing an entry that is still waiting to sync. The server ignores a re-sent
/// create, so the words may change only while it certainly has none of them.
final class CaptureEditTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private final class Server: JournalTransport {
        var texts: [String: String] = [:]
        var failure: Error?
        var during: ((Capture) throws -> Void)?

        func send(_ capture: Capture, audioURL: URL?) async throws {
            try during?(capture)
            if let failure { throw failure }
            texts[capture.id] = capture.text
        }

        func fetch(_ id: String) async throws -> JournalSnapshot { throw HTTPFailure(status: 404) }
    }

    /// What JournalAPI throws when the create never reached the server.
    private var neverConnected: URLError {
        URLError(.cannotConnectToHost, userInfo: [JournalAPI.failedBeforeSendingKey: true])
    }

    func testAnEntryNeverSentCanBeEdited() async throws {
        let capture = Capture(text: "First draft")
        try store.save(capture)
        XCTAssertTrue(capture.canEditText(attempt: nil))
        try store.editText(capture.id, to: "  Second draft \n", attempt: nil)
        XCTAssertEqual(try store.load(capture.id).text, "Second draft")

        let server = Server()
        try await CaptureSync(store: store).run(using: server)
        XCTAssertEqual(server.texts[capture.id], "Second draft")
    }

    func testCommentaryCanBeClearedWhileKeepingTheYouTubeAttachment() async throws {
        let capture = try store.commitDraft(text: "My thoughts", youtubeURLs: ["https://youtu.be/aircAruvnKk"])
        try store.editText(capture.id, to: " \n ", attempt: nil)
        XCTAssertEqual(try store.load(capture.id).text, "")
        XCTAssertEqual(try store.load(capture.id).links, capture.links)

        let server = Server()
        try await CaptureSync(store: store).run(using: server)
        XCTAssertEqual(server.texts[capture.id], "")
        XCTAssertEqual(try store.load(capture.id).state, .synced)
        XCTAssertEqual(try store.load(capture.id).links, capture.links)
    }

    func testAnEntryThatFailedBeforeConnectingStaysEditable() async throws {
        let transfers = try TransferStore(root: root.appendingPathComponent("transfers"))
        let capture = Capture(text: "Offline words")
        try store.save(capture)
        let server = Server()
        server.failure = neverConnected
        do { try await CaptureSync(store: store, transfers: transfers).run(using: server); XCTFail("offline") } catch {}

        let attempt = try transfers.load(capture.id)
        XCTAssertNotNil(attempt)
        XCTAssertEqual(try store.load(capture.id).mayBeOnServer, false)
        try store.editText(capture.id, to: "Better words", attempt: attempt)

        server.failure = nil
        try transfers.retryNow(capture.id, now: Date())
        try await CaptureSync(store: store, transfers: transfers).run(using: server)
        XCTAssertEqual(server.texts[capture.id], "Better words")
    }

    func testAnEntryThatMayHaveLandedIsLocked() async throws {
        let capture = Capture(text: "Sent, answer lost")
        try store.save(capture)
        let server = Server()
        // The request may have arrived; only the answer is missing.
        server.failure = URLError(.timedOut)
        do { try await CaptureSync(store: store).run(using: server); XCTFail("timed out") } catch {}

        let stored = try store.load(capture.id)
        XCTAssertEqual(stored.mayBeOnServer, true)
        XCTAssertFalse(stored.canEditText(attempt: nil))
        XCTAssertThrowsError(try store.editText(capture.id, to: "Lost", attempt: nil)) {
            XCTAssertEqual($0 as? CaptureError, .alreadySent)
        }
    }

    func testAnUnmarkedConnectionErrorLocksIt() async throws {
        // A later step (a clip, a photo) failing offline says nothing about the
        // create before it, so only the create's own mark unlocks the entry.
        let capture = Capture(text: "Created, then a photo failed")
        try store.save(capture)
        let server = Server()
        server.failure = URLError(.cannotConnectToHost)
        do { try await CaptureSync(store: store).run(using: server); XCTFail("offline") } catch {}
        XCTAssertEqual(try store.load(capture.id).mayBeOnServer, true)
    }

    func testOnceLockedAnOfflineRetryDoesNotUnlockIt() async throws {
        let capture = Capture(text: "Maybe there")
        try store.save(capture)
        let server = Server()
        server.failure = URLError(.timedOut)
        do { try await CaptureSync(store: store).run(using: server); XCTFail("timed out") } catch {}
        server.failure = neverConnected
        do { try await CaptureSync(store: store).run(using: server); XCTFail("offline") } catch {}
        XCTAssertEqual(try store.load(capture.id).mayBeOnServer, true)
    }

    func testNotEditableWhileSending() throws {
        let capture = Capture(text: "In flight")
        try store.save(capture)
        let transfers = try TransferStore(root: root.appendingPathComponent("transfers"))
        let attempt = try XCTUnwrap(transfers.begin(capture.id, now: Date()))
        XCTAssertFalse(capture.canEditText(attempt: attempt))
    }

    func testOnlyTypedJournalEntriesAreEditable() throws {
        XCTAssertFalse(Capture(text: "Toast", kind: .food).canEditText(attempt: nil))
        var recording = Capture(mode: .transcribe)
        recording.state = .pending
        XCTAssertFalse(recording.canEditText(attempt: nil))
        let entryID = ULID.make()
        XCTAssertFalse(Capture(entryID: entryID).canEditText(attempt: nil))
        var synced = Capture(text: "Done")
        synced.state = .synced
        XCTAssertFalse(synced.canEditText(attempt: nil))
    }

    func testCapturesSavedBeforeTrackingAreEditableOnlyIfNeverAttempted() throws {
        let capture = Capture(text: "Old manifest")
        try store.save(capture)
        // Rewrite the manifest without the field, as an older build stored it.
        let url = root.appendingPathComponent(capture.id).appendingPathExtension("json")
        var json = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        json.removeValue(forKey: "mayBeOnServer")
        try JSONSerialization.data(withJSONObject: json).write(to: url)

        let legacy = try store.load(capture.id)
        XCTAssertNil(legacy.mayBeOnServer)
        XCTAssertTrue(legacy.canEditText(attempt: nil))
        var waiting = try XCTUnwrap(TransferStore(root: root.appendingPathComponent("transfers")).begin(capture.id, now: Date()))
        waiting.state = .waiting
        XCTAssertFalse(legacy.canEditText(attempt: waiting))
    }

    @MainActor
    func testAnEditMadeWhileAnEarlierEntryUploadsIsWhatGetsSent() async throws {
        let earlier = Capture(text: "Earlier", now: Date(timeIntervalSince1970: 100))
        let later = Capture(text: "Later, first words", now: Date(timeIntervalSince1970: 200))
        try store.save(earlier)
        try store.save(later)
        let server = Server()
        let store = self.store!
        server.during = { capture in
            if capture.id == earlier.id { try store.editText(later.id, to: "Later, edited", attempt: nil) }
        }
        try await CaptureSync(store: store).run(using: server)
        XCTAssertEqual(server.texts[later.id], "Later, edited")
    }

    func testOnlyTheMarkedErrorCountsAsNeverSent() {
        XCTAssertTrue(JournalAPI.failedBeforeSending(neverConnected))
        XCTAssertFalse(JournalAPI.failedBeforeSending(URLError(.cannotConnectToHost)))
        XCTAssertFalse(JournalAPI.failedBeforeSending(HTTPFailure(status: 500)))
    }
}
