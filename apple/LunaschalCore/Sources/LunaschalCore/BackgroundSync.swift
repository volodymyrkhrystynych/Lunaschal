import Foundation

@MainActor
public protocol BackgroundSyncLease: AnyObject {
    func onExpiration(_ handler: @escaping @MainActor () -> Void)
    func complete(success: Bool)
}

@MainActor
public protocol BackgroundSyncScheduling {
    func submit(earliest: Date) throws
    func cancel()
}

/// Owns one OS execution lease. Expiration cancels work and completes the lease
/// once; late task results cannot complete a replacement lease or reschedule it.
@MainActor
public final class BackgroundSync {
    private let scheduler: BackgroundSyncScheduling
    private let next: () throws -> Date?
    private let run: () async -> Bool
    private let cancelRun: () -> Void
    private let status: (String) -> Void
    private var scheduled: Date?
    private var active: UUID?
    private var worker: Task<Void, Never>?

    public init(scheduler: BackgroundSyncScheduling, next: @escaping () throws -> Date?,
                run: @escaping () async -> Bool, cancelRun: @escaping () -> Void,
                status: @escaping (String) -> Void) {
        self.scheduler = scheduler; self.next = next; self.run = run
        self.cancelRun = cancelRun; self.status = status
    }

    public func schedule() {
        do {
            guard let date = try next() else {
                scheduler.cancel(); scheduled = nil
                status("Background sync is idle.")
                return
            }
            // Repeated foreground ticks must not keep moving a pending request.
            if let scheduled, scheduled <= date { return }
            scheduler.cancel(); scheduled = nil
            try scheduler.submit(earliest: date)
            scheduled = date
            status("Background sync requested. iOS chooses when it runs.")
        } catch {
            status("Background scheduling unavailable. Open the app to sync. \(error.localizedDescription)")
        }
    }

    @discardableResult
    public func handle(_ lease: BackgroundSyncLease) -> Task<Void, Never>? {
        guard active == nil else { lease.complete(success: false); return nil }
        let id = UUID()
        active = id; scheduled = nil
        lease.onExpiration { [weak self, weak lease] in
            guard let self, let lease, self.active == id else { return }
            self.worker?.cancel()
            self.cancelRun()
            self.finish(id, lease: lease, success: false)
        }
        guard active == id else { return nil }
        status("Syncing during background execution time…")
        worker = Task { [weak self] in
            guard let self else { return }
            guard !Task.isCancelled, self.active == id else { return }
            let success = await self.run()
            self.finish(id, lease: lease, success: success && !Task.isCancelled)
        }
        return worker
    }

    private func finish(_ id: UUID, lease: BackgroundSyncLease, success: Bool) {
        guard active == id else { return }
        active = nil; worker = nil
        lease.complete(success: success)
        schedule()
    }
}

public enum BackgroundSyncPlan {
    public static func next(captures: [Capture], attempts: [TransferAttempt], hasEdits: Bool,
                            signedIn: Bool, enabled: Bool, now: Date) -> Date? {
        guard signedIn, enabled else { return nil }
        let states = Dictionary(attempts.map { ($0.captureID, $0) }, uniquingKeysWith: { _, last in last })
        var dates = captures.filter { $0.state == .pending }.compactMap { capture -> Date? in
            if let attempt = states[capture.id] {
                if attempt.state == .authentication || attempt.state == .rejected { return nil }
                return attempt.retryAt ?? now
            }
            return now
        }
        if hasEdits { dates.append(now) }
        guard let date = dates.min() else { return nil }
        return max(date, now.addingTimeInterval(60))
    }
}
