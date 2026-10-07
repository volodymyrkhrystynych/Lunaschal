import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// A URLSession that stays safe to call after `cancel()`.
///
/// `cancel()` used to be `invalidateAndCancel()`, and starting a request on an
/// invalidated session raises an Objective-C exception that Swift cannot catch.
/// A sync pass carries on past a cancelled request — the weather and daily
/// refreshes are `try?` by design — so the very next call aborted the app. Every
/// TestFlight crash on build 4 was that. Cancelling now stops what is in flight
/// and makes every later call throw `URLError(.cancelled)`; the session itself
/// is only invalidated when nothing can reach it any more.
final class CancellableSession: @unchecked Sendable {
    private let base: URLSession
    private let lock = NSLock()
    private var cancelled = false

    init(configuration: URLSessionConfiguration, delegate: URLSessionDelegate?) {
        base = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    deinit { base.invalidateAndCancel() }

    var isCancelled: Bool {
        lock.lock(); defer { lock.unlock() }
        return cancelled
    }

    func cancel() {
        lock.lock(); cancelled = true; lock.unlock()
        base.getAllTasks { tasks in tasks.forEach { $0.cancel() } }
    }

    private func checkCancelled() throws {
        if isCancelled { throw URLError(.cancelled) }
    }

    func data(for request: URLRequest) async throws -> (Data, URLResponse) {
        try checkCancelled()
        return try await base.data(for: request)
    }

    func upload(for request: URLRequest, fromFile file: URL) async throws -> (Data, URLResponse) {
        try checkCancelled()
        return try await base.upload(for: request, fromFile: file)
    }

    func download(for request: URLRequest) async throws -> (URL, URLResponse) {
        try checkCancelled()
        return try await base.download(for: request)
    }

    #if canImport(Darwin)
    func bytes(for request: URLRequest) async throws -> (URLSession.AsyncBytes, URLResponse) {
        try checkCancelled()
        return try await base.bytes(for: request)
    }
    #endif
}
