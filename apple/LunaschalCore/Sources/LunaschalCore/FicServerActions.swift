import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Asking the server to fetch new chapters. Nothing is downloaded here: these
/// routes only queue work for the server's serial drain worker, which stays
/// polite to the forums, and the new chapters arrive through the next sync.

/// The `sourceType`s the server can fetch again: the forums and the three
/// sites. An uploaded EPUB, DOCX or PDF has nowhere to update from.
public enum FicSources {
    public static let updatable: Set<String> = ["xenforo", "fanfiction", "ao3", "patreon"]

    public static func isUpdatable(_ sourceType: String?) -> Bool {
        sourceType.map(updatable.contains) ?? false
    }
}

/// `POST /api/fanfic/<id>/check-updates` toggles the fic's place in that
/// queue, so asking twice before the worker reaches it cancels the request.
///
/// `force` ("Re-download all chapters") rewrites every saved chapter, edited
/// or not: how a chapter saved before the server understood a forum's
/// colours and spoilers gets them back.
public struct FicUpdateReply: Decodable, Equatable {
    public let queued: Bool
    public let deep: Bool?
    public let force: Bool?

    public init(queued: Bool, deep: Bool? = nil, force: Bool? = nil) {
        self.queued = queued
        self.deep = deep
        self.force = force
    }

    public func summary(title: String) -> String {
        guard queued else { return "Stopped waiting for an update to “\(title)”." }
        if force == true {
            return "Every chapter of “\(title)” will be downloaded again on the server. They arrive with the next sync."
        }
        return deep == true
            ? "“\(title)” will be re-read in full on the server. Edited chapters arrive with the next sync."
            : "“\(title)” is queued for an update on the server. New chapters arrive with the next sync."
    }
}

/// `POST /api/fanfic/refresh-alerts`: reads each signed-in forum's alerts and
/// queues the threads they mention.
public struct FicRefreshSummary: Decodable, Equatable {
    public let flagged: Int
    public let newImports: Int
    public let skippedActive: Int
    public let alertsSeen: Int
    public let errors: [String: String]

    public init(flagged: Int, newImports: Int, skippedActive: Int, alertsSeen: Int, errors: [String: String] = [:]) {
        self.flagged = flagged
        self.newImports = newImports
        self.skippedActive = skippedActive
        self.alertsSeen = alertsSeen
        self.errors = errors
    }

    public var summary: String {
        var lines: [String] = []
        if flagged + newImports == 0 {
            lines.append(alertsSeen == 0 ? "No new alerts on the forums." : "Nothing new to fetch.")
        } else {
            var parts: [String] = []
            if flagged > 0 { parts.append("\(flagged) \(flagged == 1 ? "fic" : "fics") to update") }
            if newImports > 0 { parts.append("\(newImports) new \(newImports == 1 ? "fic" : "fics") to import") }
            lines.append("Queued on the server: \(parts.joined(separator: ", ")). They arrive with the next sync.")
        }
        if skippedActive > 0 {
            lines.append("\(skippedActive) already queued or downloading.")
        }
        for (site, error) in errors.sorted(by: { $0.key < $1.key }) {
            lines.append("\(site): \(error)")
        }
        return lines.joined(separator: "\n")
    }
}

/// `POST /api/fanfic/refresh-alerts` only starts the read now, and
/// `GET …/refresh-alerts/status` has its outcome: reading every forum's
/// alerts outlasted the request a phone keeps open once it leaves the app.
public struct FicAlertRefreshStatus: Decodable, Equatable {
    public let state: String
    public let result: FicRefreshSummary?
    public let error: String?

    public init(state: String, result: FicRefreshSummary? = nil, error: String? = nil) {
        self.state = state
        self.result = result
        self.error = error
    }
}

public enum FicAlertRefreshError: LocalizedError, Equatable {
    case failed(String)
    /// The server restarted mid-read and has nothing to report.
    case lost

    public var errorDescription: String? {
        switch self {
        case .failed(let reason): return "Refreshing the forums failed: \(reason)"
        case .lost: return "The server restarted before the forums were read. Try again."
        }
    }
}

