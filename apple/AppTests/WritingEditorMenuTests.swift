import LunaschalCore
import UIKit
import XCTest
@testable import Lunaschal

/// "Discuss rewrite" in a chapter's edit menu: offered for a selected passage,
/// and not for a note, an empty selection, or whitespace a drag happened to catch.
@MainActor
final class WritingEditorMenuTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    private func editor(_ kind: WritingDocumentKind, text: String) throws -> (WritingEditorModel, UITextView) {
        let capture = try CaptureModel(store: CaptureStore(root: root))
        let writing = WritingModel(capture: capture)
        let project = ULID.make()
        let document = try writing.store.create(kind, project: project, title: "The Wreck")
        try writing.store.edit(document.key, project: project, content: text)
        let model = WritingEditorModel(writing: writing, document: try XCTUnwrap(writing.store.document(document.key, project: project)))
        let view = UITextView()
        view.text = text
        return (model, view)
    }

    private func menuTitles(_ model: WritingEditorModel, _ view: UITextView, _ range: NSRange) -> [String]? {
        view.selectedRange = range
        let coordinator = WritingTextEditor.Coordinator(model)
        let copy = UIAction(title: "Copy") { _ in }
        return coordinator.textView(view, editMenuForTextIn: range, suggestedActions: [copy])?
            .children.map { ($0 as? UIAction)?.title ?? "" }
    }

    func testASelectedPassageCanBeDiscussed() throws {
        let (model, view) = try editor(.chapter, text: "The boat was down. Mara came up through the hatch.")
        let range = NSRange(location: 19, length: 31)
        XCTAssertEqual(menuTitles(model, view, range), ["Discuss rewrite", "Copy"])
        XCTAssertEqual(model.passage(in: view), "Mara came up through the hatch.")
    }

    func testDiscussingKeepsThePassageForTheSheet() throws {
        let (model, _) = try editor(.chapter, text: "Some prose.")
        model.discuss("Some prose.")
        XCTAssertEqual(model.discussing?.passage, "Some prose.")
    }

    func testANoteOffersOnlyTheSystemMenu() throws {
        let (model, view) = try editor(.note, text: "Nineteen. Raised on the docks.")
        XCTAssertNil(menuTitles(model, view, NSRange(location: 0, length: 9)))
    }

    func testNothingWorthDiscussingOffersOnlyTheSystemMenu() throws {
        let (model, view) = try editor(.chapter, text: "One.   Two.")
        XCTAssertNil(menuTitles(model, view, NSRange(location: 4, length: 3)))
    }
}
