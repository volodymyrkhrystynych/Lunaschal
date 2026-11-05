import XCTest
import UIKit
import LunaschalCore
@testable import Lunaschal

@MainActor
final class CaptureEntryTests: XCTestCase {
    override func tearDown() {
        UIPasteboard.general.items = []
        super.tearDown()
    }

    func testPastingImagesStagesAttachmentsWithoutReplacingWords() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root)
        let editor = CaptureEntryTextView()
        editor.text = "Lunch with friends"
        editor.selectedRange = NSRange(location: 0, length: editor.text.utf16.count)
        var failures: [Error] = []
        editor.onPasteImage = { image in
            do {
                try store.stageFile(data: XCTUnwrap(image.pngData()), name: "Pasted image.png", contentType: "image/png")
            } catch { failures.append(error) }
        }
        let image = UIGraphicsImageRenderer(size: CGSize(width: 12, height: 12)).image { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 12, height: 12))
        }
        UIPasteboard.general.images = [image, image]
        XCTAssertTrue(editor.canPerformAction(#selector(UIResponderStandardEditActions.paste(_:)), withSender: nil))
        editor.paste(nil)
        XCTAssertTrue(failures.isEmpty)
        XCTAssertEqual(editor.text, "Lunch with friends")
        let capture = try store.commitDraft(text: editor.text, youtubeURLs: [])
        XCTAssertEqual(capture.files.count, 2)
        XCTAssertTrue(capture.files.allSatisfy(\.isImage))
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id).files, capture.files)
    }

    func testTextPasteStillReplacesTheSelectedText() async {
        let editor = CaptureEntryTextView()
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 400, height: 800))
        let controller = UIViewController()
        window.rootViewController = controller
        editor.frame = window.bounds
        controller.view.addSubview(editor)
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        editor.becomeFirstResponder()
        editor.text = "Old text"
        editor.selectedRange = NSRange(location: 0, length: 3)
        editor.onPasteImage = { _ in XCTFail("Text must not become an image") }
        UIPasteboard.general.string = "New"
        let changed = XCTNSNotificationExpectation(name: UITextView.textDidChangeNotification, object: editor)
        editor.paste(nil)
        await fulfillment(of: [changed], timeout: 3)
        XCTAssertEqual(editor.text, "New text")
    }
}
