import Foundation

// The Email tab (backend/routes/email.py): the mirrored mailbox, how each
// account's connection is doing, and where a notification leads. Pure.

public enum EmailCategory: String, Codable, CaseIterable, Identifiable {
    case jobApplication = "job_application", personal, notification, newsletter, other
    public var id: Self { self }
    public var label: String {
        switch self {
        case .jobApplication: return "Jobs"
        case .personal: return "Personal"
        case .notification: return "Notifications"
        case .newsletter: return "Newsletters"
        case .other: return "Other"
        }
    }
}

/// Why the classifier called a message important.
public enum EmailReason: String, Codable {
    case jobNextStep = "job_next_step", personal, security, deadline
    public var label: String {
        switch self {
        case .jobNextStep: return "Job next step"
        case .personal: return "Personal"
        case .security: return "Security"
        case .deadline: return "Deadline"
        }
    }
}

public enum EmailFilter: Hashable {
    case important, all, category(EmailCategory)

    public static var choices: [EmailFilter] { [.all, .important] + EmailCategory.allCases.map(EmailFilter.category) }

    public var label: String {
        switch self {
        case .important: return "Important"
        case .all: return "All"
        case .category(let category): return category.label
        }
    }

    public var query: [String: String] {
        switch self {
        case .important: return ["importance": "high"]
        case .all: return [:]
        case .category(let category): return ["category": category.rawValue]
        }
    }
}

/// A row of `GET /api/email?summary=1`, or with bodies, `GET /api/email/<id>`.
/// Category and reason stay strings so a value this build has not heard of
/// cannot stop the list from decoding.
public struct EmailMessage: Codable, Equatable, Identifiable {
    public let id: String
    public var subject: String?
    public var sender: String?
    public var senderEmail: String?
    public var snippet: String?
    public var receivedAt: String
    public var category: String?
    public var importance: String?
    public var importanceReason: String?
    public var summary: String?
    public var bodyText: String?
    public var bodyHtml: String?

    public init(id: String, subject: String? = nil, sender: String? = nil, senderEmail: String? = nil,
                snippet: String? = nil, receivedAt: String, category: String? = nil, importance: String? = nil,
                importanceReason: String? = nil, summary: String? = nil, bodyText: String? = nil, bodyHtml: String? = nil) {
        self.id = id; self.subject = subject; self.sender = sender; self.senderEmail = senderEmail
        self.snippet = snippet; self.receivedAt = receivedAt; self.category = category; self.importance = importance
        self.importanceReason = importanceReason; self.summary = summary; self.bodyText = bodyText; self.bodyHtml = bodyHtml
    }

    public var from: String { [sender, senderEmail].compactMap { $0 }.first { !$0.isEmpty } ?? "Unknown sender" }
    public var title: String { subject.flatMap { $0.isEmpty ? nil : $0 } ?? "(no subject)" }
    public var isImportant: Bool { importance == "high" }
    public var reason: EmailReason? { importanceReason.flatMap(EmailReason.init(rawValue:)) }
    /// The classifier's line when there is one, else the mail's own preview.
    public var preview: String { [summary, snippet].compactMap { $0 }.first { !$0.isEmpty } ?? "" }
    public var date: Date? { EmailDate.parse(receivedAt) }
}

public enum EmailDate {
    public static func parse(_ text: String?) -> Date? {
        guard let text else { return nil }
        let plain = ISO8601DateFormatter()
        if let date = plain.date(from: text) { return date }
        plain.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return plain.date(from: text)
    }
}

/// The inbox in sections, newest first, by the day the mail arrived.
public enum EmailDays {
    public static func group(_ messages: [EmailMessage], now: Date = Date(),
                             calendar: Calendar = .current) -> [(title: String, messages: [EmailMessage])] {
        var sections: [(day: Date?, messages: [EmailMessage])] = []
        for message in messages {
            let day = message.date.map(calendar.startOfDay(for:))
            if let last = sections.last, last.day == day { sections[sections.count - 1].messages.append(message) }
            else { sections.append((day, [message])) }
        }
        return sections.map { (title($0.day, now: now, calendar: calendar), $0.messages) }
    }

