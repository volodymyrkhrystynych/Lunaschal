import XCTest
import PencilKit
import UIKit
@testable import Lunaschal

@MainActor
final class DrawingToolbarTests: XCTestCase {
    func testFavoritesAndDockSurviveReopening() throws {
        let name = "toolbar-tests-\(UUID())"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        var preferences = DrawingToolbarPreferences()
        preferences.favorites = [DrawingFavorite(kind: "fountainPen", red: 0.3, green: 0.5, blue: 0.7, width: 4), DrawingFavorite(kind: "ruler")]
        preferences.dock = .left
        preferences.offset = 0.8
        preferences.save(to: defaults)
        XCTAssertEqual(DrawingToolbarPreferences.load(from: defaults), preferences)
        defaults.set(Data("broken".utf8), forKey: DrawingToolbarPreferences.key)
        XCTAssertEqual(DrawingToolbarPreferences.load(from: defaults).favorites.map(\.kind), DrawingFavorite.defaults.map(\.kind))
    }

    func testEveryInkPreservesItsColorAndValidWidth() throws {
        for (kind, ink) in zip(DrawingFavorite.inkKinds, DrawingFavorite.inks) {
            let favorite = DrawingFavorite(kind: kind, red: 0.2, green: 0.4, blue: 0.6, width: ink.defaultWidth)
            let tool = try XCTUnwrap(favorite.tool as? PKInkingTool)
            XCTAssertEqual(tool.inkType, ink)
            XCTAssertEqual(tool.width, ink.defaultWidth)
            XCTAssertEqual(tool.color, favorite.color)
        }
        XCTAssertNotNil(DrawingFavorite(kind: "eraser").tool as? PKEraserTool)
        XCTAssertNotNil(DrawingFavorite(kind: "pixelEraser").tool as? PKEraserTool)
        XCTAssertNotNil(DrawingFavorite(kind: "lasso").tool as? PKLassoTool)
    }

    func testDockStaysInsideSafeAreaAfterRotationAndWithManyFavorites() {
        for size in [CGSize(width: 320, height: 700), CGSize(width: 1000, height: 500)] {
            let bounds = CGRect(origin: CGPoint(x: 8, y: 32), size: size)
            for dock in DrawingDock.allCases {
                for offset in [-1.0, 0.0, 0.5, 1.0, 2.0] {
                    let frame = dock.frame(in: bounds, length: 2000, offset: offset)
                    XCTAssertTrue(bounds.contains(frame))
                    XCTAssertEqual(dock.vertical ? frame.width : frame.height, 44)
                }
            }
            XCTAssertEqual(DrawingDock.nearest(to: CGPoint(x: bounds.minX, y: bounds.midY), in: bounds), .left)
            XCTAssertEqual(DrawingDock.nearest(to: CGPoint(x: bounds.midX, y: bounds.maxY), in: bounds), .bottom)
        }
    }

    func testButtonsFollowUndoManagerAndActuallyUndoAndRedo() throws {
        let controller = UIViewController()
        let toolbar = DrawingToolbar(presenter: controller)
        controller.view.addSubview(toolbar)
        let manager = UndoManager()
        let value = UndoableValue(manager: manager)
        toolbar.undoManagerProvider = { manager }
        let buttons = descendants(toolbar).compactMap { $0 as? UIButton }
        let undo = try XCTUnwrap(buttons.first { $0.accessibilityLabel == "Undo" })
        let redo = try XCTUnwrap(buttons.first { $0.accessibilityLabel == "Redo" })
        toolbar.refreshUndo()
        XCTAssertFalse(undo.isEnabled)
        XCTAssertFalse(redo.isEnabled)
        manager.beginUndoGrouping()
        value.set(10)
        manager.endUndoGrouping()
        toolbar.refreshUndo()
        XCTAssertTrue(undo.isEnabled)
        undo.sendActions(for: .touchUpInside)
        XCTAssertEqual(value.value, 0)
        XCTAssertTrue(redo.isEnabled)
        redo.sendActions(for: .touchUpInside)
        XCTAssertEqual(value.value, 10)
    }

    private func descendants(_ view: UIView) -> [UIView] { view.subviews.flatMap { [$0] + descendants($0) } }
}

private final class UndoableValue {
    let manager: UndoManager
    var value = 0
    init(manager: UndoManager) { self.manager = manager }
    func set(_ newValue: Int) {
        let old = value
        manager.registerUndo(withTarget: self) { $0.set(old) }
        value = newValue
    }
}
