import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Registering this phone for notifications, and answering one from the lock
// screen (backend/routes/push.py, backend/routes/coach.py).

/// Which APNs host a device token belongs to: Xcode builds get sandbox
/// tokens, TestFlight and App Store builds production ones.
public enum PushEnvironment: String, Codable {
    case sandbox, production
}

public enum PushToken {
    /// The hex string APNs and the server use for a device token.
    public static func hex(_ token: Data) -> String { token.map { String(format: "%02x", $0) }.joined() }
}

extension JournalAPI {
    /// `coach` is the Settings switch for reminders; the device row stays
    /// when it's off, because the Code page's notifications use it too.
    public func registerPushDevice(token: String, environment: PushEnvironment, bundleID: String?,
                                   appVersion: String?, coach: Bool) async throws {
        var req = request("api/push/devices", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: [
            "token": token, "environment": environment.rawValue,
            "bundleId": bundleID ?? "", "appVersion": appVersion ?? "", "coach": coach,
        ])
        try await expectSuccess(req)
    }

    /// A reply typed on a notification. The id makes a retry one message, and
    /// the server has the agent answer it with the app closed.
    public func coachReply(id: String, text: String) async throws {
        var req = request("api/coach/reply", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["id": try Self.pathID(id), "text": text])
        try await expectSuccess(req)
    }

    /// What was done with a reminder — opened, done, snoozed — recorded as
    /// context for what the coach learns. Best effort; it's never retried.
    public func coachResponse(decisionID: String, action: String, at: Date) async throws {
        var req = request("api/coach/nudges/\(try Self.pathID(decisionID))/response", method: "POST")
        var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "capturedAt", value: ISO8601DateFormatter().string(from: at))]
        req.url = parts.url
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["action": action])
        try await expectSuccess(req)
    }

    public func coachSettings() async throws -> CoachSettings {
        try JSONDecoder().decode(CoachSettings.self, from: await get("api/coach/settings", [:]))
    }

    public func updateCoachSettings(_ changes: [String: Any]) async throws -> CoachSettings {
        var req = request("api/coach/settings", method: "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: changes)
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw HTTPFailure(status: (response as? HTTPURLResponse)?.statusCode ?? 0)
        }
        return try JSONDecoder().decode(CoachSettings.self, from: data)
    }

    public func coachStats() async throws -> [CoachStat] {
        try JSONDecoder().decode([CoachStat].self, from: await get("api/coach/stats", [:]))
    }

    private func expectSuccess(_ req: URLRequest) async throws {
        let (_, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw HTTPFailure(status: http.statusCode) }
    }
}
