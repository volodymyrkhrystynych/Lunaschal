import Foundation
#if canImport(CoreGraphics)
import CoreGraphics
#endif

/// The newspaper as one continuous column: every issue page stacked top to
/// bottom on a single canvas, each at the column's full width and its own
/// height, so reading the paper is one vertical scroll fitted to the width.
public enum NotebookColumn {
    /// Canvas units, the same width a blank notebook page has.
    public static let width: CGFloat = 1240

    /// Each page's place in the column, from each page's height at the
    /// column's width, top to bottom with nothing between them.
    public static func slots(heights: [CGFloat]) -> [CGRect] {
        var y: CGFloat = 0
        return heights.map { height in
            defer { y += height }
            return CGRect(x: 0, y: y, width: width, height: height)
        }
    }

    /// The whole column.
    public static func bounds(of slots: [CGRect]) -> CGRect {
        CGRect(x: 0, y: 0, width: width, height: max(slots.last?.maxY ?? 0, 1))
    }

    /// The page at height `y` in the column, clamped to the first and last.
    public static func slot(atY y: CGFloat, in slots: [CGRect]) -> Int {
        guard !slots.isEmpty else { return 0 }
        return slots.firstIndex { y < $0.maxY } ?? slots.count - 1
    }

    /// What to show for page `slot` in a view of `view` points: the column's
    /// full width from the top of that page, in either orientation. Nothing
    /// is off to either side, so nothing scrolls sideways; the rest of the
    /// paper is below.
    public static func visibleFrame(slot: CGRect, view: CGSize) -> CGRect {
        NotebookFit.width(slot, in: view)
    }

    /// Which pages carry ink, from a picture of the ink alone: one flag per
    /// pixel row, true where anything was drawn, each row `unitsPerRow` of the
    /// column tall. A newspaper files only these and its cover, and PaperKit
    /// has no way to ask a markup what lies where.
    public static func slotsWithInk(rows: [Bool], unitsPerRow: CGFloat, slots: [CGRect]) -> Set<Int> {
        guard !slots.isEmpty, unitsPerRow > 0 else { return [] }
        var found = Set<Int>()
        for (row, inked) in rows.enumerated() where inked {
            found.insert(slot(atY: (CGFloat(row) + 0.5) * unitsPerRow, in: slots))
        }
        return found
    }
}

/// How much of a page to show in a view of a given shape.
public enum NotebookFit {
    /// All of `page`, centred, whatever shape the view is.
    public static func whole(_ page: CGRect, in view: CGSize) -> CGRect {
        guard view.width > 0, view.height > 0, page.width > 0, page.height > 0 else { return page }
        let viewRatio = view.height / view.width
        if page.height / page.width >= viewRatio {
            let width = page.height / viewRatio
            return CGRect(x: page.midX - width / 2, y: page.minY, width: width, height: page.height)
        }
        let height = page.width * viewRatio
        return CGRect(x: page.minX, y: page.midY - height / 2, width: page.width, height: height)
    }

    /// The page's full width, from its top.
    public static func width(_ page: CGRect, in view: CGSize) -> CGRect {
        guard view.width > 0, view.height > 0 else { return page }
        return CGRect(x: page.minX, y: page.minY, width: page.width, height: page.width * view.height / view.width)
    }
}

/// Turning a notes page with a finger. A page turn is a deliberate drag, not
/// a flick: the page follows the finger, and only a drag past the threshold
/// turns it when the finger lifts. Short of that it springs back.
public enum PageSwipe {
    /// Of the page's on-screen width, and never less than `minimum` points.
    public static let fraction: CGFloat = 0.35
    public static let minimum: CGFloat = 120
    /// How close to a side of the screen a finger has to land for its drag to
    /// turn the page. Anywhere else a finger is as likely to be a palm the
    /// Pencil's palm rejection missed, and a page that slides under it is a
    /// page being written on going somewhere else.
    public static let edgeWidth: CGFloat = 44

    public enum Edge: Equatable { case leading, trailing }

    /// The side a drag starting at `x` pulls from, in a view `width` wide; nil
    /// away from both.
    public static func edge(startX x: CGFloat, width: CGFloat) -> Edge? {
        guard width > 2 * edgeWidth else { return nil }
        if x <= edgeWidth { return .leading }
        if x >= width - edgeWidth { return .trailing }
        return nil
    }

    /// The part of a drag that counts: in from the side it started at. From
    /// the trailing edge it pulls the next page in, from the leading edge the
    /// previous one; outwards it does nothing.
    public static func pull(dx: CGFloat, from edge: Edge) -> CGFloat {
        edge == .trailing ? min(dx, 0) : max(dx, 0)
    }

    public static func threshold(pageWidth: CGFloat) -> CGFloat {
        max(minimum, pageWidth * fraction)
    }

    public struct Preview: Equatable {
        /// How far to slide the page, in points.
        public var offset: CGFloat
        /// The drag is heading past the last page, so finishing it adds one.
        public var creating: Bool
        /// 0...1, how far towards the threshold.
        public var progress: CGFloat
        /// Lifting now turns the page, or adds one.
        public var armed: Bool

        public static let idle = Preview(offset: 0, creating: false, progress: 0, armed: false)
    }

    public enum Outcome: Equatable {
        case stay, previous, next, newPage
    }

    /// A drag in progress, `dx` points from where it started; negative is
    /// towards the next page. Backwards from the first page nothing moves —
    /// a drag that does nothing should look like it does nothing.
    public static func preview(dx: CGFloat, index: Int, count: Int, threshold: CGFloat) -> Preview {
        guard dx != 0, !(dx > 0 && index <= 0) else { return .idle }
        let progress = min(1, abs(dx) / max(threshold, 1))
        return Preview(offset: dx, creating: dx < 0 && index >= count - 1, progress: progress, armed: progress >= 1)
    }

    /// What lifting the finger does.
    public static func outcome(dx: CGFloat, index: Int, count: Int, threshold: CGFloat) -> Outcome {
        let preview = preview(dx: dx, index: index, count: count, threshold: threshold)
        guard preview.armed else { return .stay }
        if preview.creating { return .newPage }
        return dx < 0 ? .next : .previous
    }
}
