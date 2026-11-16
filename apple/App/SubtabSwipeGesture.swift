import LunaschalCore
import SwiftUI
import UIKit

extension View {
    /// A sideways swipe anywhere on the page moves to the page beside it, in
    /// the order of the switch at the top (see `SubtabSwipe`). Off where the
    /// page has a sideways swipe of its own, as the calendar does for days.
    func subtabSwipe<Page: Equatable>(_ page: Binding<Page>, pages: [Page], enabled: Bool = true) -> some View {
        gesture(SubtabSwipeGesture(enabled: enabled) { dx, dy in
            if let next = SubtabSwipe.destination(from: page.wrappedValue, in: pages, dx: dx, dy: dy) {
                page.wrappedValue = next
            }
        })
    }
}

/// A UIKit pan rather than a SwiftUI DragGesture, because only a UIKit
/// delegate can stand aside for what is under the finger: a row's swipe
/// actions (a Daily log's Remove) and anything that scrolls sideways (the
/// composer's photo strip). A DragGesture fired alongside both, so deleting a
/// log also turned the page.
struct SubtabSwipeGesture: UIGestureRecognizerRepresentable {
    let enabled: Bool
    let onSwipe: (Double, Double) -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator { Coordinator() }

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let pan = UIPanGestureRecognizer()
        pan.delegate = context.coordinator
        // Left at UIKit's default on purpose: once a swipe is recognised the
        // touch is taken from the row under it, which would otherwise read the
        // lift as a tap and open the journal entry the swipe began on. A tap
        // never moves far enough sideways to begin this pan, so it still lands.
        pan.cancelsTouchesInView = true
        pan.isEnabled = enabled
        return pan
    }

    func updateUIGestureRecognizer(_ recognizer: UIPanGestureRecognizer, context: Context) {
        recognizer.isEnabled = enabled
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        guard recognizer.state == .ended else { return }
        let moved = recognizer.translation(in: recognizer.view)
        onSwipe(Double(moved.x), Double(moved.y))
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
            guard let pan = recognizer as? UIPanGestureRecognizer, let view = pan.view else { return false }
            let velocity = pan.velocity(in: view)
            guard abs(velocity.x) > abs(velocity.y) else { return false }
            return !startsInSidewaysScroller(pan)
        }

        /// The list keeps scrolling while this watches, but nothing else runs
        /// alongside it: a feed card is a SwiftUI button, which a sideways
        /// swipe across it would otherwise still count as a tap, opening the
        /// entry the swipe began on.
        func gestureRecognizer(_ recognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            other.view is UIScrollView
        }

        /// A row's swipe actions win: this waits for that recognizer to give
        /// up, which it does when the row has nothing on that side. UIKit's
        /// class for it is private, so it is found by name; if the name ever
        /// changes, the page simply turns alongside the row again.
        func gestureRecognizer(_ recognizer: UIGestureRecognizer,
                               shouldRequireFailureOf other: UIGestureRecognizer) -> Bool {
            String(describing: type(of: other)).contains("SwipeAction")
        }

        /// A sideways scroller under the finger has the swipe: the composer's
        /// staged photos, a row of pills.
        private func startsInSidewaysScroller(_ pan: UIPanGestureRecognizer) -> Bool {
            guard let window = pan.view?.window else { return false }
            let now = pan.location(in: window), moved = pan.translation(in: window)
            let start = CGPoint(x: now.x - moved.x, y: now.y - moved.y)
            var view = window.hitTest(start, with: nil)
            while let current = view {
                if let scroll = current as? UIScrollView, scroll.isScrollEnabled,
                   scroll.contentSize.width > scroll.bounds.width + 1 {
                    return true
                }
                view = current.superview
            }
            return false
        }
    }
}
