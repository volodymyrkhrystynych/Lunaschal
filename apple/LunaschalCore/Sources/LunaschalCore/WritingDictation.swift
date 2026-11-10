import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Speaking into a chapter. A clip is kept here with the place it belongs until
// its words are in that document's draft, so a clip spoken offline, or into a
// server that is down, waits rather than disappears, and the audio goes only
// once the text is safe.

public struct WritingClip: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let key: WritingDocumentKey
    public let projectId: String
    public var anchor: DictationAnchor
    public let createdAt: Date

    public init(id: String = ULID.make(), key: WritingDocumentKey, projectId: String, anchor: DictationAnchor,
                createdAt: Date = Date()) {
        self.id = id; self.key = key; self.projectId = projectId; self.anchor = anchor; self.createdAt = createdAt
    }
}

public final class WritingDictationStore {
    public let root: URL
    private let lock = NSRecursiveLock()

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent("\(id).json") }
    public func audio(_ clip: WritingClip) -> URL { root.appendingPathComponent("\(clip.id).m4a") }

    /// Takes the recorded file over, moving it out of wherever it was recorded.
    @discardableResult
    public func add(_ audioURL: URL, key: WritingDocumentKey, projectId: String, anchor: DictationAnchor,
                    at date: Date = Date()) throws -> WritingClip {
        lock.lock(); defer { lock.unlock() }
        let clip = WritingClip(key: key, projectId: projectId, anchor: anchor, createdAt: date)
        try FileManager.default.moveItem(at: audioURL, to: audio(clip))
        try JSONEncoder().encode(clip).write(to: manifest(clip.id), options: .atomic)
        return clip
    }

    /// Oldest first, which is the order they were spoken.
    public func clips() -> [WritingClip] {
        lock.lock(); defer { lock.unlock() }
        guard let names = try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
        else { return [] }
        return names.filter { $0.pathExtension == "json" }
            .compactMap { try? JSONDecoder().decode(WritingClip.self, from: Data(contentsOf: $0)) }
            .sorted { ($0.createdAt, $0.id) < ($1.createdAt, $1.id) }
    }

    public func clips(for key: WritingDocumentKey) -> [WritingClip] { clips().filter { $0.key == key } }

    /// Keeps every waiting clip's place as the document is edited.
    public func shift(_ key: WritingDocumentKey, location: Int, removed: Int, inserted: Int) throws {
        lock.lock(); defer { lock.unlock() }
        for var clip in clips(for: key) {
            let before = clip.anchor
            clip.anchor.shift(location: location, removed: removed, inserted: inserted)
            if clip.anchor != before {
                try JSONEncoder().encode(clip).write(to: manifest(clip.id), options: .atomic)
            }
        }
    }

    public func remove(_ clip: WritingClip) {
        lock.lock(); defer { lock.unlock() }
        try? FileManager.default.removeItem(at: audio(clip))
        try? FileManager.default.removeItem(at: manifest(clip.id))
    }
}

/// The server could not make words of this clip, and never will.
public struct WritingDictationRefusal: LocalizedError, Equatable {
    public let message: String
    public init(message: String) { self.message = message }
    public var errorDescription: String? { message }
}

public protocol WritingTranscriber {
    func transcribeWriting(audioURL: URL) async throws -> String
}

public final class WritingDictationSync {
    private let clips: WritingDictationStore
    private let gate = NSLock()
    private var running = false

    public init(clips: WritingDictationStore) { self.clips = clips }

    /// Transcribes every waiting clip and hands the words to `deliver`, which
    /// must have them on disk before it returns: only then is the audio deleted.
    /// A clip that fails stays, except one the server calls unusable. A network
    /// failure stops the pass. Returns what could not be used.
    public func run(using transcriber: WritingTranscriber,
                    deliver: @MainActor (WritingClip, String) throws -> Void) async throws -> [String] {
        guard begin() else { return [] }
        defer { end() }
        var refused: [String] = []
        for clip in clips.clips() {
            try Task.checkCancellation()
            do {
                let text = try await transcriber.transcribeWriting(audioURL: clips.audio(clip))
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                // Read again: the anchor may have moved during the await.
                let latest = clips.clips(for: clip.key).first { $0.id == clip.id } ?? clip
                if !text.isEmpty { try await deliver(latest, text) }
                clips.remove(clip)
            } catch let refusal as WritingDictationRefusal {
                refused.append("A clip couldn't be transcribed: \(refusal.message)")
                clips.remove(clip)
            }
        }
        return refused
    }

    private func begin() -> Bool {
        gate.lock(); defer { gate.unlock() }
        if running { return false }
        running = true
        return true
    }

    private func end() {
        gate.lock(); defer { gate.unlock() }
        running = false
    }
}

extension JournalAPI: WritingTranscriber {
    /// Sends no `source`, so the words land in the book and not also in the
    /// Transcriptions log.
    public func transcribeWriting(audioURL: URL) async throws -> String {
        struct Reply: Decodable { let text: String }
        let (body, boundary) = try writeMultipart(fields: [], files: [
            MultipartFile(field: "audio", filename: "recording.m4a", contentType: "audio/mp4", source: audioURL)
        ])
        defer { try? FileManager.default.removeItem(at: body) }
        var req = request("api/transcribe", method: "POST")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.upload(for: req, fromFile: body)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        // 400 is the route's word for a clip too short or empty to transcribe.
        if http.statusCode == 400 {
            throw WritingDictationRefusal(message: Self.errorMessage(data) ?? "The clip was empty.")
        }
        try checkChat(data, response)
        return try JSONDecoder().decode(Reply.self, from: data).text
    }
}
