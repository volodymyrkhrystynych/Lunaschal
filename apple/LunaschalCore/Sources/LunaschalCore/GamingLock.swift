import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// The gaming lock (backend/gaming/lock.py): on a lock weekday the server keeps
// game launchers closed until work is closed out — normally by telling the
// chat you're done, which ends in an `unlock_gaming` card. The override here
// is the way round the review: it takes a reason and counts as a missed item
// in that day's review.

/// `GET /api/gaming`, and what every gaming route answers with.
public struct GamingStatus: Codable, Equatable {
    public struct Settings: Codable, Equatable {
        public var enabled: Bool
        /// Weekdays the lock covers, 0 = Monday … 6 = Sunday (Python's weekday()).
        public var days: [Int]
        public var patterns: [String]
    }

    public var day: String
    public var applies: Bool
    public var locked: Bool
    public var unlockedAt: Double?
    public var method: String?
    public var reason: String?
    public var ratingAtUnlock: Int?
    public var workEnd: String?
    public var settings: Settings

    /// Shortest an override reason may be; the server enforces the same.
    public static let minimumReason = 10

    /// The weekday labels in the server's order, Monday first.
    public static let weekdayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    /// One line for the settings screen: where today stands.
    public func summary(timeZone: TimeZone = .current) -> String {
        if !settings.enabled { return "Off" }
        if !applies { return "Not a lock day — games are open" }
        if locked {
            return "Locked until you tell the chat you're done with work"
                + (workEnd.map { " (work ends \($0))" } ?? "")
        }
        var text = method == "override" ? "Unlocked without the review" : "Unlocked after the review"
        if let unlockedAt {
            let formatter = DateFormatter()
            formatter.dateFormat = "HH:mm"
            formatter.timeZone = timeZone
            text += " at \(formatter.string(from: Date(timeIntervalSince1970: unlockedAt)))"
        }
        if let workEnd { text += " · work ended \(workEnd)" }
        return text
    }

    /// Whether `reason` is long enough to send as an override.
    public static func acceptableReason(_ reason: String) -> Bool {
        reason.trimmingCharacters(in: .whitespacesAndNewlines).count >= minimumReason
    }
}

/// The server's own words for why it refused ("Say why, in at least 10
/// characters", "Games are not locked today"), so the screen can show them.
public struct GamingLockRefusal: LocalizedError, Equatable {
    public let message: String
    public var errorDescription: String? { message }

    /// The `error` field of a refusal body, if it has one.
    public static func message(from data: Data) -> String? {
        guard let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let message = reply["error"] as? String, !message.isEmpty else { return nil }
        return message
    }
}

extension JournalAPI {
    public func gamingStatus() async throws -> GamingStatus {
        try JSONDecoder().decode(GamingStatus.self, from: await get("api/gaming", [:]))
    }

    public func updateGamingSettings(_ changes: [String: Any]) async throws -> GamingStatus {
        try await sendGaming("api/gaming/settings", method: "PATCH", body: changes)
    }

    /// Unlock today without the end-of-work review. The reason is kept and
    /// shown in tomorrow's review.
    public func overrideGamingLock(reason: String, pauseInference: Bool) async throws -> GamingStatus {
        try await sendGaming("api/gaming/override", method: "POST",
                             body: ["reason": reason, "pauseInference": pauseInference])
    }

    private func sendGaming(_ path: String, method: String, body: [String: Any]) async throws -> GamingStatus {
        var req = request(path, method: method)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if (400..<500).contains(status), let message = GamingLockRefusal.message(from: data) {
                throw GamingLockRefusal(message: message)
            }
            throw HTTPFailure(status: status)
        }
        return try JSONDecoder().decode(GamingStatus.self, from: data)
    }
}
