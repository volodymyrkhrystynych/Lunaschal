import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct HTTPFailure: LocalizedError {
    public let status: Int
    public var errorDescription: String? {
        switch status {
        case 401, 403: return "Sign in again to sync. Your captures remain on this device."
        case 413: return "The server refused this recording's size. The original is still on this device."
        default: return "Server returned HTTP \(status). Your capture is still on this device."
        }
    }
    public var retryAutomatically: Bool { status == 408 || status == 429 || status >= 500 }
}

public protocol JournalTransport {
    func send(_ capture: Capture, audioURL: URL?) async throws
    func fetch(_ id: String) async throws -> JournalSnapshot
}

/// Refuse redirects rather than forwarding a session token or upload elsewhere.
private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

public final class JournalAPI: JournalTransport, ReplicaTransport {
    public let server: URL
    private let token: String?
    private let session: URLSession

    public init(server: URL, token: String?, allowCellular: Bool) throws {
        self.server = try ServerAddress.parse(server.absoluteString)
        self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.httpCookieStorage = nil
        config.urlCache = nil
        config.allowsCellularAccess = allowCellular
        #if !os(Linux)
        config.allowsExpensiveNetworkAccess = allowCellular
        #endif
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 15 * 60
        session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }

    deinit { session.invalidateAndCancel() }

    public func cancel() { session.invalidateAndCancel() }

    public func login(password: String, code: String) async throws -> String {
        var req = request("api/auth/login", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["password": password, "code": code])
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        let fields = http.allHeaderFields.reduce(into: [String: String]()) { result, pair in
            result[String(describing: pair.key)] = String(describing: pair.value)
        }
        guard let cookie = HTTPCookie.cookies(withResponseHeaderFields: fields, for: server)
            .first(where: { $0.name == "lunaschal_token" }) else { throw CaptureError.invalidResponse }
        return cookie.value
    }

