import XCTest
import PDFKit
import PaperKit
import UIKit
import LunaschalCore
@testable import Lunaschal

final class NotebookCropTests: XCTestCase {
    private let landscape = CGRect(x: 0, y: 0, width: 1376, height: 1032)

    func testOurWindowOnTheRightKeepsTheLeftHalf() {
        let ours = CGRect(x: 688, y: 0, width: 688, height: 1032)
        XCTAssertEqual(NotebookCrop.otherRegion(screen: landscape, window: ours), CGRect(x: 0, y: 0, width: 688, height: 1032))
    }

    func testOurWindowOnTheLeftKeepsTheRightPart() {
        let ours = CGRect(x: 0, y: 0, width: 458, height: 1032)
        XCTAssertEqual(NotebookCrop.otherRegion(screen: landscape, window: ours), CGRect(x: 458, y: 0, width: 918, height: 1032))
    }

    func testPortraitStackedWindows() {
        let portrait = CGRect(x: 0, y: 0, width: 1032, height: 1376)
        let ours = CGRect(x: 0, y: 0, width: 1032, height: 688)
        XCTAssertEqual(NotebookCrop.otherRegion(screen: portrait, window: ours), CGRect(x: 0, y: 688, width: 1032, height: 688))
    }

    func testStageManagerWindowInTheMiddleTakesTheBiggerSide() {
        let ours = CGRect(x: 300, y: 100, width: 500, height: 800)
        XCTAssertEqual(NotebookCrop.otherRegion(screen: landscape, window: ours), CGRect(x: 800, y: 0, width: 576, height: 1032))
    }

    func testFullScreenHasNoOtherApp() {
        XCTAssertNil(NotebookCrop.otherRegion(screen: landscape, window: landscape))
        // A few points of inset is a border, not another app.
        XCTAssertNil(NotebookCrop.otherRegion(screen: landscape, window: landscape.insetBy(dx: 10, dy: 10)))
    }

    func testPixelRectFollowsTheScreenshotsOwnScale() {
        let region = CGRect(x: 0, y: 0, width: 688, height: 1032)
        XCTAssertEqual(NotebookCrop.pixelRect(region, screen: landscape, imageSize: CGSize(width: 2752, height: 2064)),
                       CGRect(x: 0, y: 0, width: 1376, height: 2064))
        // Not bounds × scale (a downscaled screenshot): still half the image.
        XCTAssertEqual(NotebookCrop.pixelRect(region, screen: landscape, imageSize: CGSize(width: 1000, height: 750)),
                       CGRect(x: 0, y: 0, width: 500, height: 750))
    }

    func testPasteCropsOnlyAScreenSizedImage() throws {
        let ours = CGRect(x: 688, y: 0, width: 688, height: 1032)
        // A system screenshot: exactly the screen at 2x.
        let screenshot = try XCTUnwrap(solid(.red, CGSize(width: 2752, height: 2064)))
        XCTAssertTrue(NotebookCrop.isScreenshot(screenshot, screen: landscape, scale: 2))
        let cropped = NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: ours, scale: 2)
        XCTAssertEqual(cropped.width, 1376)
        XCTAssertEqual(cropped.height, 2064)
        // A copied photo with the screen's shape but not its size goes in whole.
        let photo = try XCTUnwrap(solid(.red, CGSize(width: 1376, height: 1032)))
        XCTAssertFalse(NotebookCrop.isScreenshot(photo, screen: landscape, scale: 2))
        XCTAssertEqual(NotebookCrop.cropIfScreenshot(photo, screen: landscape, window: ours, scale: 2).width, 1376)
        // Our window full-screen: a screenshot, but nothing to cut.
        XCTAssertEqual(NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: landscape, scale: 2).width, 2752)
    }

    func testCropKeepsTheWholeImageWhenTheShapeDisagrees() throws {
        let image = try XCTUnwrap(solid(.red, CGSize(width: 750, height: 1000)))
        let cropped = NotebookCrop.crop(image, screen: landscape, window: CGRect(x: 688, y: 0, width: 688, height: 1032))
        XCTAssertEqual(cropped.width, 750, "a portrait screenshot of a landscape screen is left whole")
        let wide = try XCTUnwrap(solid(.red, CGSize(width: 1376, height: 1032)))
        XCTAssertEqual(NotebookCrop.crop(wide, screen: landscape, window: CGRect(x: 688, y: 0, width: 688, height: 1032)).width, 688)
    }
}

