import Foundation
import XCTest
@testable import LunaschalCore

final class TodoRecordingTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("todo-rec-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    private func clip(_ bytes: Int = 64) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("clip-\(UUID().uuidString).m4a")
        try Data(repeating: 1, count: bytes).write(to: url)
        return url
    }

    @MainActor
    private final class Transport: TodoRecordingTransport {
        var answers: [Error?] = []
        var sent: [String] = []
        func sendTodoRecording(_ item: TodoRecording, audioURL: URL) async throws {
            XCTAssertTrue(FileManager.default.fileExists(atPath: audioURL.path))
            let answer = answers.isEmpty ? nil : answers.removeFirst()
            if let answer { throw answer }
            sent.append(item.id)
        }
    }

    func testAClipIsMovedIntoTheQueue() throws {
        let store = try TodoRecordingStore(root: root)
        let source = try clip()
        let item = try XCTUnwrap(store.add(clip: source))
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path), "moved, not copied")
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.audioURL(item).path))
        XCTAssertTrue(ULID.isValid(item.id))
        XCTAssertEqual(try store.list(), [item])
    }

    func testAnEmptyClipIsDropped() throws {
        let store = try TodoRecordingStore(root: root)
        let source = try clip(0)
        XCTAssertNil(try store.add(clip: source))
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path))
        XCTAssertEqual(try store.list(), [])
    }

    @MainActor
    func testSentClipsLeaveTheQueueInTheOrderTheyWereSaid() async throws {
        let store = try TodoRecordingStore(root: root)
        let first = try XCTUnwrap(store.add(clip: clip(), now: Date(timeIntervalSince1970: 100)))
        let second = try XCTUnwrap(store.add(clip: clip(), now: Date(timeIntervalSince1970: 200)))
        let transport = Transport()
        let sent = try await TodoRecordingSync(store: store).run(using: transport)
        XCTAssertTrue(sent)
        XCTAssertEqual(transport.sent, [first.id, second.id])
        XCTAssertEqual(try store.list(), [])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.audioURL(first).path))
    }

    @MainActor
    func testOfflineKeepsEveryClip() async throws {
        let store = try TodoRecordingStore(root: root)
        try store.add(clip: clip())
        let transport = Transport()
        transport.answers = [URLError(.notConnectedToInternet)]
        do { try await TodoRecordingSync(store: store).run(using: transport); XCTFail("offline stops the pass") } catch {}
        let kept = try store.list()
        XCTAssertEqual(kept.map(\.state), [.pending])
        XCTAssertNotNil(kept.first?.lastError)

        transport.answers = [HTTPFailure(status: 401)]
        do { try await TodoRecordingSync(store: store).run(using: transport); XCTFail() } catch {}
        XCTAssertEqual(try store.list().map(\.state), [.pending], "signed out keeps it to retry")
    }

    @MainActor
    func testARefusedClipIsKeptAsFailedAndTheRestGoOn() async throws {
        let store = try TodoRecordingStore(root: root)
        let refused = try XCTUnwrap(store.add(clip: clip(), now: Date(timeIntervalSince1970: 100)))
        let fine = try XCTUnwrap(store.add(clip: clip(), now: Date(timeIntervalSince1970: 200)))
        let transport = Transport()
        transport.answers = [HTTPFailure(status: 400), nil]
        try await TodoRecordingSync(store: store).run(using: transport)
        XCTAssertEqual(transport.sent, [fine.id])
        let left = try store.list()
        XCTAssertEqual(left.map(\.id), [refused.id])
        XCTAssertEqual(left.first?.state, .failed)
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.audioURL(refused).path), "nothing said is lost")

        // A failed clip is not retried on its own.
        try await TodoRecordingSync(store: store).run(using: transport)
        XCTAssertEqual(transport.sent, [fine.id])
    }

    func testTheReplyMustNameThisRecording() throws {
        let item = TodoRecording(id: ULID.make(), createdAt: Date(), state: .pending, lastError: nil)
        XCTAssertNoThrow(try JournalAPI.validateTodoRecordingAcknowledgement(
            Data(#"{"id": "\#(item.id)", "status": "pending"}"#.utf8), for: item))
        XCTAssertThrowsError(try JournalAPI.validateTodoRecordingAcknowledgement(
            Data(#"{"id": "someone-else"}"#.utf8), for: item))
    }

    func testDecodesWhatTheServerIsStillWorkingOn() throws {
        let rows = try JSONDecoder().decode([ServerTodoRecording].self, from: Data("""
        [{"id": "A", "status": "pending", "error": null, "transcript": null, "capturedAt": null, "createdAt": "x"},
         {"id": "B", "status": "error", "error": "No speech was heard in that recording.", "transcript": null}]
        """.utf8))
        XCTAssertEqual(rows.map(\.failed), [false, true])
        XCTAssertEqual(rows[1].error, "No speech was heard in that recording.")
    }
}
