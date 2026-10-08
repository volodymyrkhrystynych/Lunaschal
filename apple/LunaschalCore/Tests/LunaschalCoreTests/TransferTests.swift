import Foundation
import XCTest
@testable import LunaschalCore

final class TransferTests: XCTestCase {
    private let time = Date(timeIntervalSince1970: 1_790_000_000)
    private func root() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testInterruptedAttemptRecoversWithNewIdentityAndRejectsStaleCompletion() throws {
        let directory = try root(), store = try TransferStore(root: directory), id = ULID.make()
        let first = try XCTUnwrap(store.begin(id, now: time))
        XCTAssertNil(try store.begin(id, now: time))
        let reopened = try TransferStore(root: directory)
        XCTAssertEqual(try reopened.load(id), first)
        try reopened.recoverInterrupted(now: time)
        XCTAssertFalse(try reopened.finish(first, outcome: .rejected, now: time))
        let second = try XCTUnwrap(reopened.begin(id, now: time))
        XCTAssertNotEqual(first.attemptID, second.attemptID)
        XCTAssertFalse(try reopened.finish(first, outcome: .retryable, now: time))
        XCTAssertEqual(try reopened.load(id), second)
    }

    func testBackoffPersistsAcrossReopenAndCapsAtThirtyMinutes() throws {
        let directory = try root(), id = ULID.make()
        var now = time
        for index in 0..<12 {
            let store = try TransferStore(root: directory)
            let attempt = try XCTUnwrap(store.begin(id, now: now))
            try store.finish(attempt, outcome: .retryable, now: now)
            let retry = try XCTUnwrap(store.load(id)?.retryAt)
            XCTAssertEqual(retry.timeIntervalSince(now), min(1800, 30 * pow(2, Double(index))))
            XCTAssertNil(try TransferStore(root: directory).begin(id, now: retry.addingTimeInterval(-1)))
            now = retry
        }
        try TransferStore(root: directory).retryWaiting(now: time)
        XCTAssertEqual(try TransferStore(root: directory).begin(id, now: time)?.attempts, 1)
    }

    func testAuthenticationAndRejectionNeedTheirExplicitResumeActions() throws {
        let store = try TransferStore(root: root()), authID = ULID.make(), rejectedID = ULID.make()
        try store.finish(XCTUnwrap(store.begin(authID, now: time)), outcome: .authentication, now: time)
        try store.finish(XCTUnwrap(store.begin(rejectedID, now: time)), outcome: .rejected, now: time)
        try store.retryWaiting(now: time)
        XCTAssertNil(try store.begin(authID, now: .distantFuture))
        XCTAssertNil(try store.begin(rejectedID, now: .distantFuture))
        try store.resumeAuthentication(now: time)
        XCTAssertNotNil(try store.begin(authID, now: time))
        XCTAssertNil(try store.begin(rejectedID, now: time))
        try store.retryNow(rejectedID, now: time)
        XCTAssertNotNil(try store.begin(rejectedID, now: time))
    }

    func testOnlyAcknowledgedCapturesDiscardAttemptState() throws {
        let store = try TransferStore(root: root())
        var capture = Capture(text: "Keep me")
        _ = try store.begin(capture.id, now: time)
        try store.discardAfterSync(capture)
        XCTAssertNotNil(try store.load(capture.id))
        capture.state = .synced
        try store.discardAfterSync(capture)
        XCTAssertNil(try store.load(capture.id))
    }

    func testMismatchedManifestCannotRedirectAnAttempt() throws {
        let directory = try root(), store = try TransferStore(root: directory)
        let first = ULID.make(), second = ULID.make()
        let value = try XCTUnwrap(store.begin(second, now: time))
        try JSONEncoder().encode(value).write(to: directory.appendingPathComponent(first).appendingPathExtension("json"))
        XCTAssertThrowsError(try store.begin(first, now: time))
        XCTAssertEqual(try store.load(second), value)
    }

    @MainActor
    func testLostReplyWaitsAcrossRelaunchThenReplaysSameCapture() async throws {
        let directory = try root(), captures = try CaptureStore(root: directory)
        let transfers = try TransferStore(root: directory.appendingPathComponent("transfers"))
        let capture = Capture(text: "Original thought")
        try captures.save(capture)
        let transport = RetryTransport()
        transport.error = URLError(.networkConnectionLost)
        var now = time
        do {
            try await CaptureSync(store: captures, transfers: transfers, now: { now }).run(using: transport)
            XCTFail("Expected lost reply")
        } catch {}
        transport.error = nil
        let reopened = try TransferStore(root: transfers.root)
        let restarted = CaptureSync(store: captures, transfers: reopened, now: { now })
        try await restarted.run(using: transport)
        XCTAssertEqual(transport.sent, [capture.id])
        XCTAssertEqual(try captures.load(capture.id).state, .pending)
        now = now.addingTimeInterval(30)
        try await restarted.run(using: transport)
        XCTAssertEqual(transport.sent, [capture.id, capture.id])
        XCTAssertEqual(try captures.load(capture.id).state, .synced)
        XCTAssertTrue(try reopened.all().isEmpty)
    }

