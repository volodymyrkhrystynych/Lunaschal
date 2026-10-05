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
    /// `files` and `clips` hold the stored bytes of `capture.files` and
    /// `capture.clips`, in the same order.
    func send(_ capture: Capture, audioURL: URL?, files: [URL], clips: [URL]) async throws
    func fetch(_ id: String) async throws -> JournalSnapshot
}

public extension JournalTransport {
    func send(_ capture: Capture, audioURL: URL?, files: [URL], clips: [URL]) async throws {
        try await send(capture, audioURL: audioURL)
    }
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
    private let uploads: RecordingUploadStore?

    public init(server: URL, token: String?, allowCellular: Bool, uploads: RecordingUploadStore? = nil) throws {
        self.server = try ServerAddress.parse(server.absoluteString)
        self.token = token
        self.uploads = uploads
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
        try await send(capture, audioURL: audioURL, files: [], clips: [])
    }

    public func send(_ capture: Capture, audioURL: URL?, files: [URL], clips: [URL]) async throws {
        guard files.count == capture.files.count else { throw CaptureError.missingFile }
        guard clips.count == capture.clips.count else { throw CaptureError.missingAudio }
        let data: Data
        let response: URLResponse
        if capture.mode == .text {
            var req = request("api/journal", method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode([
                "id": capture.id, "content": capture.text,
                "capturedAt": ISO8601DateFormatter().string(from: capture.createdAt),
                "pendingAttachments": String(capture.links.count + capture.files.count + capture.clips.count)
            ])
            (data, response) = try await session.data(for: req)
        } else {
            guard let audioURL else { throw CaptureError.missingAudio }
            var req = request("api/journal/recordings", method: "POST")
            if let uploads {
                let body = try uploads.prepare(capture, audioURL: audioURL, server: server)
                req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
                (data, response) = try await session.upload(for: req, fromFile: uploads.bodyURL(body))
            } else {
                let body = try RecordingMultipart(capture: capture, audioURL: audioURL)
                defer { try? FileManager.default.removeItem(at: body.url) }
                req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
                (data, response) = try await session.upload(for: req, fromFile: body.url)
            }
        }
        try check(data, response)
        try Self.validateAcknowledgement(data, for: capture)
        // Clips first, one at a time: the server appends each transcript to the
        // entry as it lands, so upload order is the order the words appear in.
        // The recordings route treats an existing entry id as "attach here".
        for (clip, url) in zip(capture.clips, clips) {
            let body = try RecordingMultipart(entryID: capture.id, attachmentID: clip.attachmentID,
                                              capturedAt: clip.createdAt, transcribe: clip.transcribe, audioURL: url)
            defer { try? FileManager.default.removeItem(at: body.url) }
            var req = request("api/journal/recordings", method: "POST")
            req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
            let (clipData, clipResponse) = try await session.upload(for: req, fromFile: body.url)
            try check(clipData, clipResponse)
            try Self.validateClipAcknowledgement(clipData, for: capture, clip: clip)
        }
        // Each link is replay-safe on its own attachment id, so a send that
        // failed partway through simply re-posts the ones already attached.
        for link in capture.links {
            var req = request("api/journal/\(capture.id)/attachments/link", method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode([
                "url": link.url, "attachmentId": link.attachmentID,
                "capturedAt": ISO8601DateFormatter().string(from: capture.createdAt),
            ])
            let (attachmentData, attachmentResponse) = try await session.data(for: req)
            try check(attachmentData, attachmentResponse)
            try Self.validateLinkAcknowledgement(attachmentData, for: capture, link: link)
        }
        // Same replay contract as links: the server recognises a re-POSTed
        // attachment id and answers without storing it twice.
        for (file, url) in zip(capture.files, files) {
            let body = try AttachmentMultipart(capture: capture, file: file, fileURL: url)
            defer { try? FileManager.default.removeItem(at: body.url) }
            var req = request("api/journal/\(capture.id)/attachments", method: "POST")
            req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
            let (fileData, fileResponse) = try await session.upload(for: req, fromFile: body.url)
            try check(fileData, fileResponse)
            try Self.validateFileAcknowledgement(fileData, for: capture, file: file)
        }
    }

    public static func validateClipAcknowledgement(_ data: Data, for capture: Capture, clip: CaptureClip) throws {
        struct Ack: Decodable {
            let id: String
            let attachment: Attachment
            struct Attachment: Decodable { let id: String }
        }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        guard ack.id == capture.id, ack.attachment.id == clip.attachmentID else { throw CaptureError.invalidResponse }
    }

    public static func validateFileAcknowledgement(_ data: Data, for capture: Capture, file: CaptureFile) throws {
        struct Ack: Decodable { let id: String; let entryId: String }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        guard ack.id == file.attachmentID, ack.entryId == capture.id else { throw CaptureError.invalidResponse }
    }

    public static func validateLinkAcknowledgement(_ data: Data, for capture: Capture, link: CaptureLink) throws {
        struct Ack: Decodable { let id: String; let entryId: String }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        guard ack.id == link.attachmentID, ack.entryId == capture.id else { throw CaptureError.invalidResponse }
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

    public func mediaCollections() async throws -> [String] {
        let (data, response) = try await session.data(for: request("api/mobile/capabilities"))
        try check(data, response)
        return try JSONDecoder().decode(MediaCapabilities.self, from: data).supportedCollections
    }

    public func publishDrawing(_ value: DrawingPublication, store: DrawingPublicationStore) async throws -> DrawingReply {
        guard server == value.server else { throw CaptureError.differentServer }
        let files = try store.payloads(value)
        let body = try DrawingMultipart(operation: value.operation, ink: files.ink, preview: files.preview)
        defer { try? FileManager.default.removeItem(at: body.url) }
        var req = request("api/mobile/drawings", method: "POST")
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body.url)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        if ![400, 409, 410, 422].contains(http.statusCode) { try check(data, response) }
        return try JSONDecoder().decode(DrawingReply.self, from: data)
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
        guard let attachmentID = capture.attachmentID else { throw CaptureError.invalidID }
        try self.init(entryID: capture.id, attachmentID: attachmentID, capturedAt: capture.createdAt,
                      transcribe: capture.mode == .transcribe, audioURL: audioURL)
    }

    public init(entryID: String, attachmentID: String, capturedAt: Date, transcribe: Bool, audioURL: URL) throws {
        guard ULID.isValid(entryID), ULID.isValid(attachmentID) else { throw CaptureError.invalidID }
        guard (try audioURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
            throw CaptureError.missingAudio
        }
        (url, boundary) = try writeMultipart(fields: [
            ("id", entryID), ("attachmentId", attachmentID),
            ("capturedAt", ISO8601DateFormatter().string(from: capturedAt)),
            ("transcribe", transcribe ? "true" : "false"),
            ("name", "Recording")
        ], filename: "recording.m4a", contentType: "audio/mp4", source: audioURL)
    }
}

/// A photo or file for `POST /api/journal/<id>/attachments`, streamed the same way.
public struct AttachmentMultipart {
    public let url: URL
    public let boundary: String

    public init(capture: Capture, file: CaptureFile, fileURL: URL) throws {
        guard ULID.isValid(capture.id), ULID.isValid(file.attachmentID) else { throw CaptureError.invalidID }
        guard (try fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
            throw CaptureError.missingFile
        }
        (url, boundary) = try writeMultipart(fields: [
            ("attachmentId", file.attachmentID), ("name", file.name)
        ], filename: file.name, contentType: file.contentType ?? "application/octet-stream", source: fileURL)
    }
}

private func writeMultipart(fields: [(String, String)], filename: String, contentType: String,
                            source: URL) throws -> (URL, String) {
    // A quote or line break in a picked file's name would end the header early.
    func header(_ value: String) -> String {
        String(value.unicodeScalars.filter { $0 != "\"" && $0 != "\r" && $0 != "\n" })
    }
    let boundary = "Lunaschal-\(UUID().uuidString)"
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(boundary).appendingPathExtension("multipart")
    try Data().write(to: url)
    do {
        let output = try FileHandle(forWritingTo: url)
        defer { try? output.close() }
        for (name, value) in fields {
            try output.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
        }
        try output.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(header(filename))\"\r\nContent-Type: \(header(contentType))\r\n\r\n".utf8))
        let input = try FileHandle(forReadingFrom: source)
        defer { try? input.close() }
        while let chunk = try input.read(upToCount: 64 * 1024), !chunk.isEmpty {
            try output.write(contentsOf: chunk)
        }
        try output.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
    } catch {
        try? FileManager.default.removeItem(at: url)
        throw error
    }
    return (url, boundary)
}
