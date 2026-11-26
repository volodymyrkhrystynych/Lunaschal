import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Email tab (the server's /api/email routes)

extension JournalAPI {
    public static let emailPage = 50

    public func emails(_ filter: EmailFilter, search: String = "", offset: Int = 0) async throws -> [EmailMessage] {
        var query = filter.query
        query["summary"] = "1"
        query["limit"] = String(Self.emailPage)
        query["offset"] = String(offset)
        if !search.isEmpty { query["query"] = search }
        return try JSONDecoder().decode([EmailMessage].self, from: await get("api/email", query))
    }

    public func email(_ id: String) async throws -> EmailMessage {
        try JSONDecoder().decode(EmailMessage.self, from: await get("api/email/\(try Self.pathID(id))", [:]))
    }

    public func emailAccounts() async throws -> [EmailAccount] {
        try JSONDecoder().decode([EmailAccount].self, from: await get("api/email/accounts", [:]))
    }

    /// The provider's sign-in page, which ends back in the app.
    public func startEmailSignIn(provider: String) async throws -> URL {
        struct Body: Encodable { let provider: String; let client = "ios" }
        struct Reply: Decodable { let url: String }
        let reply = try JSONDecoder().decode(Reply.self, from: await chatJSON("api/email/oauth/start", Body(provider: provider)))
        guard let url = URL(string: reply.url), url.scheme == "https" else { throw CaptureError.invalidResponse }
        return url
    }

    public func connectIMAP(host: String, port: Int, username: String, password: String, address: String) async throws {
        struct Body: Encodable { let host: String; let port: Int; let username: String; let password: String; let emailAddress: String }
        _ = try await chatJSON("api/email/imap/connect", Body(host: host, port: port, username: username,
                                                              password: password, emailAddress: address))
    }

    public func disconnectEmail(provider: String) async throws {
        var req = request("api/email/oauth/disconnect", method: "POST")
        req.url = req.url.flatMap { URL(string: $0.absoluteString + "?provider=\(provider)") }
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }

    public func syncEmail() async throws { try await send("api/email/sync", method: "POST") }

    public func emailImage(_ hash: String) async throws -> (data: Data, type: String) {
        let (data, response) = try await session.data(for: request("api/email/images/\(hash)"))
        try checkChat(data, response)
        return (data, (response as? HTTPURLResponse)?.mimeType ?? "application/octet-stream")
    }

    /// Only the email switch: the coach's and the Code tab's are left alone.
    public func registerEmailPush(token: String, environment: PushEnvironment, on: Bool) async throws {
        struct Body: Encodable { let token: String; let environment: String; let platform = "ios"; let email: Bool }
        _ = try await chatJSON("api/push/devices", Body(token: token, environment: environment.rawValue, email: on))
    }
}