    static func title(_ day: Date?, now: Date, calendar: Calendar) -> String {
        guard let day else { return "Undated" }
        let days = calendar.dateComponents([.day], from: day, to: calendar.startOfDay(for: now)).day ?? 0
        if days == 0 { return "Today" }
        if days == 1 { return "Yesterday" }
        let format = DateFormatter()
        format.calendar = calendar
        format.timeZone = calendar.timeZone
        format.setLocalizedDateFormatFromTemplate(
            calendar.component(.year, from: day) == calendar.component(.year, from: now) ? "EEEEMMMd" : "yMMMd")
        return format.string(from: day)
    }
}

/// `GET /api/email/accounts`.
public struct EmailAccount: Codable, Equatable, Identifiable {
    public let provider: String
    public var emailAddress: String
    public var connected: Bool
    public var syncEnabled: Bool
    public var needsReconnect: Bool
    public var lastSyncedAt: Int?
    public var lastSyncError: String?
    public var connectedAt: String?
    public var refreshExpiresAt: String?

    public init(provider: String, emailAddress: String, connected: Bool, syncEnabled: Bool = true,
                needsReconnect: Bool = false, lastSyncedAt: Int? = nil, lastSyncError: String? = nil,
                connectedAt: String? = nil, refreshExpiresAt: String? = nil) {
        self.provider = provider; self.emailAddress = emailAddress; self.connected = connected
        self.syncEnabled = syncEnabled; self.needsReconnect = needsReconnect; self.lastSyncedAt = lastSyncedAt
        self.lastSyncError = lastSyncError; self.connectedAt = connectedAt; self.refreshExpiresAt = refreshExpiresAt
    }

    public var id: String { provider }
    public var label: String { Self.label(provider) }
    /// Signs in on the provider's page; IMAP takes a password instead.
    public var usesOAuth: Bool { provider != "imap" }

    public static func label(_ provider: String) -> String {
        switch provider {
        case "gmail": return "Gmail"
        case "outlook": return "Outlook"
        default: return "Email"
        }
    }

    public func health(now: Date = Date()) -> EmailHealth {
        if needsReconnect { return .needsReconnect }
        if !connected { return .disconnected }
        guard let expiry = EmailDate.parse(refreshExpiresAt) else { return .connected }
        // Past its stated end but not yet refused: the next sync finds out.
        return .expires(days: max(0, Int((expiry.timeIntervalSince(now) / 86_400).rounded(.down))))
    }
}

public enum EmailHealth: Equatable {
    case connected, needsReconnect, disconnected
    case expires(days: Int)

    public var label: String {
        switch self {
        case .connected: return "Connected"
        case .needsReconnect: return "Sign-in expired"
        case .disconnected: return "Disconnected"
        case .expires(0): return "Connected, sign-in ends within a day"
        case .expires(1): return "Connected, sign-in ends in 1 day"
        case .expires(let days): return "Connected, sign-in ends in \(days) days"
        }
    }
}

/// How the browser sheet's sign-in ended, from the server's redirect.
public enum EmailSignIn: Equatable {
    case connected
    case failed(String)

    public static func parse(_ url: URL) -> EmailSignIn? {
        guard url.scheme == "lunaschal", url.host == "email" else { return nil }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        if items.contains(where: { $0.name == "connected" && $0.value == "1" }) { return .connected }
        switch items.first(where: { $0.name == "error" })?.value {
        case nil: return nil
        case "declined": return .failed("You didn't allow access, so nothing changed.")
        case "other_account": return .failed("That's a different address from the one connected. Choose the same account, or disconnect it first.")
        default: return .failed("The sign-in didn't finish. Try again.")
        }
    }
}

