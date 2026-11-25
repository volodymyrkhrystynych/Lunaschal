import Foundation

/// How often something fast may reach the screen. Every `@Published` write
/// re-renders whatever observes it, and a streamed reply arrives a token at a
/// time: published one by one, a long answer kept the main thread re-laying out
/// the transcript until iOS killed the app as frozen (0x8BADF00D, every stack
/// deep in SwiftUI's lazy-stack layout). So the stream is gathered here and
/// handed over at most once per `interval`; the last piece is never held back,
/// because `drain()` takes whatever is left when the stream ends.
public struct StreamCoalescer {
    public let interval: TimeInterval
    private var pending = ""
    private var last: TimeInterval?

    public init(interval: TimeInterval = 0.1) { self.interval = interval }

    /// The text to show now, or nil to keep gathering. The first piece goes
    /// straight through, so a reply starts appearing without a delay.
    public mutating func add(_ text: String, now: TimeInterval) -> String? {
        pending += text
        if let last, now - last < interval { return nil }
        last = now
        defer { pending = "" }
        return pending.isEmpty ? nil : pending
    }

    /// Whatever is still gathered: called when the stream ends or changes kind.
    public mutating func drain() -> String? {
        defer { pending = "" }
        return pending.isEmpty ? nil : pending
    }
}

/// A gate for progress messages: one per `interval`, so a download reporting
/// every chunk doesn't re-render every screen that observes its model.
public struct ProgressThrottle {
    public let interval: TimeInterval
    private var last: TimeInterval?

    public init(interval: TimeInterval = 0.5) { self.interval = interval }

    public mutating func ready(now: TimeInterval) -> Bool {
        if let last, now - last < interval { return false }
        last = now
        return true
    }

    /// The next report goes through: a new download starts with its first line.
    public mutating func reset() { last = nil }
}
