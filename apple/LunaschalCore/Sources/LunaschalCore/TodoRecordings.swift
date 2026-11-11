import Foundation

/// A to-do said into the mic beside "Add to-do". Stopping the recording is the
/// last thing the user waits for: the clip is kept here until
/// `POST /api/tasks/todos/recordings` has it, and the server transcribes it and
/// puts the to-do on the list itself. So one said offline arrives once the
/// phone is back in reach, and a server that fails on it still has the clip.
public struct TodoRecording: Codable, Equatable, Identifiable {
    public enum State: String, Codable { case pending, failed }

    /// The recording's id, and the id of the to-do it becomes: a replayed
    /// upload is a no-op on the server.
    public let id: String
    public let createdAt: Date
    public var state: State
    public var lastError: String?
}

public final class TodoRecordingStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [TodoRecording] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(TodoRecording.self, from: Data(contentsOf: $0)) }
            .sorted { $0.createdAt < $1.createdAt }
    }

    /// Takes the finished clip (moved, not copied) and queues it. An empty one
    /// has nothing to say and is deleted, returning nil.
    @discardableResult
    public func add(clip: URL, now: Date = Date()) throws -> TodoRecording? {
        guard size(of: clip) > 0 else { try? fm.removeItem(at: clip); return nil }
        let item = TodoRecording(id: ULID.make(now: now), createdAt: now, state: .pending, lastError: nil)
        // Audio before the manifest: a manifest is what gets sent, so it must
        // never exist without its audio beside it.
        try fm.moveItem(at: clip, to: audioURL(item))
        do { try save(item) } catch { try? fm.removeItem(at: audioURL(item)); throw error }
        return item
    }

    public func audioURL(_ item: TodoRecording) -> URL { root.appendingPathComponent(item.id).appendingPathExtension("m4a") }

    public func save(_ item: TodoRecording) throws {
        guard ULID.isValid(item.id) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(item).write(to: manifest(item.id), options: .atomic)
    }

    public func remove(_ item: TodoRecording) throws {
        for url in [manifest(item.id), audioURL(item)] where fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }

    private func size(of url: URL) -> Int {
        (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
    }
}

public protocol TodoRecordingTransport {
    func sendTodoRecording(_ item: TodoRecording, audioURL: URL) async throws
}

/// Uploads queued to-do clips oldest first. The Chat voice outbox's rules: a
/// clip the server refuses is marked failed and the rest carry on; anything
/// else (offline, signed out) stops the pass and keeps everything.
@MainActor
public final class TodoRecordingSync {
    private let store: TodoRecordingStore

    public init(store: TodoRecordingStore) { self.store = store }

    /// Whether any clip reached the server.
    @discardableResult
    public func run(using transport: TodoRecordingTransport) async throws -> Bool {
        var sent = false
        for var item in try store.list() where item.state == .pending {
            try Task.checkCancellation()
            do {
                try await transport.sendTodoRecording(item, audioURL: store.audioURL(item))
                try store.remove(item)
                sent = true
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
                item.lastError = error.localizedDescription
                if let http = error as? HTTPFailure, ![401, 403].contains(http.status), !http.retryAutomatically {
                    item.state = .failed
                    try store.save(item)
                    continue
                }
                try store.save(item)
                throw error
            }
        }
        return sent
    }
}

/// A spoken to-do the server has but that isn't on the list yet
/// (`GET /api/tasks/todos/recordings`): still being transcribed, or failed.
public struct ServerTodoRecording: Decodable, Equatable, Identifiable {
    public let id: String
    public let status: String
    public let error: String?
    public let transcript: String?

    public var failed: Bool { status == "error" }

    public init(id: String, status: String, error: String? = nil, transcript: String? = nil) {
        self.id = id
        self.status = status
        self.error = error
        self.transcript = transcript
    }
}
