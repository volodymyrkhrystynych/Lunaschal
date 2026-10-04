import Foundation

public struct TransferAttempt: Codable, Equatable {
    public enum State: String, Codable { case sending, waiting, authentication, rejected }
    public let captureID: String
    public let attemptID: String
    public var attempts: Int
    public var state: State
    public var retryAt: Date?
}

/// Foreground attempt ledger, serialized by CaptureSync's main actor. It is not
/// yet a mapping to system background tasks: recovery may run only when no task
/// from the previous execution can still deliver a completion.
public final class TransferStore {
    public enum Outcome { case retryable, authentication, rejected, cancelled }
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func all() throws -> [TransferAttempt] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .compactMap { try load($0.deletingPathExtension().lastPathComponent) }
    }

    public func load(_ id: String) throws -> TransferAttempt? {
        let url = try file(id)
        guard fm.fileExists(atPath: url.path) else { return nil }
        let value = try JSONDecoder().decode(TransferAttempt.self, from: Data(contentsOf: url))
        guard value.captureID == id, ULID.isValid(value.attemptID), value.attempts >= 0 else {
            throw CaptureError.invalidResponse
        }
        return value
    }

    public func begin(_ id: String, now: Date) throws -> TransferAttempt? {
        let previous = try load(id)
        if let previous {
            guard previous.state == .waiting, (previous.retryAt ?? .distantPast) <= now else { return nil }
        }
        let value = TransferAttempt(captureID: id, attemptID: ULID.make(),
            attempts: min(previous?.attempts ?? 0, 1000) + 1, state: .sending, retryAt: nil)
        try save(value)
        return value
    }

    @discardableResult
    public func finish(_ attempt: TransferAttempt, outcome: Outcome, now: Date) throws -> Bool {
        guard var current = try load(attempt.captureID), current.attemptID == attempt.attemptID,
              current.state == .sending else { return false }
        current.retryAt = nil
        switch outcome {
        case .retryable:
            current.state = .waiting
            let delay = min(1800, 30 * pow(2, Double(min(max(current.attempts - 1, 0), 6))))
            current.retryAt = now.addingTimeInterval(delay)
        case .authentication: current.state = .authentication
        case .rejected: current.state = .rejected
        case .cancelled:
            current.state = .waiting
            current.attempts = max(0, current.attempts - 1)
            current.retryAt = now
        }
        try save(current)
        return true
    }

    public func isCurrent(_ attempt: TransferAttempt) throws -> Bool {
        guard let current = try load(attempt.captureID) else { return false }
        return current.state == .sending && current.attemptID == attempt.attemptID
    }

    public func recoverInterrupted(now: Date) throws {
        for var attempt in try all() where attempt.state == .sending {
            attempt.state = .waiting
            attempt.retryAt = now
            try save(attempt)
        }
    }

    public func retryNow(_ id: String, now: Date) throws {
        guard var attempt = try load(id), attempt.state != .sending else { return }
        attempt.state = .waiting
        attempt.retryAt = now
        attempt.attempts = 0
        try save(attempt)
    }

    public func resumeAuthentication(now: Date) throws {
        for attempt in try all() where attempt.state == .authentication { try retryNow(attempt.captureID, now: now) }
    }

    public func retryWaiting(now: Date) throws {
        for attempt in try all() where attempt.state == .waiting { try retryNow(attempt.captureID, now: now) }
    }

    public func discardAfterSync(_ capture: Capture) throws {
        guard capture.state == .synced else { return }
        let url = try file(capture.id)
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
    }

    private func file(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id).appendingPathExtension("json")
    }
    private func save(_ value: TransferAttempt) throws {
        try JSONEncoder().encode(value).write(to: file(value.captureID), options: .atomic)
    }
}
