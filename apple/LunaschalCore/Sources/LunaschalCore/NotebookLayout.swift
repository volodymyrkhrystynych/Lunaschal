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

    /// How far the canvas may zoom, from the fit up to `maximum` times it.
    /// With zoom off the range is the fit alone, so a palm that lands as a
    /// pinch has nowhere to take the page.
    public static func zoomRange(fitted: CGFloat, maximum: CGFloat, enabled: Bool) -> ClosedRange<CGFloat> {
        enabled ? fitted...(fitted * max(maximum, 1)) : fitted...fitted
    }

    /// Back to the fit's scale from a zoomed-in `visible` frame: the fit's
    /// width and sideways position, centred down the page on wherever the
    /// reader was looking, never above the top.
    public static func unzoomed(_ visible: CGRect, fit: CGRect) -> CGRect {
        CGRect(x: fit.minX, y: max(visible.midY - fit.height / 2, 0), width: fit.width, height: fit.height)
    }
}
