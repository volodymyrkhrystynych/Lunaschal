import XCTest
import PDFKit
import PaperKit
import PencilKit
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
        // A system screenshot: exactly the screen at 2x. Lunaschal on the
        // right, so the left half is what's kept.
        let screenshot = try XCTUnwrap(solid(.red, CGSize(width: 2752, height: 2064)))
        XCTAssertTrue(NotebookCrop.isScreenshot(screenshot, screen: landscape, scale: 2))
        let cropped = NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: ours, scale: 2)
        XCTAssertEqual(cropped.outcome, .cropped)
        XCTAssertEqual(cropped.image.width, 1376)
        XCTAssertEqual(cropped.image.height, 2064)
        // A copied photo with the screen's shape but not its size goes in whole.
        let photo = try XCTUnwrap(solid(.red, CGSize(width: 1376, height: 1032)))
        XCTAssertFalse(NotebookCrop.isScreenshot(photo, screen: landscape, scale: 2))
        let whole = NotebookCrop.cropIfScreenshot(photo, screen: landscape, window: ours, scale: 2)
        XCTAssertEqual(whole.outcome, .notScreenshot(width: 1376, height: 1032))
        XCTAssertEqual(whole.image.width, 1376)
        // Our window full-screen: a screenshot, but nothing to cut.
        let full = NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: landscape, scale: 2)
        XCTAssertEqual(full.outcome, .fullScreen)
        XCTAssertEqual(full.image.width, 2752)
    }

    func testDisplayZoomScreenshotsAreRecognisedByTheNativePixels() throws {
        // "More Space": more points than the panel has pixels at 2x, so only
        // the native size (reported portrait) says what a screenshot is.
        let zoomed = CGRect(x: 0, y: 0, width: 1590, height: 1192)
        let native = CGSize(width: 2064, height: 2752)
        let screenshot = try XCTUnwrap(solid(.red, CGSize(width: 2752, height: 2064)))
        XCTAssertFalse(NotebookCrop.isScreenshot(screenshot, screen: zoomed, scale: 2))
        XCTAssertTrue(NotebookCrop.isScreenshot(screenshot, screen: zoomed, scale: 2, native: native))
        let result = NotebookCrop.cropIfScreenshot(screenshot, screen: zoomed, window: CGRect(x: 795, y: 0, width: 795, height: 1192),
                                                   scale: 2, native: native)
        XCTAssertEqual(result.outcome, .cropped)
        XCTAssertEqual(result.image.width, 1376)
    }

    func testPasteCutsUnlessTheScreenshotClearlyHasLunaschalOnTheOtherSide() throws {
        let screenPixels = CGSize(width: 2752, height: 2064)
        let left = CGRect(x: 0, y: 0, width: 1376, height: 2064), right = CGRect(x: 1376, y: 0, width: 1376, height: 2064)
        // Taken with the reader on the left and Lunaschal on the right.
        let screenshot = try XCTUnwrap(picture(screenPixels) { drawApp($0, left, lines: true); drawApp($0, right, lines: false) })
        let ours = try XCTUnwrap(picture(CGSize(width: 172, height: 258)) { drawApp($0, CGRect(x: 0, y: 0, width: 172, height: 258), lines: false) })
        let onRight = CGRect(x: 688, y: 0, width: 688, height: 1032), onLeft = CGRect(x: 0, y: 0, width: 688, height: 1032)

        XCTAssertFalse(NotebookCrop.sidesSwapped(screenshot, snapshot: ours, screen: landscape, window: onRight))
        let kept = NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: onRight, scale: 2, snapshot: ours)
        XCTAssertEqual(kept.outcome, .cropped)
        XCTAssertEqual(kept.image.width, 1376)
        // Lunaschal has since moved to the left: the old screenshot's left
        // half is the reader, so nothing is cut.
        XCTAssertTrue(NotebookCrop.sidesSwapped(screenshot, snapshot: ours, screen: landscape, window: onLeft))
        XCTAssertEqual(NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: onLeft, scale: 2, snapshot: ours).outcome,
                       .sidesSwapped)
        // Two blank halves can't be told apart: that's no reason not to cut.
        let blank = try XCTUnwrap(solid(.white, screenPixels))
        let blankOurs = try XCTUnwrap(solid(.white, CGSize(width: 172, height: 258)))
        XCTAssertEqual(NotebookCrop.cropIfScreenshot(blank, screen: landscape, window: onRight, scale: 2, snapshot: blankOurs).outcome,
                       .cropped)
        // A snapshot that resembles neither half (PaperKit's canvas drawn
        // blank, the tool picker missing) doesn't stop the cut either.
        let unlike = try XCTUnwrap(solid(.black, CGSize(width: 172, height: 258)))
        XCTAssertEqual(NotebookCrop.cropIfScreenshot(screenshot, screen: landscape, window: onRight, scale: 2, snapshot: unlike).outcome,
                       .cropped)
    }

    func testCropKeepsTheWholeImageWhenTheShapeDisagrees() throws {
        let image = try XCTUnwrap(solid(.red, CGSize(width: 750, height: 1000)))
        let cropped = NotebookCrop.crop(image, screen: landscape, window: CGRect(x: 688, y: 0, width: 688, height: 1032))
        XCTAssertEqual(cropped.width, 750, "a portrait screenshot of a landscape screen is left whole")
        let wide = try XCTUnwrap(solid(.red, CGSize(width: 1376, height: 1032)))
        XCTAssertEqual(NotebookCrop.crop(wide, screen: landscape, window: CGRect(x: 688, y: 0, width: 688, height: 1032)).width, 688)
    }
}

