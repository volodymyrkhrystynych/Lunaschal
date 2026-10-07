import Foundation

/// Measures how long the main thread goes without running anything else while
/// a sync pass runs: a tick that should come every 50 ms and comes late was
/// held up by whatever had the main thread. Nothing is published per tick, so
/// watching doesn't itself make SwiftUI redraw.
@MainActor
final class StallWatch {
    private static let interval: UInt64 = 50_000_000
    private var longest: Double = 0
    private var task: Task<Void, Never>?

    init() {
        task = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                let start = Date()
                try? await Task.sleep(nanoseconds: Self.interval)
                let late = Date().timeIntervalSince(start) - Double(Self.interval) / 1e9
                guard let self else { return }
                self.longest = max(self.longest, late)
            }
        }
    }

    /// Stops watching and returns the longest stall, in seconds.
    func stop() -> Double {
        task?.cancel()
        task = nil
        return longest
    }
}