public enum FicAlertRefresh {
    /// Waits out a refresh `start` began. A poll that fails (the app was in
    /// the background, the network changed) is tried again, up to
    /// `tolerated` in a row; only the server's own answer ends it.
    public static func wait(start: () async throws -> FicAlertRefreshStatus,
                            status: () async throws -> FicAlertRefreshStatus,
                            interval: Duration = .seconds(2), tolerated: Int = 10,
                            sleep: (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) async throws -> FicRefreshSummary {
        var current = try await start()
        var failures = 0
        while current.state == "running" {
            try await sleep(interval)
            do {
                current = try await status()
                failures = 0
            } catch {
                if error is CancellationError { throw error }
                failures += 1
                if failures > tolerated { throw error }
            }
        }
        if current.state == "error" { throw FicAlertRefreshError.failed(current.error ?? "unknown error") }
        guard current.state == "done", let result = current.result else { throw FicAlertRefreshError.lost }
        return result
    }
}

/// A refusal the fanfic routes explain in their `{"error": …}` body, such as
/// "A download is already running for this fic". `HTTPFailure`'s wording is
/// about captures waiting on the device, which says nothing true here.
public struct FicServerFailure: LocalizedError, Equatable {
    public let status: Int
    public let message: String?

    public init(status: Int, body: Data) {
        self.status = status
        struct Body: Decodable { let error: String? }
        let text = (try? JSONDecoder().decode(Body.self, from: body))?.error?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        message = text?.isEmpty == false ? text : nil
    }

    public var errorDescription: String? {
        if let message { return message }
        switch status {
        case 401, 403: return "Sign in again to reach the server."
        case 404: return "The server no longer has this fic."
        default: return "Server returned HTTP \(status)."
        }
    }
}

extension JournalAPI {
    public func checkFicForUpdates(_ ficID: String, deep: Bool = false, force: Bool = false) async throws -> FicUpdateReply {
        guard ULID.isValid(ficID) else { throw CaptureError.invalidID }
        return try JSONDecoder().decode(FicUpdateReply.self,
                                        from: await postFanfic("api/fanfic/\(ficID)/check-updates",
                                                               ["deep": deep || force, "force": force]))
    }

    public func refreshFicAlerts() async throws -> FicRefreshSummary {
        try await FicAlertRefresh.wait(start: startFicAlertRefresh, status: ficAlertRefreshStatus)
    }

    /// Starts the read. A server from before the status route answers with
    /// the summary itself, which counts as already done.
    public func startFicAlertRefresh() async throws -> FicAlertRefreshStatus {
        let data = try await postFanfic("api/fanfic/refresh-alerts", [String: Bool](), timeout: 30)
        if let status = try? JSONDecoder().decode(FicAlertRefreshStatus.self, from: data) { return status }
        return FicAlertRefreshStatus(state: "done", result: try JSONDecoder().decode(FicRefreshSummary.self, from: data))
    }

    public func ficAlertRefreshStatus() async throws -> FicAlertRefreshStatus {
        var req = request("api/fanfic/refresh-alerts/status")
        req.timeoutInterval = 30
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw FicServerFailure(status: http.statusCode, body: data) }
        return try JSONDecoder().decode(FicAlertRefreshStatus.self, from: data)
    }

    /// Re-get one forum chapter from its post, in case the author edited it.
    /// The new text arrives through the next sync.
    public func refetchFicChapter(_ chapterID: String) async throws {
        guard ULID.isValid(chapterID) else { throw CaptureError.invalidID }
        _ = try await postFanfic("api/fanfic/chapters/\(chapterID)/refetch", [String: Bool]())
    }

    func postFanfic<Body: Encodable>(_ path: String, _ body: Body, timeout: TimeInterval = 120) async throws -> Data {
        var req = request(path, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        req.timeoutInterval = timeout
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw FicServerFailure(status: http.statusCode, body: data) }
        return data
    }
}