/// Where a notification, a chat card or a link leads in the Email tab.
public enum EmailRoute: Equatable {
    case inbox, reconnect
    case message(String)

    public init?(userInfo: [AnyHashable: Any]) {
        guard userInfo["kind"] as? String == "email" else { return nil }
        if userInfo["route"] as? String == "reconnect" { self = .reconnect }
        else if let id = userInfo["emailId"] as? String, ULID.isValid(id) { self = .message(id) }
        else { self = .inbox }
    }

    /// `lunaschal://email`, `…?reconnect=1`, `…?id=<ulid>`.
    public init?(url: URL) {
        guard url.scheme == "lunaschal", url.host == "email" else { return nil }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        if items.contains(where: { $0.name == "reconnect" }) { self = .reconnect }
        else if let id = items.first(where: { $0.name == "id" })?.value, ULID.isValid(id) { self = .message(id) }
        else { self = .inbox }
    }

    public var url: URL {
        switch self {
        case .inbox: return URL(string: "lunaschal://email")!
        case .reconnect: return URL(string: "lunaschal://email?reconnect=1")!
        case .message(let id): return URL(string: "lunaschal://email?id=\(id)")!
        }
    }
}

/// A chat message the server posted about email: its `metadata.email`.
public enum EmailNotice: Equatable {
    public struct Item: Equatable, Identifiable {
        public let id: String
        public let sender: String
        public let subject: String
        public let reason: EmailReason?
    }
    case important([Item])
    case disconnected(provider: String, address: String)

    public static func parse(metadata: String?) -> EmailNotice? {
        guard let email = JSONValue.parse(metadata)?["email"] else { return nil }
        switch email["kind"]?.text {
        case "important":
            let items = (email["items"]?.array ?? []).compactMap { item -> Item? in
                guard let id = item["id"]?.text, ULID.isValid(id) else { return nil }
                return Item(id: id, sender: item["sender"]?.text ?? "", subject: item["subject"]?.text ?? "",
                            reason: EmailReason(rawValue: item["reason"]?.text ?? ""))
            }
            return items.isEmpty ? nil : .important(items)
        case "disconnected":
            return .disconnected(provider: email["provider"]?.text ?? "", address: email["address"]?.text ?? "")
        default: return nil
        }
    }
}

/// The document a message is drawn in. The markup was sanitized by the server;
/// this adds the second boundary: nothing loads from the network, and images
/// appear only once asked for, from the server's own copies.
public enum EmailHTML {
    /// The web view hands these requests to the app, which holds the session.
    public static let imageScheme = "lunaschal-image"
    private static let source = #"data-src=(["'])/api/email/images/([a-f0-9]+)\1"#

    public static func document(_ html: String, images: Bool) -> String {
        let body = images ? html.replacingOccurrences(
            of: source, with: "src=$1\(imageScheme)://email/$2$1", options: [.regularExpression, .caseInsensitive]) : html
        return """
        <!doctype html><html><head><meta charset="utf-8">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src \(imageScheme):; style-src 'unsafe-inline'">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>:root{color-scheme:light}html,body{margin:0;background:#fff;color:#161616}
        body{padding:12px;font:-apple-system-body;overflow-wrap:anywhere}
        img{max-width:100%!important;height:auto!important}table{max-width:100%!important}
        img:not([src]){display:none}</style></head><body>\(body)</body></html>
        """
    }

    public static func imageCount(_ html: String) -> Int {
        guard let regex = try? NSRegularExpression(pattern: source, options: .caseInsensitive) else { return 0 }
        return regex.numberOfMatches(in: html, range: NSRange(html.startIndex..., in: html))
    }

    /// The server's image key, from a request the web view made.
    public static func imageHash(_ url: URL) -> String? {
        guard url.scheme == imageScheme, url.host == "email" else { return nil }
        let hash = url.lastPathComponent
        return !hash.isEmpty && hash.allSatisfy { "0123456789abcdef".contains($0) } ? hash : nil
    }
}
