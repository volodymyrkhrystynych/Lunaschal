import Foundation

/// Reading spans: a chapter open and being scrolled is the reader's evidence
/// that the user was reading, and when. One span is one continuous stretch of
/// scrolling in one chapter; the server upserts it by id
/// (`PUT /api/fanfic/<fic>/reading-spans/<id>`) and the briefing's day
/// reconstruction reads the result. A port of `src/lib/readingSpans.ts`; keep
/// the two in step. All times are unix seconds.
public enum ReadingSpans {
    /// No scroll for this long ends a span; the next scroll starts a new one.
    public static let idleGapSeconds = 5 * 60
    /// The most one pause between scrolls adds to active time. Must match
    /// `_SPAN_GAP_CAP` in backend/routes/fanfic.py.
    public static let gapCapSeconds = 3 * 60
    /// How often an open span is re-sent while it keeps growing.
    public static let flushIntervalSeconds = 60
}

public struct ReadingSpan: Codable, Equatable {
    public let id: String
    public let ficId: String
    public let chapterId: String
    public var startedAt: Int
    public var endedAt: Int
    public var activeSeconds: Int
    public var startFraction: Double
    public var endFraction: Double
}

public struct ReadingSpanState: Equatable {
    public var span: ReadingSpan?
    /// The span has changed since it was last handed out for flushing.
    public var dirty = false
    public var lastFlushAt = 0

    public init() {}

    /// A span with no active time is a single scroll and isn't worth a write.
    private var worthSending: ReadingSpan? {
        guard dirty, let span, span.activeSeconds > 0 else { return nil }
        return span
    }

    /// Record one user scroll. Returns the span it closed when that span still
    /// had unsent changes.
    public mutating func recordScroll(now: Int, fraction: Double, ficId: String, chapterId: String,
                                      newId: () -> String = { ULID.make() }) -> ReadingSpan? {
        if var current = span, current.chapterId == chapterId,
           now >= current.endedAt, now - current.endedAt <= ReadingSpans.idleGapSeconds {
            current.activeSeconds += min(now - current.endedAt, ReadingSpans.gapCapSeconds)
            current.endedAt = now
            current.endFraction = fraction
            span = current
            dirty = true
            return nil
        }
        let closed = worthSending
        span = ReadingSpan(id: newId(), ficId: ficId, chapterId: chapterId, startedAt: now, endedAt: now,
                           activeSeconds: 0, startFraction: fraction, endFraction: fraction)
        dirty = true
        lastFlushAt = now
        return closed
    }

    /// The span to send now, if any: when forced (chapter change, app
    /// backgrounded, reader closed) or once the flush interval has passed.
    public mutating func takeFlush(now: Int, force: Bool = false) -> ReadingSpan? {
        guard let span = worthSending,
              force || now - lastFlushAt >= ReadingSpans.flushIntervalSeconds else { return nil }
        dirty = false
        lastFlushAt = now
        return span
    }

    /// End the current span (the chapter changed or the reader closed).
    public mutating func close() -> ReadingSpan? {
        let flush = worthSending
        self = ReadingSpanState()
        return flush
    }
}

/// Reading activity waiting for the server: span heartbeats and last-read
/// chapters. Both are idempotent on the server, so a replay is harmless and
/// only the newest version of each needs keeping.
public enum FicActivity: Codable, Equatable {
    case span(ReadingSpan)
    /// `POST /api/fanfic/<fic>/progress` — the desktop's last-read pointer.
    case progress(ficId: String, chapterId: String)

    /// One file per key: a newer span replaces its older heartbeat, and a book
    /// keeps only the chapter it was last opened at.
    var key: String {
        switch self {
        case .span(let span): return "span-" + span.id
        case .progress(let ficId, _): return "progress-" + ficId
        }
    }
}

public protocol FicActivityTransport {
    func sendFicActivity(_ item: FicActivity) async throws
}

/// The reader's outbox, one JSON file per key.
public final class FicActivityStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [FicActivity] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .map { try JSONDecoder().decode(FicActivity.self, from: Data(contentsOf: $0)) }
    }

    public func enqueue(_ item: FicActivity) throws {
        switch item {
        case .span(let span):
            guard ULID.isValid(span.ficId), ULID.isValid(span.chapterId), ULID.isValid(span.id) else {
                throw CaptureError.invalidID
            }
            // A heartbeat that arrives after a newer one changes nothing.
            if case .span(let saved)? = try load(item.key), saved.endedAt > span.endedAt { return }
        case .progress(let ficId, let chapterId):
            guard ULID.isValid(ficId), ULID.isValid(chapterId) else { throw CaptureError.invalidID }
        }
        try JSONEncoder().encode(item).write(to: file(item.key), options: .atomic)
    }

    /// Remove `item` only if it is still the queued version: a heartbeat
    /// enqueued while the older one was in flight must survive.
    public func remove(_ item: FicActivity) throws {
        guard try load(item.key) == item else { return }
        try fm.removeItem(at: file(item.key))
    }

    private func load(_ key: String) throws -> FicActivity? {
        let url = file(key)
        guard fm.fileExists(atPath: url.path) else { return nil }
        return try JSONDecoder().decode(FicActivity.self, from: Data(contentsOf: url))
    }

    private func file(_ key: String) -> URL { root.appendingPathComponent(key).appendingPathExtension("json") }
}

/// Uploads the reader's outbox. Something the server refuses (a chapter
/// deleted since, a clock far off) is dropped — there is nothing to review in
/// a reading heartbeat — while anything retryable stops the pass.
@MainActor
public final class FicActivitySync {
    private let store: FicActivityStore

    public init(store: FicActivityStore) { self.store = store }

    public func run(using transport: FicActivityTransport) async throws {
        for item in try store.list() {
            try Task.checkCancellation()
            do {
                try await transport.sendFicActivity(item)
            } catch let http as HTTPFailure where ![401, 403].contains(http.status) && !http.retryAutomatically {
                // Refused: fall through and drop it.
            }
            try store.remove(item)
        }
    }
}
