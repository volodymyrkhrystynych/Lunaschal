import LunaschalCore
import SwiftUI
import XCTest
@testable import Lunaschal

/// The app froze, on any tab, until iOS killed it (0x8BADF00D, the main thread
/// deep in SwiftUI layout): the root view observed Chat, so every streamed
/// piece of a reply and every keystroke re-rendered every tab. These hold the
/// root to what it actually shows.
@MainActor
final class RootRenderTests: XCTestCase {
    private var root: URL!
    private var window: UIWindow?

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws {
        window?.isHidden = true
        window = nil
        try? FileManager.default.removeItem(at: root)
    }

    private func settle(_ seconds: TimeInterval = 0.3) {
        RunLoop.main.run(until: Date().addingTimeInterval(seconds))
    }

    func testChatAndCodeChangesPublishNothingTheRootObserves() throws {
        let capture = try CaptureModel(store: CaptureStore(root: root))
        let owned = UnwatchedModels(capture: capture)
        var published = 0
        let watch = owned.objectWillChange.sink { published += 1 }
        defer { watch.cancel() }
        for index in 0..<50 { owned.chat.input = "typing \(index)" }
        owned.code.notice = "agent event"
        for index in 0..<50 { owned.email.search = "query \(index)" }
        XCTAssertEqual(published, 0)
    }

    func testTypingInChatDoesNotReRenderTheWholeApp() throws {
        let capture = try CaptureModel(store: CaptureStore(root: root))
        let owned = UnwatchedModels(capture: capture)
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = UIHostingController(rootView: CaptureRoot(model: capture, unwatched: owned))
        window.makeKeyAndVisible()
        self.window = window
        settle(1)

        let before = CaptureRoot.bodyEvaluations
        for index in 0..<50 {
            owned.chat.input = "typing \(index)"
            settle(0.01)
        }
        settle()
        XCTAssertEqual(CaptureRoot.bodyEvaluations, before, "a keystroke in Chat re-rendered the root")

        for index in 0..<50 {
            owned.email.search = "query \(index)"
            settle(0.01)
        }
        settle()
        XCTAssertEqual(CaptureRoot.bodyEvaluations, before, "a keystroke in Email's search re-rendered the root")

        // The control: what the root does show still reaches it.
        capture.message = "Shown in the root's alert"
        settle()
        XCTAssertGreaterThan(CaptureRoot.bodyEvaluations, before)
    }

    /// The root no longer observes Email, so the badge for a lost mail account
    /// arrives only through its bridge. An account cached as needing a new
    /// sign-in has to badge Email (wide iPad) or More (everything else).
    func testALostMailAccountStillBadgesTheRoot() throws {
        let capture = try CaptureModel(store: CaptureStore(root: root))
        try capture.emailStore.saveAccounts([
            EmailAccount(provider: "gmail", emailAddress: "someone@example.com", connected: false,
                         needsReconnect: true),
        ])
        let owned = UnwatchedModels(capture: capture)
        XCTAssertTrue(owned.email.needsReconnect)
        XCTAssertEqual(mailBadge(hosting: CaptureRoot(model: capture, unwatched: owned)), "1")
    }

    func testAConnectedMailAccountLeavesTheBadgeOff() throws {
        let capture = try CaptureModel(store: CaptureStore(root: root))
        try capture.emailStore.saveAccounts([
            EmailAccount(provider: "gmail", emailAddress: "someone@example.com", connected: true),
        ])
        let owned = UnwatchedModels(capture: capture)
        XCTAssertNil(mailBadge(hosting: CaptureRoot(model: capture, unwatched: owned)))
    }

    /// The badge on whichever tab carries mail: Email, or More. The window
    /// needs the app's scene, or SwiftUI never lays the tab bar out.
    private func mailBadge(hosting view: CaptureRoot) -> String? {
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        let window = scene.map { UIWindow(windowScene: $0) } ?? UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = UIHostingController(rootView: view)
        window.makeKeyAndVisible()
        self.window = window
        settle(1)
        guard let bar = tabBar(in: window) else {
            XCTFail("no tab bar under the root")
            return nil
        }
        let items = bar.items ?? []
        guard let item = items.first(where: { $0.title == "Email" }) ?? items.first(where: { $0.title == "More" }) else {
            XCTFail("no Email or More tab among \(items.map { $0.title ?? "" })")
            return nil
        }
        return item.badgeValue
    }

    private func tabBar(in view: UIView) -> UITabBar? {
        if let bar = view as? UITabBar { return bar }
        for subview in view.subviews {
            if let found = tabBar(in: subview) { return found }
        }
        return nil
    }
}
