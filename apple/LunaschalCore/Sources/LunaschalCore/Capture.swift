import Foundation

public enum CaptureMode: String, Codable, CaseIterable {
    case text, record, transcribe
}

public enum CaptureState: String, Codable {
    case recording, interrupted, pending, failed, synced
}

public struct JournalSnapshot: Codable, Equatable {
    public let id: String
    public let content: String
    public let title: String?
    public let attachments: [Attachment]?

    public struct Attachment: Codable, Equatable {
        public let id: String
        public let transcriptStatus: String?
        public let transcript: String?
    }
}

public struct Capture: Codable, Identifiable, Equatable {
    public let id: String
    public let attachmentID: String?
    public let createdAt: Date
    public let mode: CaptureMode
    public let text: String
    public var state: CaptureState
    public var lastError: String?
    public var snapshot: JournalSnapshot?

    public init(text: String = "", mode: CaptureMode = .text, now: Date = Date()) {
        id = ULID.make(now: now)
        attachmentID = mode == .text ? nil : ULID.make(now: now)
        createdAt = now
        self.mode = mode
        self.text = text
        state = mode == .text ? .pending : .recording
    }
}

public enum ULID {
    private static let alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    public static func make(now: Date = Date()) -> String {
        var timestamp = UInt64(max(0, now.timeIntervalSince1970 * 1000))
        var prefix = [Character](repeating: "0", count: 10)
        for index in (0..<10).reversed() {
            prefix[index] = alphabet[Int(timestamp & 31)]
            timestamp >>= 5
        }
        // Sixteen independent five-bit draws supply the ULID's 80 random bits.
        return String(prefix + (0..<16).map { _ in alphabet[Int.random(in: 0..<32)] })
    }

    public static func isValid(_ value: String) -> Bool {
        value.count == 26 && value.first! <= "7" && value.allSatisfy { alphabet.contains($0) }
    }
}

public enum CaptureError: LocalizedError {
    case invalidID, emptyText, missingAudio, invalidServer, differentServer, invalidResponse

    public var errorDescription: String? {
        switch self {
        case .invalidID: return "The saved capture has an invalid identifier."
        case .emptyText: return "Write something before saving."
        case .missingAudio: return "The recording is missing or empty. Its saved entry has been kept."
        case .invalidServer: return "Enter an HTTPS server address without a path, credentials, or query."
        case .differentServer: return "This device's captures are bound to a different server."
        case .invalidResponse: return "The server did not confirm this capture. It is still saved on this device."
        }
    }
}

public enum ServerAddress {
    public static func parse(_ value: String) throws -> URL {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { throw CaptureError.invalidServer }
        parts.scheme = "https"
        parts.host = host.lowercased()
        parts.path = ""
        if parts.port == 443 { parts.port = nil }
        guard let url = parts.url else { throw CaptureError.invalidServer }
        return url
    }
}
