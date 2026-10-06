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
    /// The weather the server stored on a meal, or nil before it has any.
    func foodWeather(_ id: String) async throws -> String?
}

public extension JournalTransport {
    func foodWeather(_ id: String) async throws -> String? { nil }

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
    let session: URLSession
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
        if capture.kind == .food { return try await sendFood(capture, files: files, clips: clips) }
        let data: Data
        let response: URLResponse
        if capture.mode == .text {
            var req = request("api/journal", method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            // Fic commentary goes up as raw_content, as the desktop reader's
            // does: that is what queues the journal's polish pass, so a
            // misheard character name is fixed like any other entry's.
            var body = [
                "id": capture.id, capture.ficID == nil ? "content" : "raw_content": capture.text,
                "capturedAt": ISO8601DateFormatter().string(from: capture.createdAt),
                "pendingAttachments": String(capture.links.count + capture.files.count + capture.clips.count)
            ]
            if let latitude = capture.latitude, let longitude = capture.longitude {
                body["latitude"] = String(latitude)
                body["longitude"] = String(longitude)
            }
            req.httpBody = try JSONEncoder().encode(body)
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
        // A recording carries its fic in the upload and the server links it
        // there; typed commentary needs the link as a second, idempotent call.
        if capture.mode == .text, let ficID = capture.ficID {
            var link = ["journalEntryId": capture.id]
            if let chapterID = capture.chapterID { link["chapterId"] = chapterID }
            do { _ = try await postJSON("api/fanfic/\(ficID)/journal-link", link) }
            // The fic or chapter was deleted since: the entry itself landed,
            // and failing the capture over its link would only strand it.
            catch let failure as HTTPFailure where failure.status == 404 {}
        }
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

    /// The meal and its photos go up in one request, each photo under the id
    /// minted for it here, so a replay re-sends them and the server skips the
    /// ones it already holds. Clips follow one at a time, like the journal's.
    private func sendFood(_ capture: Capture, files: [URL], clips: [URL]) async throws {
        let body = try FoodMultipart(capture: capture, files: files)
        defer { try? FileManager.default.removeItem(at: body.url) }
        var req = request("api/food", method: "POST")
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body.url)
        try check(data, response)
        try Self.validateFoodAcknowledgement(data, for: capture)
        for (index, (clip, url)) in zip(capture.clips, clips).enumerated() {
            let body = try FoodRecordingMultipart(capture: capture, clip: clip, position: files.count + index, audioURL: url)
            defer { try? FileManager.default.removeItem(at: body.url) }
            var req = request("api/food/recordings", method: "POST")
            req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
            let (clipData, clipResponse) = try await session.upload(for: req, fromFile: body.url)
            try check(clipData, clipResponse)
            try Self.validateFoodClipAcknowledgement(clipData, for: capture, clip: clip)
        }
    }

    /// The food log drops a file it cannot store without failing the request,
    /// so every photo sent must come back by id before the meal counts as saved.
    public static func validateFoodAcknowledgement(_ data: Data, for capture: Capture) throws {
        struct Ack: Decodable {
            let id: String
            let media: [Media]
            struct Media: Decodable { let id: String }
        }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        let stored = Set(ack.media.map(\.id))
        guard ack.id == capture.id, capture.files.allSatisfy({ stored.contains($0.attachmentID) }) else {
            throw CaptureError.invalidResponse
        }
    }

    public static func validateFoodClipAcknowledgement(_ data: Data, for capture: Capture, clip: CaptureClip) throws {
        struct Ack: Decodable {
            let id: String
            let media: Media
            struct Media: Decodable { let id: String }
        }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        guard ack.id == capture.id, ack.media.id == clip.attachmentID else { throw CaptureError.invalidResponse }
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

    public func foodWeather(_ id: String) async throws -> String? {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        struct Meal: Decodable { let id: String; let weather: String? }
        let (data, response) = try await session.data(for: request("api/food/\(id)"))
        try check(data, response)
        let meal = try JSONDecoder().decode(Meal.self, from: data)
        guard meal.id == id else { throw CaptureError.invalidResponse }
        return meal.weather
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

    /// The replica collections this server can sync.
    public func syncCollections() async throws -> [String] {
        struct Capabilities: Decodable { let collections: [String] }
        let (data, response) = try await session.data(for: request("api/mobile/capabilities"))
        try check(data, response)
        return try JSONDecoder().decode(Capabilities.self, from: data).collections
    }

    /// A day's wake and sleep. Derived on the server from what was done that
    /// day, so it's fetched rather than replicated.
    public func sleep(day: String) async throws -> SleepDay {
        let (data, response) = try await session.data(for: request("api/calendar/sleep/\(try Self.calendarPath(day))"))
        try check(data, response)
        return try JSONDecoder().decode(SleepDay.self, from: data)
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

    func request(_ path: String, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: server.appendingPathComponent(path))
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token { req.setValue("lunaschal_token=\(token)", forHTTPHeaderField: "Cookie") }
        return req
    }

    func check(_ data: Data, _ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else { throw HTTPFailure(status: http.statusCode) }
    }
}

// MARK: Daily (the Lifestyle routes)

extension JournalAPI: DailyTransport {
    public func sendDaily(_ log: DailyLog, image: URL?) async throws {
        guard ULID.isValid(log.id) else { throw CaptureError.invalidID }
        let data: Data
        switch log.kind {
        case .weight:
            guard let weight = log.weight else { throw DailyError.invalidWeight }
            struct Body: Encodable { let weight: Double; let date: String }
            data = try await postJSON("api/lifestyle/weight", Body(weight: weight, date: log.day))
        case .calories:
            guard let calories = log.calories, let text = log.description else { throw DailyError.invalidCalories }
            // An Int field, so the count goes over the wire as 600 and never 600.0.
            // capturedAt: when it was logged, not when it synced, as the server
            // reads the row as the user being awake.
            struct Body: Encodable { let id: String; let description: String; let calories: Int; let date: String; let capturedAt: String }
            data = try await postJSON("api/lifestyle/calories",
                                      Body(id: log.id, description: text, calories: calories, date: log.day,
                                           capturedAt: ISO8601DateFormatter().string(from: log.createdAt)))
        case .selfie:
            guard let image else { throw DailyError.missingImage }
            let body = try SelfieMultipart(log: log, image: image)
            defer { try? FileManager.default.removeItem(at: body.url) }
            var req = request("api/lifestyle/selfies", method: "POST")
            req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
            let (reply, response) = try await session.upload(for: req, fromFile: body.url)
            try check(reply, response)
            data = reply
        }
        try Self.validateDailyAcknowledgement(data, for: log)
    }

    /// Weight and selfie answer with the day they were filed under, a calorie
    /// entry with the id it was sent with. Either must match before it counts.
    public static func validateDailyAcknowledgement(_ data: Data, for log: DailyLog) throws {
        struct Ack: Decodable { let id: String; let date: String }
        let ack = try JSONDecoder().decode(Ack.self, from: data)
        let matches = log.kind == .calories ? ack.id == log.id && ack.date == log.day : ack.date == log.day
        guard matches else { throw CaptureError.invalidResponse }
    }

    public func dailyStatus(day: String) async throws -> DailyStatus {
        struct Weight: Decodable { let date: String; let weight: Double }
        struct Selfie: Decodable { let id: String; let date: String }
        struct Calories: Decodable {
            let date: String
            let entries: [Entry]
            struct Entry: Decodable { let id: String; let description: String; let calories: Int }
        }
        let weights = try JSONDecoder().decode([Weight].self, from: await get("api/lifestyle/weight", ["start": day, "end": day]))
        let selfies = try JSONDecoder().decode([Selfie].self, from: await get("api/lifestyle/selfies", ["limit": "1"]))
        let calories = try JSONDecoder().decode(Calories.self, from: await get("api/lifestyle/calories", ["date": day]))
        guard calories.date == day else { throw CaptureError.invalidResponse }
        return DailyStatus(
            day: day,
            weight: weights.last { $0.date == day }?.weight,
            selfie: selfies.first { $0.date == day }.map { DailyStatus.Selfie(id: $0.id) },
            entries: calories.entries.map { DailyStatus.Entry(id: $0.id, description: $0.description, calories: $0.calories) })
    }

    /// Today's weather where the server last knew the user to be.
    public func weatherToday() async throws -> WeatherDay {
        try WeatherDay.decode(await get("api/lifestyle/weather/today", [:]))
    }

    /// Tells the server where the phone is, which also resyncs today's weather
    /// for that place; answers like `weatherToday`.
    public func updateWeatherLocation(latitude: Double, longitude: Double) async throws -> WeatherDay {
        struct Body: Encodable { let latitude: Double; let longitude: Double }
        return try WeatherDay.decode(await postJSON("api/lifestyle/weather/location",
                                                    Body(latitude: latitude, longitude: longitude)))
    }

    /// The server's small, orientation-corrected copy of a selfie.
    public func selfieThumbnail(_ id: String) async throws -> Data {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return try await get("api/lifestyle/selfies/\(id)/image", ["thumbnail": "1"])
    }

    func get(_ path: String, _ query: [String: String]) async throws -> Data {
        var req = request(path)
        var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
        parts.queryItems = query.isEmpty ? nil : query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        req.url = parts.url
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        return data
    }

    func postJSON<Body: Encodable>(_ path: String, _ body: Body) async throws -> Data {
        var req = request(path, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        return data
    }
}

// MARK: Workouts (the desktop's quick-entry route)

extension JournalAPI: WorkoutTransport {
    public func sendWorkout(_ item: WorkoutLog) async throws {
        guard ULID.isValid(item.id) else { throw CaptureError.invalidID }
        struct Body: Encodable { let id: String; let text: String; let exercise: String?; let capturedAt: String }
        let data = try await postJSON("api/lifestyle/workouts/entries", Body(
            id: item.id, text: item.text, exercise: item.exercise,
            capturedAt: ISO8601DateFormatter().string(from: item.createdAt)))
        try Self.validateWorkoutAcknowledgement(data, for: item)
    }

    /// The reply is the workout the entry went into. A set is stored under the
    /// id the phone gave it, an outdoor activity is its own session; one of the
    /// two must be there before the entry counts as uploaded.
    public static func validateWorkoutAcknowledgement(_ data: Data, for item: WorkoutLog) throws {
        struct Reply: Decodable {
            let session: Session
            struct Session: Decodable { let id: String; let exercises: [Exercise] }
            struct Exercise: Decodable { let sets: [Set] }
            struct Set: Decodable { let id: String }
        }
        let session = try JSONDecoder().decode(Reply.self, from: data).session
        let stored = session.id == item.id || session.exercises.contains { $0.sets.contains { $0.id == item.id } }
        guard stored else { throw CaptureError.invalidResponse }
    }

    public func recentExercises() async throws -> [RecentExercise] {
        try JSONDecoder().decode([RecentExercise].self, from: await get("api/lifestyle/workouts/recent-exercises", [:]))
    }

    public func recentWorkouts(limit: Int = 4) async throws -> [WorkoutSession] {
        try JSONDecoder().decode([WorkoutSession].self, from: await get("api/lifestyle/workouts", ["limit": String(limit)]))
    }

    /// The desktop's "Rate / location". Either may be left out.
    public func updateWorkout(_ id: String, location: String?, intensity: Int?) async throws {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        struct Body: Encodable { let locationType: String?; let intensityRating: Int? }
        var req = request("api/lifestyle/workouts/\(id)", method: "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(Body(locationType: location, intensityRating: intensity))
        let (data, response) = try await session.data(for: req)
        try check(data, response)
    }
}

// MARK: Reading activity (the fanfic reader's spans and last-read chapter)

extension JournalAPI: FicActivityTransport {
    public func sendFicActivity(_ item: FicActivity) async throws {
        switch item {
        case .span(let span):
            guard ULID.isValid(span.ficId), ULID.isValid(span.id) else { throw CaptureError.invalidID }
            var req = request("api/fanfic/\(span.ficId)/reading-spans/\(span.id)", method: "PUT")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(span)
            let (data, response) = try await session.data(for: req)
            try check(data, response)
        case .progress(let ficId, let chapterId):
            guard ULID.isValid(ficId) else { throw CaptureError.invalidID }
            _ = try await postJSON("api/fanfic/\(ficId)/progress", ["chapterId": chapterId])
        }
    }
}

/// `POST /api/lifestyle/selfies`: the photo and the 4am day it was taken on.
public struct SelfieMultipart {
    public let url: URL
    public let boundary: String

    public init(log: DailyLog, image: URL) throws {
        guard log.kind == .selfie, ULID.isValid(log.id) else { throw CaptureError.invalidID }
        guard (try image.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else { throw DailyError.missingImage }
        (url, boundary) = try writeMultipart(fields: [("date", log.day)], files: [
            MultipartFile(field: "image", filename: "selfie.jpg", contentType: "image/jpeg", source: image)
        ])
    }
}

/// Stream the audio into a disposable multipart file; memory use is bounded.
public struct RecordingMultipart {
    public let url: URL
    public let boundary: String

    public init(capture: Capture, audioURL: URL) throws {
        guard let attachmentID = capture.attachmentID else { throw CaptureError.invalidID }
        try self.init(entryID: capture.id, attachmentID: attachmentID, capturedAt: capture.createdAt,
                      transcribe: capture.mode == .transcribe, audioURL: audioURL,
                      ficID: capture.ficID, chapterID: capture.chapterID)
    }

    /// `ficId`/`chapterId` make the recording the reader's commentary: the
    /// server links the entry to that chapter, and re-links it on a replay.
    public init(entryID: String, attachmentID: String, capturedAt: Date, transcribe: Bool, audioURL: URL,
                ficID: String? = nil, chapterID: String? = nil) throws {
        guard ULID.isValid(entryID), ULID.isValid(attachmentID),
              ficID.map(ULID.isValid) ?? true, chapterID.map(ULID.isValid) ?? true else { throw CaptureError.invalidID }
        guard (try audioURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
            throw CaptureError.missingAudio
        }
        var fields = [
            ("id", entryID), ("attachmentId", attachmentID),
            ("capturedAt", ISO8601DateFormatter().string(from: capturedAt)),
            ("transcribe", transcribe ? "true" : "false"),
            ("name", "Recording")
        ]
        if let ficID {
            fields.append(("ficId", ficID))
            if let chapterID { fields.append(("chapterId", chapterID)) }
        }
        (url, boundary) = try writeMultipart(fields: fields, filename: "recording.m4a",
                                             contentType: "audio/mp4", source: audioURL)
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

/// `POST /api/food`: the meal's text and capture time, how many clips will
/// follow, and every photo with the id it was given on this device.
public struct FoodMultipart {
    public let url: URL
    public let boundary: String

    public init(capture: Capture, files: [URL]) throws {
        guard capture.kind == .food, ULID.isValid(capture.id), files.count == capture.files.count,
              capture.files.allSatisfy({ ULID.isValid($0.attachmentID) }) else { throw CaptureError.invalidID }
        var parts: [MultipartFile] = []
        for (file, source) in zip(capture.files, files) {
            guard (try source.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
                throw CaptureError.missingFile
            }
            guard file.isFoodMedia, let type = file.contentType else { throw CaptureError.notFoodMedia }
            parts.append(MultipartFile(field: "media", filename: file.name, contentType: type, source: source))
        }
        let ids = String(decoding: try JSONEncoder().encode(capture.files.map(\.attachmentID)), as: UTF8.self)
        var fields = [
            ("id", capture.id), ("text", capture.text),
            ("capturedAt", ISO8601DateFormatter().string(from: capture.createdAt)),
            ("pendingClips", String(capture.clips.count)), ("mediaIds", ids)
        ]
        if let latitude = capture.latitude, let longitude = capture.longitude {
            fields += [("latitude", String(latitude)), ("longitude", String(longitude))]
        }
        (url, boundary) = try writeMultipart(fields: fields, files: parts)
    }
}

/// `POST /api/food/recordings`: one clip, under the meal and the id it was
/// recorded with. The food log transcribes every clip it is given.
public struct FoodRecordingMultipart {
    public let url: URL
    public let boundary: String

    public init(capture: Capture, clip: CaptureClip, position: Int, audioURL: URL) throws {
        guard ULID.isValid(capture.id), ULID.isValid(clip.attachmentID) else { throw CaptureError.invalidID }
        guard (try audioURL.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
            throw CaptureError.missingAudio
        }
        (url, boundary) = try writeMultipart(fields: [
            ("id", capture.id), ("mediaId", clip.attachmentID), ("position", String(position))
        ], files: [MultipartFile(field: "audio", filename: "recording.m4a", contentType: "audio/mp4", source: audioURL)])
    }
}

struct MultipartFile {
    let field: String
    let filename: String
    let contentType: String
    let source: URL
}

private func writeMultipart(fields: [(String, String)], filename: String, contentType: String,
                            source: URL) throws -> (URL, String) {
    try writeMultipart(fields: fields, files: [MultipartFile(field: "file", filename: filename,
                                                             contentType: contentType, source: source)])
}

func writeMultipart(fields: [(String, String)], files: [MultipartFile]) throws -> (URL, String) {
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
        for file in files {
            try output.write(contentsOf: Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(file.field)\"; filename=\"\(header(file.filename))\"\r\nContent-Type: \(header(file.contentType))\r\n\r\n".utf8))
            let input = try FileHandle(forReadingFrom: file.source)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 64 * 1024), !chunk.isEmpty {
                try output.write(contentsOf: chunk)
            }
            try output.write(contentsOf: Data("\r\n".utf8))
        }
        try output.write(contentsOf: Data("--\(boundary)--\r\n".utf8))
    } catch {
        try? FileManager.default.removeItem(at: url)
        throw error
    }
    return (url, boundary)
}
