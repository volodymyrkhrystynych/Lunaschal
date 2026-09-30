import Foundation
import XCTest
@testable import LunaschalCore

final class BackgroundSyncTests: XCTestCase {
    @MainActor
    func testPendingScheduleDoesNotDriftAndDisablingCancelsIt() throws {
        let scheduler = FakeBackgroundScheduler()
        let time = Date(timeIntervalSince1970: 1000)
        var next: Date? = time
        let coordinator = BackgroundSync(scheduler: scheduler, next: { next }, run: { true }, cancelRun: {}, status: { _ in })
        coordinator.schedule()
        next = time.addingTimeInterval(60)
        coordinator.schedule()
        XCTAssertEqual(scheduler.requests, [time])
        next = time.addingTimeInterval(-1)
        coordinator.schedule()
        XCTAssertEqual(scheduler.requests, [time, time.addingTimeInterval(-1)])
        let cancellations = scheduler.cancellations
        next = nil
        coordinator.schedule()
        XCTAssertEqual(scheduler.cancellations, cancellations + 1)
    }

    @MainActor
    func testSuccessfulWorkCompletesLeaseExactlyOnce() async {
        let scheduler = FakeBackgroundScheduler(), lease = FakeBackgroundLease()
        var runs = 0, cancellations = 0
        let coordinator = BackgroundSync(scheduler: scheduler, next: { nil }, run: { runs += 1; return true },
            cancelRun: { cancellations += 1 }, status: { _ in })
        await coordinator.handle(lease)?.value
        lease.expire()
        XCTAssertEqual(lease.completions, [true])
        XCTAssertEqual(runs, 1)
        XCTAssertEqual(cancellations, 0)
    }

    @MainActor
    func testExpirationCancelsOnceAndIgnoresLateSuccess() async {
        let scheduler = FakeBackgroundScheduler(), lease = FakeBackgroundLease()
        let started = expectation(description: "Started background work")
        var continuation: CheckedContinuation<Bool, Never>?
        var cancellations = 0
        let coordinator = BackgroundSync(scheduler: scheduler, next: { Date(timeIntervalSince1970: 1000) },
            run: { await withCheckedContinuation { continuation = $0; started.fulfill() } },
            cancelRun: { cancellations += 1 }, status: { _ in })
        let work = coordinator.handle(lease)
        await fulfillment(of: [started], timeout: 2)
        let duplicate = FakeBackgroundLease()
        XCTAssertNil(coordinator.handle(duplicate))
        XCTAssertEqual(duplicate.completions, [false])
        lease.expire(); lease.expire()
        XCTAssertEqual(lease.completions, [false])
        XCTAssertEqual(cancellations, 1)
        let submissions = scheduler.requests
        continuation?.resume(returning: true)
        await work?.value
        XCTAssertEqual(lease.completions, [false])
        XCTAssertEqual(scheduler.requests, submissions)
    }

    @MainActor
    func testExpirationBeforeWorkStartsNeverCallsUploader() async {
        let scheduler = FakeBackgroundScheduler(), lease = FakeBackgroundLease()
        lease.expiresImmediately = true
        var runs = 0
        let coordinator = BackgroundSync(scheduler: scheduler, next: { nil }, run: { runs += 1; return true },
            cancelRun: {}, status: { _ in })
        XCTAssertNil(coordinator.handle(lease))
        XCTAssertEqual(runs, 0)
        XCTAssertEqual(lease.completions, [false])
    }

