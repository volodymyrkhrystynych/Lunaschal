import Foundation

/// How long one sync pass took, step by step, and the longest the UI thread
/// was held while it ran: what Settings shows under Transfers, so a freeze on
/// a device can be traced to a step without attaching a debugger.
public struct SyncTimings: Equatable, Sendable {
    public struct Step: Equatable, Sendable {
        public let name: String
        public let seconds: Double
    }

    public private(set) var steps: [Step] = []
    /// The longest the main thread went without running anything else.
    public var longestStall: Double = 0

    public init() {}

    public mutating func record(_ name: String, seconds: Double) {
        steps.append(Step(name: name, seconds: max(0, seconds)))
    }

    public var total: Double { steps.reduce(0) { $0 + $1.seconds } }

    /// "Last sync 4.2 s · UI held up to 1.3 s · slowest: journal 2.1 s, uploads 0.9 s, weather 0.4 s"
    public func summary(slowest count: Int = 3) -> String {
        var parts = ["Last sync \(Self.format(total))"]
        if longestStall >= 0.1 { parts.append("UI held up to \(Self.format(longestStall))") }
        let slow = steps.filter { $0.seconds >= 0.05 }.sorted { $0.seconds > $1.seconds }.prefix(count)
        if !slow.isEmpty {
            parts.append("slowest: " + slow.map { "\($0.name) \(Self.format($0.seconds))" }.joined(separator: ", "))
        }
        return parts.joined(separator: " · ")
    }

    static func format(_ seconds: Double) -> String {
        seconds < 10 ? String(format: "%.1f s", seconds) : String(format: "%.0f s", seconds)
    }
}
