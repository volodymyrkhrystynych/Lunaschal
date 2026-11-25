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

        // The control: what the root does show still reaches it.
        capture.message = "Shown in the root's alert"
        settle()
        XCTAssertGreaterThan(CaptureRoot.bodyEvaluations, before)
    }
}