    @MainActor
    func testExpirationLeavesRealCapturePendingWithRecoverableAttempt() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        let transfers = try TransferStore(root: root.appendingPathComponent("transfers"))
        let capture = Capture(text: "Survive the background deadline")
        try store.save(capture)
        let sync = CaptureSync(store: store, transfers: transfers)
        let started = expectation(description: "Upload began")
        let transport = SuspendedTransport(started: { started.fulfill() })
        let lease = FakeBackgroundLease()
        let coordinator = BackgroundSync(scheduler: FakeBackgroundScheduler(), next: { nil }, run: {
            do { try await sync.run(using: transport); return true } catch { return false }
        }, cancelRun: {}, status: { _ in })
        let work = coordinator.handle(lease)
        await fulfillment(of: [started], timeout: 2)
        lease.expire()
        transport.resume?()
        await work?.value
        XCTAssertEqual(lease.completions, [false])
        XCTAssertEqual(try store.load(capture.id), capture)
        XCTAssertEqual(try transfers.load(capture.id)?.state, .waiting)
        XCTAssertEqual(try transfers.load(capture.id)?.attempts, 0)
    }

    @MainActor
    func testSchedulingFailureCanRecoverWithoutDroppingPendingWork() {
        let scheduler = FakeBackgroundScheduler()
        scheduler.shouldFail = true
        var statuses: [String] = []
        let coordinator = BackgroundSync(scheduler: scheduler, next: { Date(timeIntervalSince1970: 1000) },
            run: { true }, cancelRun: {}, status: { statuses.append($0) })
        coordinator.schedule()
        XCTAssertTrue(statuses.last?.contains("unavailable") == true)
        scheduler.shouldFail = false
        coordinator.schedule()
        XCTAssertEqual(scheduler.requests.count, 1)
    }

    func testPlanRespectsRetryDeadlinesAuthenticationAndUserChoice() {
        let now = Date(timeIntervalSince1970: 1000)
        let capture = Capture(text: "Thought")
        var attempt = TransferAttempt(captureID: capture.id, attemptID: ULID.make(), attempts: 1,
                                      state: .waiting, retryAt: now.addingTimeInterval(300))
        func next(enabled: Bool = true, signedIn: Bool = true, edits: Bool = false) -> Date? {
            BackgroundSyncPlan.next(captures: [capture], attempts: [attempt], hasEdits: edits,
                                    signedIn: signedIn, enabled: enabled, now: now)
        }
        XCTAssertEqual(next(), now.addingTimeInterval(300))
        XCTAssertNil(next(enabled: false))
        XCTAssertNil(next(signedIn: false))
        XCTAssertEqual(next(edits: true), now.addingTimeInterval(60))
        attempt.state = .authentication
        XCTAssertNil(next())
        attempt.state = .rejected
        XCTAssertNil(next())
        attempt.state = .sending
        attempt.retryAt = nil
        XCTAssertEqual(next(), now.addingTimeInterval(60))
        XCTAssertNil(BackgroundSyncPlan.next(captures: [], attempts: [], hasEdits: false,
                                            signedIn: true, enabled: true, now: now))
    }
}

private final class SuspendedTransport: JournalTransport {
    let started: () -> Void
    var resume: (() -> Void)?
    init(started: @escaping () -> Void) { self.started = started }
    func send(_ capture: Capture, audioURL: URL?) async throws {
        await withCheckedContinuation { continuation in
            resume = { continuation.resume() }
            started()
        }
        try Task.checkCancellation()
    }
    func fetch(_ id: String) async throws -> JournalSnapshot { throw URLError(.cancelled) }
}

@MainActor
private final class FakeBackgroundScheduler: BackgroundSyncScheduling {
    var requests: [Date] = []
    var cancellations = 0
    var shouldFail = false
    func submit(earliest: Date) throws {
        if shouldFail { throw URLError(.notConnectedToInternet) }
        requests.append(earliest)
    }
    func cancel() { cancellations += 1 }
}

@MainActor
private final class FakeBackgroundLease: BackgroundSyncLease {
    var completions: [Bool] = []
    var handler: (@MainActor () -> Void)?
    var expiresImmediately = false
    func onExpiration(_ handler: @escaping @MainActor () -> Void) {
        self.handler = handler
        if expiresImmediately { handler() }
    }
    func complete(success: Bool) { completions.append(success) }
    func expire() { handler?() }
}