/// A stand-in app, drawn into `rect` of a picture: a light page with dark
/// bars across it (`lines`), or our notebook (a top bar and a blank page).
private func drawApp(_ context: UIGraphicsImageRendererContext, _ rect: CGRect, lines: Bool) {
    UIColor.white.setFill()
    context.fill(rect)
    UIColor.darkGray.setFill()
    if lines {
        var y = rect.minY + rect.height * 0.08
        while y < rect.maxY - 20 {
            context.fill(CGRect(x: rect.minX + rect.width * 0.08, y: y, width: rect.width * 0.8, height: rect.height * 0.02))
            y += rect.height * 0.06
        }
    } else {
        context.fill(CGRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height * 0.08))
        context.fill(CGRect(x: rect.minX + rect.width * 0.1, y: rect.maxY - rect.height * 0.1,
                            width: rect.width * 0.8, height: rect.height * 0.06))
    }
}

func picture(_ size: CGSize, draw: (UIGraphicsImageRendererContext) -> Void) -> CGImage? {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    return UIGraphicsImageRenderer(size: size, format: format).image(actions: draw).cgImage
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
        // One scroll: the four issue pages stacked in a single markup, each at
        // the column's width in its own shape.
        XCTAssertTrue(model.isColumn)
        XCTAssertEqual(model.pageCount, 4)
        XCTAssertEqual(model.pages.count, 1)
        XCTAssertEqual(model.pageRect(0).size, CGSize(width: 1240, height: 1550))
        XCTAssertEqual(model.pages[0].bounds.size, CGSize(width: 1240, height: 1550 * 4))
        XCTAssertEqual(model.pageLabel, "p. 1 / 4")
        XCTAssertFalse(model.canDeleteCurrent, "issue pages can't be deleted")
        model.go(to: 2)
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 400, height: 300))))
        model.addPage()
        XCTAssertEqual(model.pageCount, 5)
        XCTAssertFalse(model.canDeleteCurrent, "ink can't be cut out of a column, so no page of one can go")
        XCTAssertEqual(model.pageRect(4), CGRect(x: 0, y: 1550 * 4, width: 1240, height: NotebookPage.blank.height))

        let files = await model.renderForSave()
        XCTAssertEqual(files.map(\.name), ["Toronto Star 2026-10-06 p1.jpg", "Toronto Star 2026-10-06 p3.jpg"])
        XCTAssertEqual(model.entryText, "Toronto Star, 2026-10-06")
        // The newspaper is drawn under the ink: the black band at the page's foot.
        let cover = try XCTUnwrap(UIImage(data: files[0].data)?.cgImage)
        XCTAssertLessThan(pixel(cover, x: cover.width / 2, y: cover.height - 20).r, 60)
        XCTAssertGreaterThan(pixel(cover, x: cover.width / 2, y: cover.height / 2).r, 200)
    }

    func testLockedPicturesLeaveTheCanvasButStayOnThePage() async throws {
        let store = try store()
        let notebook = try store.create()
        let model = NotebookEditorModel(store: store, notebook: notebook)
        XCTAssertFalse(model.currentHasPictures)
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 1000, height: 300))))
        XCTAssertTrue(model.currentHasPictures)
        let frame = model.pages[0].contentsRenderFrame

        await model.lockPictures()
        XCTAssertTrue(model.currentIsLocked)
        XCTAssertFalse(model.currentHasPictures)
        XCTAssertTrue(NotebookPage.isBlank(model.pages[0]), "nothing left on the canvas to drag")
        XCTAssertEqual(model.lockedLayer(0)?.contentsRenderFrame, frame)
        // Still drawn, and still filed.
        let files = await model.renderForSave()
        XCTAssertEqual(files.count, 1)
        let image = try XCTUnwrap(UIImage(data: files[0].data)?.cgImage)
        XCTAssertGreaterThan(pixel(image, x: image.width / 2, y: 80).r, 200)
        // A new picture goes below the locked one, and can be locked with it.
        model.insertScreenshot(try XCTUnwrap(solid(.blue, CGSize(width: 1000, height: 300))))
        XCTAssertGreaterThan(model.pages[0].contentsRenderFrame.minY, frame.maxY)
        await model.lockPictures()
        XCTAssertGreaterThan(try XCTUnwrap(model.lockedLayer(0)).contentsRenderFrame.maxY, frame.maxY + 300)

        // Survives a reopen.
        await model.checkpoint()
        let reopened = NotebookEditorModel(store: store, notebook: try store.notebook(notebook.id))
        XCTAssertTrue(reopened.currentIsLocked)
        XCTAssertTrue(NotebookPage.isBlank(reopened.pages[0]))

        await reopened.unlockPictures()
        XCTAssertFalse(reopened.currentIsLocked)
        XCTAssertTrue(reopened.currentHasPictures)
        XCTAssertFalse(NotebookPage.isBlank(reopened.pages[0]))
        await reopened.checkpoint()
        XCTAssertEqual(try store.lockedLayers(store.notebook(notebook.id)), [nil])
    }

    func testLockAndUnlockCanBeRepeatedWithoutLosingAnything() async throws {
        let store = try store()
        let notebook = try store.create()
        var model = NotebookEditorModel(store: store, notebook: notebook)
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 1000, height: 300))))
        let frame = model.pages[0].contentsRenderFrame
        for _ in 0..<3 {
            await model.lockPictures()
            XCTAssertTrue(model.currentIsLocked)
            await model.checkpoint()
            model = NotebookEditorModel(store: store, notebook: try store.notebook(notebook.id))
            await model.unlockPictures()
            XCTAssertFalse(model.currentIsLocked)
            XCTAssertEqual(model.pages[0].contentsRenderFrame, frame)
            await model.checkpoint()
            model = NotebookEditorModel(store: store, notebook: try store.notebook(notebook.id))
            XCTAssertEqual(model.pages[0].contentsRenderFrame, frame, "the pictures survive a reload")
        }
    }

    func testLockingKeepsInkAndLocksPerPage() async throws {
        let store = try store()
        let model = NotebookEditorModel(store: store, notebook: try store.create())
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 600, height: 300))))
        model.addPage()
        model.insertScreenshot(try XCTUnwrap(solid(.red, CGSize(width: 600, height: 300))))
        model.go(to: 0)
        await model.lockPictures()
        XCTAssertTrue(model.currentIsLocked)
        model.go(to: 1)
        XCTAssertFalse(model.currentIsLocked, "only the page it was locked on")
        XCTAssertTrue(model.currentHasPictures)
        model.deleteCurrentPage()
        XCTAssertEqual(model.locked.count, 1)
        XCTAssertTrue(model.currentIsLocked)
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