    public func send(_ capture: Capture, audioURL: URL?) async throws {
        let data: Data
        let response: URLResponse
        if capture.mode == .text {
            var req = request("api/journal", method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode([
                "id": capture.id, "content": capture.text,
                "capturedAt": ISO8601DateFormatter().string(from: capture.createdAt),
                "pendingAttachments": capture.youtubeURL == nil ? "0" : "1"
            ])
            (data, response) = try await session.data(for: req)
        } else {
            guard let audioURL else { throw CaptureError.missingAudio }
            let body = try RecordingMultipart(capture: capture, audioURL: audioURL)
            defer { try? FileManager.default.removeItem(at: body.url) }
            var req = request("api/journal/recordings", method: "POST")
            req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
            (data, response) = try await session.upload(for: req, fromFile: body.url)
        }
        try check(data, response)
        try Self.validateAcknowledgement(data, for: capture)
        if let link = capture.youtubeURL, let attachmentID = capture.linkAttachmentID {
            var req = request("api/journal/\(capture.id)/attachments/link", method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode([
                "url": link, "attachmentId": attachmentID,
                "capturedAt": ISO8601DateFormatter().string(from: capture.createdAt),
            ])
            let (attachmentData, attachmentResponse) = try await session.data(for: req)
            try check(attachmentData, attachmentResponse)
            try Self.validateLinkAcknowledgement(attachmentData, for: capture)
        }
    }

    public static func validateLinkAcknowledgement(_ data: Data, for capture: Capture) throws {
        struct Ack: Decodable { let id: String; let entryId: String }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        guard ack.id == capture.linkAttachmentID, ack.entryId == capture.id else { throw CaptureError.invalidResponse }
    }

    public static func validateAcknowledgement(_ data: Data, for capture: Capture) throws {
        struct Ack: Decodable {
            let id: String
            let attachment: Attachment?
            struct Attachment: Decodable { let id: String }
        }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        guard ack.id == capture.id,
              capture.attachmentID == nil || ack.attachment?.id == capture.attachmentID else {
            throw CaptureError.invalidResponse
        }
    }

    public func fetch(_ id: String) async throws -> JournalSnapshot {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        let (data, response) = try await session.data(for: request("api/journal/\(id)"))
        try check(data, response)
        let snapshot = try JSONDecoder().decode(JournalSnapshot.self, from: data)
        guard snapshot.id == id else { throw CaptureError.invalidResponse }
        return snapshot
    }

    public func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage {
        var req = request("api/mobile/sync")
        var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = cursor.map { [URLQueryItem(name: "cursor", value: $0)] }
            ?? [URLQueryItem(name: "collections", value: collections.joined(separator: ","))]
        req.url = parts.url
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        return try JSONDecoder().decode(SyncPage.self, from: data)
    }

    public func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply {
        var req = request("api/mobile/operations", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(operation)
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        if ![400, 409, 410, 413, 422].contains(http.statusCode) { try check(data, response) }
        return try JSONDecoder().decode(OperationReply.self, from: data)
    }

    public func mediaPage(collection: String, after: String) async throws -> MediaPage {
        guard MediaDescriptor.collections.contains(collection) else { throw MediaError.invalidManifest }
        var req = request("api/mobile/media")
        var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "collection", value: collection), URLQueryItem(name: "after", value: after)]
        req.url = parts.url
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        return try JSONDecoder().decode(MediaPage.self, from: data)
    }

    public func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data {
        try item.validate()
        guard let size = item.size, let digest = item.sha256, offset >= 0,
              count > 0, count <= 1024 * 1024, offset + count <= size else { throw MediaError.invalidRange }
        var req = request("api/mobile/media/\(item.collection)/\(item.id)/file")
        var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = [URLQueryItem(name: "sha256", value: digest)]
        req.url = parts.url
        req.setValue("bytes=\(offset)-\(offset + count - 1)", forHTTPHeaderField: "Range")
        req.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        let (file, response) = try await session.download(for: req)
        defer { try? FileManager.default.removeItem(at: file) }
        try check(Data(), response)
        guard let http = response as? HTTPURLResponse, http.statusCode == 206,
              http.value(forHTTPHeaderField: "Content-Range") == "bytes \(offset)-\(offset + count - 1)/\(size)",
              http.value(forHTTPHeaderField: "ETag") == "\"\(digest)\"",
              try file.resourceValues(forKeys: [.fileSizeKey]).fileSize == Int(count) else { throw MediaError.invalidRange }
        return try Data(contentsOf: file)
    }

    private func request(_ path: String, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: server.appendingPathComponent(path))
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { req.setValue("lunaschal_token=\(token)", forHTTPHeaderField: "Cookie") }
        return req
    }

    private func check(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw HTTPFailure(status: http.statusCode) }
    }
}

/// Stream the audio into a disposable multipart file; memory use is bounded.
public struct RecordingMultipart {
    public let url: URL
    public let boundary: String

    public init(capture: Capture, audioURL: URL) throws {
        guard let attachmentID = capture.attachmentID,
              ULID.isValid(attachmentID), ULID.isValid(capture.id) else { throw CaptureError.invalidID }
        guard (try audioURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
            throw CaptureError.missingAudio
        }
        boundary = "Lunaschal-\(UUID().uuidString)"
        url = FileManager.default.temporaryDirectory.appendingPathComponent(boundary).appendingPathExtension("multipart")
        try Data().write(to: url)
        do {
            let output = try FileHandle(forWritingTo: url)
            defer { try? output.close() }
            let fields = [
                ("id", capture.id), ("attachmentId", attachmentID),
                ("capturedAt", ISO8601DateFormatter().string(from: capture.createdAt)),
                ("transcribe", capture.mode == .transcribe ? "true" : "false"),
                ("name", "Recording")
            ]
            for (name, value) in fields {
                try output.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
            }
            try output.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"recording.m4a\"\r\nContent-Type: audio/mp4\r\n\r\n".utf8))
            let input = try FileHandle(forReadingFrom: audioURL)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 64 * 1024), !chunk.isEmpty {
                try output.write(contentsOf: chunk)
            }
            try output.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw error
        }
    }
}
