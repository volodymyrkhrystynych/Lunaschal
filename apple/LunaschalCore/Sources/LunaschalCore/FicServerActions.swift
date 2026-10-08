import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Asking the server to fetch new chapters. Nothing is downloaded here: both
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
public struct FicUpdateReply: Decodable, Equatable {
    public let queued: Bool
    public let deep: Bool?

    public init(queued: Bool, deep: Bool? = nil) {
        self.queued = queued
        self.deep = deep
    }

    public func summary(title: String) -> String {
        guard queued else { return "Stopped waiting for an update to “\(title)”." }
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
    public func checkFicForUpdates(_ ficID: String, deep: Bool = false) async throws -> FicUpdateReply {
        guard ULID.isValid(ficID) else { throw CaptureError.invalidID }
        return try JSONDecoder().decode(FicUpdateReply.self,
                                        from: await postFanfic("api/fanfic/\(ficID)/check-updates", ["deep": deep]))
    }

    public func refreshFicAlerts() async throws -> FicRefreshSummary {
        try JSONDecoder().decode(FicRefreshSummary.self,
                                 from: await postFanfic("api/fanfic/refresh-alerts", [String: Bool]()))
    }

    func postFanfic<Body: Encodable>(_ path: String, _ body: Body) async throws -> Data {
        var req = request(path, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        // Reading every forum's alerts page is several slow requests in a row.
        req.timeoutInterval = 120
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw FicServerFailure(status: http.statusCode, body: data) }
        return data
    }
}