@MainActor
final class NotebookColumnConversionTests: XCTestCase {
    private func store() throws -> NotebookStore {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return try NotebookStore(root: root)
    }

    /// An issue as the previous build left it: paged, each markup the shape
    /// of its PDF page, with something written on page 2.
    private func pagedIssue(_ store: NotebookStore) async throws -> Notebook {
        let pdfURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).pdf")
        try NewspaperFixture.makeIssue(at: pdfURL)
        let count = NewspaperFixture.shapes.count
        var paper = try store.createNewspaper(date: "2026-10-06", pdf: pdfURL, pageCount: count)
        paper.layout = nil
        try JSONEncoder().encode(paper).write(to: store.root.appendingPathComponent(paper.id + ".json"))
        let pdf = try XCTUnwrap(PDFDocument(url: XCTUnwrap(store.pdfURL(paper))))
        var pages: [Data] = []
        for index in 0..<count {
            var page = PaperMarkup(bounds: NotebookPage.bounds(for: pdf.page(at: index)))
            if index == 1 {
                page.insertNewShape(configuration: ShapeConfiguration(type: .rectangle), frame: CGRect(x: 200, y: 300, width: 400, height: 200))
            }
            pages.append(try await page.dataRepresentation())
        }
        return try store.checkpoint(paper.id, pages: pages, marked: [1], preview: Data([1]))
    }

    func testAPagedIssueOpensAsAColumnWithItsInkStillOnPageTwo() async throws {
        let store = try store()
        let paged = try await pagedIssue(store)
        XCTAssertFalse(paged.isColumn)
        let model = NotebookEditorModel(store: store, notebook: paged)
        XCTAssertTrue(model.isColumn)
        XCTAssertEqual(model.pageCount, NewspaperFixture.shapes.count)
        XCTAssertEqual(model.pages.count, 1, "one markup for the whole issue")
        XCTAssertTrue(model.pages[0].contentsRenderFrame.intersects(model.pageRect(1)),
                      "the mark moved down onto page 2")
        XCTAssertFalse(model.pages[0].contentsRenderFrame.intersects(model.pageRect(0)))
        await model.checkpoint()
        let saved = try store.notebook(paged.id)
        XCTAssertTrue(saved.isColumn)
        XCTAssertEqual(saved.pageCount, NewspaperFixture.shapes.count)
        XCTAssertEqual(saved.markedPages, [1], "page 2's mark came across with it")
    }

    func testWritingOnAPageFilesItAndRubbingItOutStopsFilingIt() async throws {
        let store = try store()
        let model = NotebookEditorModel(store: store, notebook: try await pagedIssue(store))
        await model.checkpoint()
        // Written on page 3, with page 3 on screen.
        model.go(to: 2)
        var written = model.pages[0]
        written.insertNewShape(configuration: ShapeConfiguration(type: .ellipse),
                               frame: model.pageRect(2).insetBy(dx: 400, dy: 200))
        model.canvasChanged(written, page: 0)
        await model.checkpoint()
        XCTAssertEqual(try store.notebook(model.notebook.id).markedPages, [1, 2])
        // Page 2's mark erased while page 2 is on screen: it stops being filed.
        model.go(to: 1)
        var erased = PaperMarkup(bounds: written.bounds)
        erased.insertNewShape(configuration: ShapeConfiguration(type: .ellipse),
                              frame: model.pageRect(2).insetBy(dx: 400, dy: 200))
        model.canvasChanged(erased, page: 0)
        await model.checkpoint()
        XCTAssertEqual(try store.notebook(model.notebook.id).markedPages, [2])
    }

    func testPagesStackAtFullWidthInTheirOwnShapes() async throws {
        let store = try store()
        let model = NotebookEditorModel(store: store, notebook: try await pagedIssue(store))
        var top: CGFloat = 0
        for (index, ratio) in NewspaperFixture.shapes.enumerated() {
            let slot = model.pageRect(index)
            XCTAssertEqual(slot.minY, top, "page \(index + 1) starts where the last one ended")
            XCTAssertEqual(slot.width, NotebookColumn.width)
            XCTAssertEqual(slot.height / slot.width, ratio, accuracy: 0.01, "page \(index + 1) keeps its shape")
            top = slot.maxY
            let rendered = await model.render(index, width: 600)
            let image = try XCTUnwrap(rendered)
            XCTAssertEqual(Double(image.height) / Double(image.width), Double(ratio), accuracy: 0.01)
        }
        XCTAssertEqual(model.pages[0].bounds.height, top)
    }

    func testANewIssueStartsAsAColumn() throws {
        let store = try store()
        let pdfURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).pdf")
        try NewspaperFixture.makeIssue(at: pdfURL)
        let paper = try store.createNewspaper(date: "2026-10-06", pdf: pdfURL, pageCount: NewspaperFixture.shapes.count)
        let model = NotebookEditorModel(store: store, notebook: paper)
        XCTAssertTrue(model.isColumn)
        XCTAssertEqual(model.pages.first?.bounds.width, NotebookColumn.width)
        XCTAssertEqual(model.pages.first?.bounds.height, model.pageRect(NewspaperFixture.shapes.count - 1).maxY)
        XCTAssertFalse(model.canDeleteCurrent)
    }

    /// The ink lands in the same place on the page whatever size the picture
    /// is: a 400-pixel preview used to show the page's top-left corner, and a
    /// 2000-pixel journal picture its ink shrunk into the corner.
    func testInkScalesWithThePicture() async throws {
        let store = try store()
        let model = NotebookEditorModel(store: store, notebook: try store.create())
        var page = model.pages[0]
        // A mark in the bottom-right quarter of an A4 page.
        let mark = CGRect(x: 900, y: 1400, width: 200, height: 200)
        page.insertNewShape(configuration: ShapeConfiguration(type: .rectangle), frame: mark)
        model.canvasChanged(page, page: 0)
        for width: CGFloat in [400, 1240, 2000] {
            let rendered = await model.render(0, width: width)
            let image = try XCTUnwrap(rendered)
            let scale = CGFloat(image.width) / NotebookPage.width
            let centre = CGPoint(x: mark.midX * scale, y: mark.midY * scale)
            let corner = CGPoint(x: 20 * scale, y: 20 * scale)
            XCTAssertTrue(isDark(image, at: centre), "the mark is where it was drawn at \(width)")
            XCTAssertFalse(isDark(image, at: corner), "and not shrunk into the corner at \(width)")
        }
    }

    /// Pencil ink on every page of a column lands where it was written in the
    /// picture of that page, at the preview's size, the page's own and the
    /// journal's. On an iPad the journal pictures had it shrunk towards the
    /// top-left corner, as if written on a smaller page.
    func testPencilInkLandsWhereItWasWrittenOnEveryPageAtEverySize() async throws {
        let store = try store()
        let pdfURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).pdf")
        try NewspaperFixture.makeIssue(at: pdfURL)
        let paper = try store.createNewspaper(date: "2026-10-06", pdf: pdfURL, pageCount: NewspaperFixture.shapes.count)
        let model = NotebookEditorModel(store: store, notebook: paper)
        let pages = 0..<NewspaperFixture.shapes.count
        let widths: [CGFloat] = [400, 1240, 2000]
        var blank: [CGFloat: [CGImage]] = [:]
        for width in widths {
            for index in pages {
                let rendered = await model.render(index, width: width)
                blank[width, default: []].append(try XCTUnwrap(rendered))
            }
        }
        // A box in the lower right of each page, where shrinking shows.
        let marks = pages.map { index -> CGRect in
            let slot = model.pageRect(index)
            return CGRect(x: 900, y: slot.minY + slot.height * 0.6, width: 200, height: 100)
        }
        var written = model.pages[0]
        written.append(contentsOf: PKDrawing(strokes: marks.map(box)))
        model.canvasChanged(written, page: 0)
        let stored = model.pages[0].contentsRenderFrame

        for width in widths {
            for index in pages {
                let rendered = await model.render(index, width: width)
                let found = try XCTUnwrap(changed(from: blank[width]![index], to: XCTUnwrap(rendered)),
                                          "page \(index + 1) has its ink at \(width)")
                let slot = model.pageRect(index)
                let expected = marks[index].applying(NotebookEditorModel.pixelTransform(region: slot, width: width))
                // The pen's width, either side of the line.
                let slack = 8 * width / NotebookColumn.width + 2
                XCTAssertEqual(found.minX, expected.minX, accuracy: slack, "page \(index + 1) at \(width)")
                XCTAssertEqual(found.minY, expected.minY, accuracy: slack, "page \(index + 1) at \(width)")
                XCTAssertEqual(found.maxX, expected.maxX, accuracy: slack, "page \(index + 1) at \(width)")
                XCTAssertEqual(found.maxY, expected.maxY, accuracy: slack, "page \(index + 1) at \(width)")
            }
        }
        XCTAssertEqual(model.pages[0].contentsRenderFrame, stored, "drawing a picture doesn't move the ink")
        await model.checkpoint()
        XCTAssertEqual(model.marked, Set(pages), "every page counts as written on")
    }

    private func box(_ rect: CGRect) -> PKStroke {
        let corners = [CGPoint(x: rect.minX, y: rect.minY), CGPoint(x: rect.maxX, y: rect.minY),
                       CGPoint(x: rect.maxX, y: rect.maxY), CGPoint(x: rect.minX, y: rect.maxY)]
        let points = corners.enumerated().map { index, corner in
            PKStrokePoint(location: corner, timeOffset: Double(index) * 0.1, size: CGSize(width: 6, height: 6),
                          opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        return PKStroke(ink: PKInk(.pen, color: .black), path: PKStrokePath(controlPoints: points, creationDate: Date()))
    }

    /// The pixels that differ between two pictures of the same size, or nil.
    private func changed(from before: CGImage, to after: CGImage) -> CGRect? {
        let a = CFDataGetBytePtr(before.dataProvider!.data!)!, b = CFDataGetBytePtr(after.dataProvider!.data!)!
        var minX = Int.max, minY = Int.max, maxX = -1, maxY = -1
        for y in 0..<after.height {
            for x in 0..<after.width {
                let o = y * after.bytesPerRow + x * 4
                let difference = abs(Int(a[o]) - Int(b[o])) + abs(Int(a[o + 1]) - Int(b[o + 1])) + abs(Int(a[o + 2]) - Int(b[o + 2]))
                if difference > 30 { minX = min(minX, x); minY = min(minY, y); maxX = max(maxX, x); maxY = max(maxY, y) }
            }
        }
        guard maxX >= 0 else { return nil }
        return CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
    }

    private func isDark(_ image: CGImage, at point: CGPoint) -> Bool {
        let bytes = CFDataGetBytePtr(image.dataProvider!.data!)!
        let offset = Int(point.y) * image.bytesPerRow + Int(point.x) * 4
        return bytes[offset] < 80 && bytes[offset + 1] < 80 && bytes[offset + 2] < 80
    }
}