    @MainActor
    func testAuthenticationPauseSurvivesRestartUntilLoginResumesIt() async throws {
        let directory = try root(), captures = try CaptureStore(root: directory)
        let transfers = try TransferStore(root: directory.appendingPathComponent("transfers"))
        let first = Capture(text: "First", now: time), second = Capture(text: "Second", now: time.addingTimeInterval(1))
        try captures.save(first); try captures.save(second)
        let transport = RetryTransport()
        transport.error = HTTPFailure(status: 401)
        let sync = CaptureSync(store: captures, transfers: transfers, now: { self.time })
        do { try await sync.run(using: transport); XCTFail("Expected authentication failure") } catch {}
        transport.error = nil
        do { try await sync.run(using: transport); XCTFail("Login should still be required") } catch {}
        XCTAssertEqual(transport.sent, [first.id])
        try transfers.resumeAuthentication(now: time)
        try await sync.run(using: transport)
        XCTAssertEqual(transport.sent, [first.id, first.id, second.id])
        XCTAssertTrue(try transfers.all().isEmpty)
    }

    @MainActor
    func testObsoleteSuccessCannotAcknowledgeANewerAttempt() async throws {
        let directory = try root(), captures = try CaptureStore(root: directory)
        let transfers = try TransferStore(root: directory.appendingPathComponent("transfers"))
        let capture = Capture(text: "Keep newer attempt pending")
        try captures.save(capture)
        let transport = RetryTransport()
        var newer: TransferAttempt?
        transport.onSend = { id in
            try transfers.recoverInterrupted(now: self.time)
            newer = try transfers.begin(id, now: self.time)
        }
        do {
            try await CaptureSync(store: captures, transfers: transfers, now: { self.time }).run(using: transport)
            XCTFail("Obsolete completion should be ignored")
        } catch is CancellationError {}
        // Unchanged, except that the interrupted send may have landed.
        XCTAssertEqual(try captures.load(capture.id), sent(capture))
        XCTAssertEqual(try transfers.load(capture.id), newer)
        XCTAssertEqual(newer?.state, .sending)
    }

    @MainActor
    func testObsoleteRejectionCannotFailANewerAttempt() async throws {
        let directory = try root(), captures = try CaptureStore(root: directory)
        let transfers = try TransferStore(root: directory.appendingPathComponent("transfers"))
        let capture = Capture(text: "Keep newer attempt pending")
        try captures.save(capture)
        let transport = RetryTransport()
        var newer: TransferAttempt?
        transport.onSend = { id in
            try transfers.recoverInterrupted(now: self.time)
            newer = try transfers.begin(id, now: self.time)
        }
        transport.error = HTTPFailure(status: 400)
        do {
            try await CaptureSync(store: captures, transfers: transfers, now: { self.time }).run(using: transport)
            XCTFail("Obsolete rejection should be ignored")
        } catch is CancellationError {}
        // Unchanged, except that the interrupted send may have landed.
        XCTAssertEqual(try captures.load(capture.id), sent(capture))
        XCTAssertEqual(try transfers.load(capture.id), newer)
        XCTAssertEqual(newer?.state, .sending)
    }

    @MainActor
    func testCancellationKeepsCaptureAndDoesNotIncreaseFailureBackoff() async throws {
        let directory = try root(), captures = try CaptureStore(root: directory)
        let transfers = try TransferStore(root: directory.appendingPathComponent("transfers"))
        let capture = Capture(text: "Cancel transfer, keep capture")
        try captures.save(capture)
        let transport = RetryTransport()
        transport.error = URLError(.cancelled)
        let sync = CaptureSync(store: captures, transfers: transfers, now: { self.time })
        do { try await sync.run(using: transport); XCTFail("Expected cancellation") } catch {}
        // Unchanged, except that the interrupted send may have landed.
        XCTAssertEqual(try captures.load(capture.id), sent(capture))
        XCTAssertEqual(try transfers.load(capture.id)?.attempts, 0)
        transport.error = nil
        try await sync.run(using: transport)
        XCTAssertEqual(try captures.load(capture.id).state, .synced)
    }
}

private final class RetryTransport: JournalTransport {
    var sent: [String] = []
    var error: Error?
    var onSend: ((String) throws -> Void)?
    func send(_ capture: Capture, audioURL: URL?) async throws {
        sent.append(capture.id)
        try onSend?(capture.id)
        if let error { throw error }
    }
    func fetch(_ id: String) async throws -> JournalSnapshot {
        try JSONDecoder().decode(JournalSnapshot.self, from: Data("{\"id\":\"\(id)\",\"content\":\"Saved\"}".utf8))
    }
}

/// A capture as stored once a send of it began: possibly on the server.
private func sent(_ capture: Capture) -> Capture {
    var capture = capture
    capture.mayBeOnServer = true
    return capture
}
