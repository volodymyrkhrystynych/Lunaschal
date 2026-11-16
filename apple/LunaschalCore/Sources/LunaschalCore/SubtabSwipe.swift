import Foundation

/// A sideways swipe across the Capture or Journal tab moves to the page beside
/// the one showing, as the switch at the top would. The pages are laid out in
/// the switch's order, so a swipe to the left brings in the page on the right,
/// the way a calendar's day swipe already did.
public enum SubtabSwipe {
    /// How far a finger has to travel before a swipe counts. The same 80 points
    /// the calendar's day swipe asks for, so the two feel alike.
    public static let minimumDistance: Double = 80

    /// The page a finished swipe lands on, or nil to stay put: too short, more
    /// up-and-down than sideways (a scroll that drifted), or off the end.
    public static func destination<Page: Equatable>(from current: Page, in pages: [Page],
                                                    dx: Double, dy: Double) -> Page? {
        guard isSideways(dx: dx, dy: dy), let index = pages.firstIndex(of: current) else { return nil }
        let next = dx < 0 ? index + 1 : index - 1
        return pages.indices.contains(next) ? pages[next] : nil
    }

    /// Twice as far across as down: a list scrolled at a slant is still a scroll.
    public static func isSideways(dx: Double, dy: Double) -> Bool {
        abs(dx) >= minimumDistance && abs(dx) > 2 * abs(dy)
    }
}