func solid(_ color: UIColor, _ size: CGSize) -> CGImage? {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    return UIGraphicsImageRenderer(size: size, format: format).image { context in
        color.setFill()
        context.fill(CGRect(origin: .zero, size: size))
    }.cgImage
}

@MainActor
final class NotebookEditorTests: XCTestCase {
    private func store() throws -> NotebookStore {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return try NotebookStore(root: root)
    }

    private func issuePDF(pages: Int) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".pdf")
        // Landscape-ish tabloid pages, so the page shape is visibly not A4.
        let data = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 800, height: 1000)).pdfData { context in
            for index in 0..<pages {
                context.beginPage()
                UIColor.black.setFill()
                context.fill(CGRect(x: 0, y: 900, width: 800, height: 100))
                ("Page \(index + 1)" as NSString).draw(at: CGPoint(x: 40, y: 40), withAttributes: [.font: UIFont.systemFont(ofSize: 40)])
            }
        }
        try data.write(to: url)
        return url
    }

    /// RGBA of one pixel, top-left origin.
    private func pixel(_ image: CGImage, x: Int, y: Int) -> (r: UInt8, g: UInt8, b: UInt8) {
        var bytes = [UInt8](repeating: 0, count: 4)
        let context = CGContext(data: &bytes, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                                space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.draw(image, in: CGRect(x: -x, y: y - image.height + 1, width: image.width, height: image.height))
        return (bytes[0], bytes[1], bytes[2])
    }

    func testPagesAddDeleteAndBounds() throws {
        let store = try store()
        let model = NotebookEditorModel(store: store, notebook: try store.create())
        XCTAssertEqual(model.pageCount, 1)
        XCTAssertFalse(model.canDeleteCurrent, "the last page stays")
        model.addPage()
        model.addPage()
        XCTAssertEqual(model.pageCount, 3)
        XCTAssertEqual(model.current, 2)
        model.go(to: 7)
        XCTAssertEqual(model.current, 2)
        model.go(to: 1)
        model.deleteCurrentPage()
        XCTAssertEqual(model.pageCount, 2)
        XCTAssertEqual(model.current, 1)
        XCTAssertEqual(model.pageLabel, "2 / 2")
    }

    func testScreenshotLandsOnTheCurrentPageAndRoundTrips() async throws {
        let store = try store()
        let notebook = try store.create()
        let model = NotebookEditorModel(store: store, notebook: notebook)
        model.addPage()
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 600, height: 400))))
        XCTAssertTrue(NotebookPage.isBlank(model.pages[0]))
        XCTAssertFalse(NotebookPage.isBlank(model.pages[1]))
        XCTAssertEqual(model.marked, [1])
        // Below what's there, the second time.
        let first = model.pages[1].contentsRenderFrame
        model.insertScreenshot(try XCTUnwrap(solid(.blue, CGSize(width: 600, height: 400))))
        XCTAssertGreaterThan(model.pages[1].contentsRenderFrame.maxY, first.maxY)
        await model.checkpoint()

        let saved = try store.notebook(notebook.id)
        XCTAssertEqual(saved.pageCount, 2)
        XCTAssertEqual(saved.markedPages, [1])
        XCTAssertNotNil(try store.previewURL(saved))
        let reopened = NotebookEditorModel(store: store, notebook: saved)
        XCTAssertEqual(reopened.pageCount, 2)
        XCTAssertFalse(NotebookPage.isBlank(reopened.pages[1]))
    }

    func testBackOnAnUntouchedNotebookStillLeavesOneToContinue() async throws {
        let store = try store()
        let notebook = try store.create()
        await NotebookEditorModel(store: store, notebook: notebook).checkpoint()
        XCTAssertNotNil(try store.notebook(notebook.id).checkpoint)
    }

    func testRenderIsUprightAndSkipsBlankPages() async throws {
        let store = try store()
        let model = NotebookEditorModel(store: store, notebook: try store.create())
        model.addPage()
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 1000, height: 300))))
        let files = await model.renderForSave()
        XCTAssertEqual(files.map(\.name), ["Notes p2.jpg"])
        // The picture went in at the top of the page; it must render at the top.
        let image = try XCTUnwrap(UIImage(data: files[0].data)?.cgImage)
        let top = pixel(image, x: image.width / 2, y: 80)
        let bottom = pixel(image, x: image.width / 2, y: image.height - 80)
        XCTAssertGreaterThan(top.r, 200); XCTAssertLessThan(top.g, 80)
        XCTAssertGreaterThan(bottom.g, 200, "the bottom of the page stays white")
    }

    func testNewspaperPagesKeepTheirShapeAndFileTheCoverPlusMarked() async throws {
        let store = try store()
        let notebook = try store.createNewspaper(date: "2026-10-06", pdf: try issuePDF(pages: 4), pageCount: 4)
        let model = NotebookEditorModel(store: store, notebook: notebook)
        XCTAssertNil(model.error)
        XCTAssertEqual(model.pageCount, 4)
        XCTAssertEqual(model.pages[0].bounds.size, CGSize(width: 1240, height: 1550))
        XCTAssertEqual(model.pageLabel, "p. 1 / 4")
        XCTAssertFalse(model.canDeleteCurrent, "issue pages can't be deleted")
        model.go(to: 2)
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 400, height: 300))))
        model.addPage()
        XCTAssertTrue(model.canDeleteCurrent, "a page added after the issue can")
        XCTAssertEqual(model.pages[4].bounds, NotebookPage.blank)

        let files = await model.renderForSave()
        XCTAssertEqual(files.map(\.name), ["Toronto Star 2026-10-06 p1.jpg", "Toronto Star 2026-10-06 p3.jpg"])
        XCTAssertEqual(model.entryText, "Toronto Star, 2026-10-06")
        // The newspaper is drawn under the ink: the black band at the page's foot.
        let cover = try XCTUnwrap(UIImage(data: files[0].data)?.cgImage)
        XCTAssertLessThan(pixel(cover, x: cover.width / 2, y: cover.height - 20).r, 60)
        XCTAssertGreaterThan(pixel(cover, x: cover.width / 2, y: cover.height / 2).r, 200)
    }

    func testInboxScreenshotsArePlacedWhenAnEditorOpens() throws {
        let store = try store()
        let png = try XCTUnwrap(UIImage(cgImage: XCTUnwrap(solid(.green, CGSize(width: 300, height: 200)))).pngData())
        try store.enqueueScreenshot(png)
        let model = NotebookEditorModel(store: store, notebook: try store.create())
        model.drainInbox()
        XCTAssertFalse(NotebookPage.isBlank(model.pages[0]))
        XCTAssertEqual(store.inboxCount(), 0)
    }

    func testScreenshotWithNoEditorWaitsInTheInbox() throws {
        let store = try store()
        let session = NotebookSession.shared
        let (savedStore, savedEditor) = (session.store, session.editor)
        defer { session.store = savedStore; session.editor = savedEditor }
        session.store = store
        session.editor = nil
        let png = try XCTUnwrap(UIImage(cgImage: XCTUnwrap(solid(.green, CGSize(width: 300, height: 200)))).pngData())
        XCTAssertEqual(try session.deliver(png), "Saved for the next notebook you open.")
        XCTAssertEqual(store.inboxCount(), 1)
        let model = NotebookEditorModel(store: store, notebook: try store.create())
        session.editor = model
        XCTAssertEqual(try session.deliver(png), "Added to page 1.")
        XCTAssertThrowsError(try session.deliver(Data("not an image".utf8)))
    }
}
