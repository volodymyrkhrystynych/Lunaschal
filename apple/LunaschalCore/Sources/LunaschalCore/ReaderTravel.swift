import Foundation

/// How far the reader has actually scrolled in this sitting. Being near the end
/// of a chapter doesn't show it was read: a bookmark is saved as a share of the
/// scrollable height and restored as a share of the blocks, which lands past
/// where it was saved, so one reopened near the end can already measure as the
/// end. A point it would be wrong to clear there is left for Next. Offsets are
/// points below the top of the chapter.
public struct ReaderTravel: Equatable {
    private var top: Double?

    public init() {}

    /// Fed only the reader's own scrolling, so the first frame is where a
    /// restored position left the view.
    public mutating func scrolled(offset: Double) {
        top = min(top ?? offset, offset)
    }

    /// Whether `offset` is at least a screen below the highest point reached.
    public func coveredScreen(offset: Double, screen: Double) -> Bool {
        guard let top, screen > 0 else { return false }
        return offset - top >= screen
    }
}
