import Foundation

/// When the reader offers its jump to the top of the chapter: a fast flick
/// towards the top says the reader wants to be there, and an ordinary scroll
/// back to re-read a line doesn't. Offsets are points below the top of the
/// chapter; times are seconds from any steady clock.
public struct ReaderTopJump: Equatable {
    /// A flick is at least this fast, measured over at least `showDistance`
    /// and the last `window` seconds or so: one frame's speed is noise.
    public static let showSpeed = 2_000.0
    public static let showDistance = 300.0
    public static let window = 0.15
    /// Within this of the top the button has nothing left to save.
    public static let topZone = 300.0
    /// Scrolling back down this far means the flick was over.
    public static let hideDistance = 40.0

    private struct Sample: Equatable {
        let offset: Double
        let time: Double
    }

    public private(set) var isShown = false
    /// The upward run inside `window`, oldest first, and the one sample
    /// before it: laying out a long chapter can drop frames, and a run with no
    /// starting point would be no run at all.
    private var rising: [Sample] = []
    private var last: Sample?
    private var fallingFrom: Double?

    public init() {}

    public mutating func scrolled(offset: Double, at time: Double) {
        let sample = Sample(offset: offset, time: time)
        defer { last = sample }
        guard let last else { return }
        if offset < last.offset {
            fallingFrom = nil
            if rising.isEmpty { rising = [last] }
            rising.append(sample)
            while rising.count > 2, time - rising[1].time > Self.window { rising.removeFirst() }
            if let start = rising.first, time > start.time {
                let distance = start.offset - offset
                if distance >= Self.showDistance, distance / (time - start.time) >= Self.showSpeed { isShown = true }
            }
        } else if offset > last.offset {
            rising = []
            let start = fallingFrom ?? last.offset
            fallingFrom = start
            if offset - start >= Self.hideDistance { isShown = false }
        }
        if offset <= Self.topZone { isShown = false }
    }

    public mutating func reset() { self = ReaderTopJump() }
}
