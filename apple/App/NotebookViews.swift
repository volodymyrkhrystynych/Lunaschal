import SwiftUI
import PhotosUI
import PaperKit
import PencilKit
import PDFKit
import LunaschalCore

/// Page geometry. Blank pages are A4 at 150 dpi; a newspaper page keeps its
/// PDF page's shape at the same width, so ink lands where it was drawn.
enum NotebookPage {
    static let width: CGFloat = 1240
    static let blank = CGRect(x: 0, y: 0, width: 1240, height: 1754)

    static func bounds(for page: PDFPage?) -> CGRect {
        guard let page else { return blank }
        var box = page.bounds(for: .cropBox).size
        if page.rotation % 180 != 0 { box = CGSize(width: box.height, height: box.width) }
        guard box.width > 0, box.height > 0 else { return blank }
        return CGRect(x: 0, y: 0, width: width, height: (width * box.height / box.width).rounded())
    }

    static func isBlank(_ markup: PaperMarkup) -> Bool {
        markup.contentsRenderFrame.isNull || markup.contentsRenderFrame.isEmpty
    }

    // PaperKit has no per-item lock, but it can strip content by kind. Locking
    // moves a page's pictures into a layer drawn under the canvas, where
    // nothing can select or drag them; unlocking moves them back.
    private static let noPictures: FeatureSet = {
        var set = FeatureSet.latest
        set.remove(.images)
        return set
    }()
    private static let onlyPictures: FeatureSet = {
        var set = FeatureSet.latest
        for feature in FeatureSet.Feature.allCases where feature != .images { set.remove(feature) }
        set.inks = []
        set.shapes = []
        return set
    }()

    static func pictures(of markup: PaperMarkup) -> PaperMarkup {
        var copy = markup
        copy.removeContentUnsupported(by: onlyPictures)
        return copy
    }

    static func withoutPictures(_ markup: PaperMarkup) -> PaperMarkup {
        var copy = markup
        copy.removeContentUnsupported(by: noPictures)
        return copy
    }

    /// Two halves of one markup share references inside PaperKit, and
    /// appending one to the other crashes it. A round trip makes them strangers.
    static func detached(_ markup: PaperMarkup) async throws -> PaperMarkup {
        try PaperMarkup(dataRepresentation: await markup.dataRepresentation())
    }
}

@MainActor
final class NotebookEditorModel: ObservableObject, NotebookScreenshotReceiver {
    @Published private(set) var notebook: Notebook
    @Published private(set) var pages: [PaperMarkup] = []
    @Published private(set) var current = 0
    @Published private(set) var marked: Set<Int> = []
    /// Each page's locked pictures, serialized; nil where a page has none.
    @Published private(set) var locked: [Data?] = []
    /// Whether the current page has pictures that can still be moved.
    @Published private(set) var currentHasPictures = false
    @Published var status = "Saved on this device"
    @Published var error: String?
    /// A passing word about the last paste. Not `status`, which the save that
    /// follows every edit overwrites within a second.
    @Published var notice: String?
    private var noticeTask: Task<Void, Never>?
    /// Bumped when the pages change from outside the canvas (a screenshot, a
    /// page added or removed, a restore), so the surface reloads its markup.
    @Published private(set) var revision = 0
    let store: NotebookStore
    let pdf: PDFDocument?
    weak var canvas: NotebookCanvasController?
    private(set) var loaded = false
    /// Pages in the notebook. For a paged one, one per markup; for a column,
    /// how many frames its one markup stacks.
    @Published private(set) var slotCount = 1
    /// Where each page is in a column, top to bottom. Empty for a paged notebook.
    private(set) var slots: [CGRect] = []
    /// A converted issue's locked pictures, serialized on the next checkpoint.
    private var pendingLocked: PaperMarkup?
    /// Each page's last serialized bytes; nil once it changes, so a checkpoint
    /// of a sixty-page issue re-encodes only the pages that were touched.
    private var encoded: [Data?] = []
    private var dirty = false
    private var debounce: Task<Void, Never>?
    private var lastCheckpoint: Task<Void, Never>?

    init(store: NotebookStore, notebook: Notebook) {
        self.store = store
        self.notebook = notebook
        pdf = (try? store.pdfURL(notebook)).flatMap { $0 }.flatMap(PDFDocument.init(url:))
        marked = notebook.markedPages
        slotCount = max(notebook.pageCount, 1)
        slots = columnSlots(count: slotCount)
        do {
            let data = try store.pages(notebook)
            if data.isEmpty {
                pages = notebook.isColumn
                    ? [PaperMarkup(bounds: NotebookColumn.bounds(of: slots))]
                    : (0..<max(notebook.pageCount, 1)).map { PaperMarkup(bounds: pageBounds($0)) }
                encoded = Array(repeating: nil, count: pages.count)
                // Nothing on disk yet: the first Back must still leave a notebook to continue.
                dirty = true
            } else {
                pages = try data.map(PaperMarkup.init(dataRepresentation:))
                encoded = data
            }
            locked = Self.padded((try? store.lockedLayers(notebook)) ?? [], to: pages.count)
            if notebook.newspaperDate != nil && !notebook.isColumn { convertToColumn() }
            loaded = true
            refreshPictureState()
        } catch {
            self.error = "Could not open this notebook. Its saved pages were kept. \(error.localizedDescription)"
            status = "Could not open notebook"
        }
        if notebook.newspaperDate != nil && pdf == nil {
            error = "This newspaper's PDF is missing from the iPad. Your ink was kept; delete the notebook and open the paper again to re-download it."
        }
    }

    /// Each page's place in a column: an issue page at its own shape, a page
    /// added after the issue as a blank A4 sheet, all at the column's width.
    /// Derived from the PDF every time, never stored, so it cannot drift from
    /// the newsprint it lines the ink up with.
    private func columnSlots(count: Int) -> [CGRect] {
        NotebookColumn.slots(heights: (0..<max(count, 1)).map { pageBounds($0).height })
    }

    /// An issue opened before columns existed has one markup per page, each
    /// the shape of its PDF page at the column's width. Each is moved down to
    /// its place in the column and stacked, so the ink lands on the same
    /// newsprint it was written on. The paged original stays in the previous
    /// checkpoint.
    private func convertToColumn() {
        let count = pages.count
        slots = columnSlots(count: count)
        var column = PaperMarkup(bounds: NotebookColumn.bounds(of: slots))
        var pictures: PaperMarkup?
        for index in 0..<count {
            let transform = Self.into(slots[index], from: pages[index].bounds)
            var page = pages[index]
            page.transformContent(transform)
            column.append(contentsOf: page)
            if let layer = lockedLayer(index) {
                var moved = layer
                moved.transformContent(transform)
                if pictures == nil { pictures = PaperMarkup(bounds: column.bounds) }
                pictures?.append(contentsOf: moved)
            }
        }
        pages = [column]
        encoded = [nil]
        locked = [nil]
        pendingLocked = pictures
        notebook.layout = .column
        slotCount = count
        dirty = true
        status = "Newspaper converted to one scroll"
    }

    /// Moves a page of `bounds` onto `slot`, scaled to its width — which for
    /// a page already the column's width is a plain move down.
    private static func into(_ slot: CGRect, from bounds: CGRect) -> CGAffineTransform {
        let scale = bounds.width > 0 ? slot.width / bounds.width : 1
        return CGAffineTransform(a: scale, b: 0, c: 0, d: scale,
                                 tx: slot.minX - bounds.minX * scale, ty: slot.minY - bounds.minY * scale)
    }

    private static func padded(_ layers: [Data?], to count: Int) -> [Data?] {
        Array((layers + Array(repeating: nil, count: max(0, count - layers.count))).prefix(count))
    }

    var isColumn: Bool { notebook.isColumn }
    var pageCount: Int { isColumn ? slotCount : pages.count }
    /// The markup the current page lives in: its own, or the column's one.
    var markupIndex: Int { isColumn ? 0 : current }
    var currentIsLocked: Bool { locked.indices.contains(markupIndex) && locked[markupIndex] != nil }

    func lockedLayer(_ index: Int) -> PaperMarkup? {
        guard locked.indices.contains(index), let data = locked[index] else { return nil }
        return try? PaperMarkup(dataRepresentation: data)
    }

    /// Everything on a page, locked pictures included, for placing a new one.
    private func contentFrame(_ index: Int) -> CGRect {
        let frames = [pages[index].contentsRenderFrame, lockedLayer(index)?.contentsRenderFrame ?? .null]
            .filter { !$0.isNull && !$0.isEmpty }
        return frames.reduce(CGRect.null) { $0.union($1) }
    }

    private func refreshPictureState() {
        currentHasPictures = pages.indices.contains(markupIndex)
            && !NotebookPage.isBlank(NotebookPage.pictures(of: pages[markupIndex]))
    }
    var isNewspaper: Bool { notebook.newspaperDate != nil }
    var pageLabel: String { pages.isEmpty ? "" : "\(isNewspaper ? "p. " : "")\(current + 1) / \(pageCount)" }
    /// Issue pages stay; only pages added after them (or any page of a blank
    /// notebook, keeping one) can go. A column's pages share one markup, and
    /// ink can't be cut out of it by region, so none of its pages can.
    var canDeleteCurrent: Bool { !isColumn && pages.count > 1 && current >= notebook.pdfPageCount }

    func pdfPage(_ index: Int) -> PDFPage? {
        index < notebook.pdfPageCount ? pdf?.page(at: index) : nil
    }

    func pageBounds(_ index: Int) -> CGRect { NotebookPage.bounds(for: pdfPage(index)) }

    /// Where page `index` is in the canvas: its own bounds, or its stretch of the column.
    func pageRect(_ index: Int) -> CGRect {
        if isColumn { return slots.indices.contains(index) ? slots[index] : .zero }
        return pages.indices.contains(index) ? pages[index].bounds : pageBounds(index)
    }

    // MARK: Pages

    func go(to index: Int) {
        guard (0..<pageCount).contains(index), index != current else { return }
        current = index
        refreshPictureState()
        if isColumn { canvas?.scroll(toSlot: index) }
    }

    /// The column scrolled; the page under the middle of the screen is current.
    func scrolled(toSlot index: Int) {
        guard isColumn, index != current, (0..<pageCount).contains(index) else { return }
        current = index
    }

    func addPage() {
        guard loaded else { return }
        if isColumn {
            // A blank frame at the foot of the column.
            slotCount += 1
            slots = columnSlots(count: slotCount)
            pages[0].bounds = NotebookColumn.bounds(of: slots)
            encoded[0] = nil
            current = slotCount - 1
            revision += 1
            canvas?.scroll(toSlot: current)
            scheduleCheckpoint()
            return
        }
        pages.append(PaperMarkup(bounds: NotebookPage.blank))
        encoded.append(nil)
        locked.append(nil)
        current = pages.count - 1
        refreshPictureState()
        revision += 1
        scheduleCheckpoint()
    }

    func deleteCurrentPage() {
        guard loaded, canDeleteCurrent else { return }
        let index = current
        pages.remove(at: index)
        encoded.remove(at: index)
        locked.remove(at: index)
        marked = Set(marked.compactMap { $0 == index ? nil : ($0 > index ? $0 - 1 : $0) })
        current = min(index, pages.count - 1)
        refreshPictureState()
        revision += 1
        scheduleCheckpoint()
    }

    /// From the canvas. Assigning a page's markup to the canvas can echo back
    /// unchanged, which must not mark a newspaper page as written on.
    func canvasChanged(_ markup: PaperMarkup, page: Int) {
        guard loaded, pages.indices.contains(page), pages[page] != markup else { return }
        pages[page] = markup
        encoded[page] = nil
        // A column's written-on pages are found from the ink at checkpoint.
        if !isColumn { marked.insert(page) }
        if page == markupIndex { refreshPictureState() }
        scheduleCheckpoint()
    }

    // MARK: Locking pictures

    func lockPictures() async {
        guard loaded, pages.indices.contains(markupIndex) else { return }
        let index = markupIndex, source = pages[index]
        guard !NotebookPage.isBlank(NotebookPage.pictures(of: source)) else { return }
        do {
            var layer = try await NotebookPage.detached(NotebookPage.pictures(of: source))
            if let existing = lockedLayer(index) {
                var merged = existing
                merged.append(contentsOf: layer)
                layer = merged
            }
            let rest = try await NotebookPage.detached(NotebookPage.withoutPictures(source))
            let data = try await layer.dataRepresentation()
            // A stroke drawn while this ran would be lost by the swap; leave it alone.
            guard pages.indices.contains(index), pages[index] == source else { return }
            locked[index] = data
            pages[index] = rest
            encoded[index] = nil
            finishLayerChange()
            status = "Pictures on this page locked"
        } catch { self.error = "Couldn't lock the pictures. \(error.localizedDescription)" }
    }

    func unlockPictures() async {
        guard loaded, pages.indices.contains(markupIndex), let layer = lockedLayer(markupIndex) else { return }
        let index = markupIndex, source = pages[index]
        do {
            // Into a fresh page, ink first: the page the pictures were taken
            // out of still remembers removing them, and appending them back
            // into it crashes PaperKit; with them in first, the ink's record
            // of the removal deletes them again.
            var merged = PaperMarkup(bounds: source.bounds)
            merged.append(contentsOf: try await NotebookPage.detached(source))
            merged.append(contentsOf: layer)
            guard pages.indices.contains(index), pages[index] == source else { return }
            locked[index] = nil
            pages[index] = merged
            encoded[index] = nil
            finishLayerChange()
            status = "Pictures on this page unlocked"
        } catch { self.error = "Couldn't unlock the pictures. \(error.localizedDescription)" }
    }

    private func finishLayerChange() {
        refreshPictureState()
        revision += 1
        dirty = true
        debounce?.cancel()
        debounce = Task { [weak self] in await self?.checkpoint() }
    }

    // MARK: Screenshots

    /// Scaled to fit the page, below what is already written when it fits
    /// there; PaperKit lets it be moved and resized afterwards.
    func insertScreenshot(_ image: CGImage) {
        guard loaded, pages.indices.contains(markupIndex) else { return }
        let index = markupIndex
        let page = pageRect(current)
        let margin: CGFloat = 40
        // Over a newspaper page, smaller, so it doesn't bury the article.
        let maxWidth = (page.width - 2 * margin) * (pdfPage(current) == nil ? 1 : 0.6)
        let maxHeight = page.height * 0.6
        let aspect = CGFloat(image.height) / CGFloat(max(image.width, 1))
        var size = CGSize(width: maxWidth, height: maxWidth * aspect)
        if size.height > maxHeight { size = CGSize(width: maxHeight / aspect, height: maxHeight) }
        // Only what is on this page: a column's content frame spans every page.
        let content = contentFrame(index).intersection(page)
        var y = page.minY + margin
        if !content.isNull, !content.isEmpty, content.maxY + margin + size.height <= page.maxY - margin {
            y = content.maxY + margin
        }
        let x = page.minX + (pdfPage(current) == nil ? (page.width - size.width) / 2 : page.width - margin - size.width)
        pages[index].insertNewImage(image, frame: CGRect(origin: CGPoint(x: x, y: y), size: size))
        encoded[index] = nil
        marked.insert(current)
        refreshPictureState()
        revision += 1
        scheduleCheckpoint()
    }

    func receiveScreenshot(_ image: CGImage) -> String {
        insertScreenshot(image)
        return "Added to page \(current + 1)."
    }

    /// A screenshot copied from the system's screenshot editor (Copy and
    /// Delete) loses Lunaschal's half on the way in, the same as one from the
    /// Shortcut; any other copied picture goes in whole.
    func pasteImage() async {
        guard let pasted = UIPasteboard.general.image, let image = Self.upright(pasted) else {
            error = "The clipboard has no image. Copy a screenshot first."
            return
        }
        // Let the menu Paste was chosen from finish closing, so it isn't in
        // the snapshot of our window the screenshot is checked against.
        try? await Task.sleep(for: .milliseconds(400))
        let session = NotebookSession.shared
        guard let geometry = session.geometry() else {
            insertScreenshot(image)
            note("Pasted whole: couldn't tell where Lunaschal's window is")
            return
        }
        let result = NotebookCrop.cropIfScreenshot(image, screen: geometry.screen, window: geometry.window,
                                                   scale: geometry.scale, native: geometry.native,
                                                   snapshot: session.snapshot())
        insertScreenshot(result.image)
        switch result.outcome {
        case .cropped: note("Pasted the other app's half")
        case .notScreenshot(let width, let height): note("Pasted whole: \(width)×\(height) isn't a screenshot of this screen")
        case .fullScreen: note("Pasted whole: Lunaschal fills the screen, so there's no other half")
        case .sidesSwapped: note("Pasted whole: Lunaschal is on the other side in this screenshot")
        }
    }

    /// Pictures chosen from the photo library, each whole and in the order
    /// picked. Nothing here is cropped: a screenshot in the library was taken
    /// some other time, with no telling where Lunaschal was.
    func insertFromLibrary(_ items: [PhotosPickerItem]) async {
        var added = 0
        for item in items {
            guard let data = try? await item.loadTransferable(type: Data.self),
                  let picked = UIImage(data: data), let image = Self.upright(picked) else { continue }
            insertScreenshot(image)
            added += 1
        }
        if added < items.count { error = "Couldn't load \(items.count - added) of the chosen pictures." }
        if added > 0 { note(added == 1 ? "Inserted a picture" : "Inserted \(added) pictures") }
    }

    private func note(_ text: String) {
        notice = text
        noticeTask?.cancel()
        noticeTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(6)) } catch { return }
            self?.notice = nil
        }
    }

    /// The pixels the way they look. A pasted image can carry an orientation
    /// its raw pixels don't, and the crop works on raw pixels.
    static func upright(_ image: UIImage) -> CGImage? {
        guard image.imageOrientation != .up else { return image.cgImage }
        let format = UIGraphicsImageRendererFormat()
        format.scale = image.scale
        return UIGraphicsImageRenderer(size: image.size, format: format).image { _ in
            image.draw(at: .zero)
        }.cgImage
    }

    /// Screenshots that arrived while no notebook was open.
    func drainInbox() {
        guard loaded, let items = try? store.inboxItems(), !items.isEmpty else { return }
        var placed: [URL] = []
        for item in items {
            guard let image = UIImage(data: item.data)?.cgImage else { placed.append(item.url); continue }
            insertScreenshot(image)
            placed.append(item.url)
        }
        store.clearInbox(placed)
        status = items.count == 1 ? "Added a waiting screenshot" : "Added \(items.count) waiting screenshots"
    }

    // MARK: YouTube

    func setYouTube(_ url: String?) -> Bool {
        do {
            notebook = try store.setYouTube(notebook.id, url: url)
            return true
        } catch { self.error = error.localizedDescription; return false }
    }

    // MARK: Saving

    func scheduleCheckpoint() {
        dirty = true
        status = "Saving on device…"
        debounce?.cancel()
        debounce = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(700)) } catch { return }
            await self?.checkpoint()
        }
    }

    /// Saves now. Checkpoints run one after another, never interleaved.
    func checkpoint() async {
        debounce?.cancel()
        let previous = lastCheckpoint
        let task = Task { [weak self] in
            await previous?.value
            await self?.performCheckpoint()
        }
        lastCheckpoint = task
        await task.value
    }

    private func performCheckpoint() async {
        guard loaded, dirty else { return }
        dirty = false
        if let pictures = pendingLocked {
            do {
                locked = [try await pictures.dataRepresentation()]
                pendingLocked = nil
            } catch { dirty = true; self.error = error.localizedDescription; return }
        }
        let snapshot = pages, layers = locked, column = isColumn, slots = slotCount
        var keep = marked
        do {
            var data: [Data] = []
            for (index, page) in snapshot.enumerated() {
                if index < encoded.count, let cached = encoded[index] { data.append(cached); continue }
                data.append(try await page.dataRepresentation())
            }
            if column, let ink = snapshot.first {
                keep = await inkedSlots(ink, slots: slots)
                marked = keep
            }
            guard let preview = await render(0, width: 400, markup: snapshot.first),
                  let png = UIImage(cgImage: preview).pngData() else { throw DrawingError.incompleteCheckpoint }
            notebook = try store.checkpoint(notebook.id, pages: data, locked: layers, marked: keep, preview: png,
                                            column: column ? slots : nil)
            // Only cache what is still the page; an edit during the await stays dirty.
            if pages.count == snapshot.count {
                for index in pages.indices where pages[index] == snapshot[index] { encoded[index] = data[index] }
            }
            error = nil
            status = dirty ? "Saving on device…" : "Saved on this device"
        } catch {
            dirty = true
            self.error = error.localizedDescription
            status = "Save failed · keep this notebook open"
        }
    }

    func restorePrevious() {
        do {
            notebook = try store.restorePrevious(notebook.id)
            let data = try store.pages(notebook)
            pages = try data.map(PaperMarkup.init(dataRepresentation:))
            encoded = data
            locked = Self.padded(try store.lockedLayers(notebook), to: pages.count)
            marked = notebook.markedPages
            slotCount = max(notebook.pageCount, 1)
            slots = columnSlots(count: slotCount)
            pendingLocked = nil
            if notebook.newspaperDate != nil && !notebook.isColumn { convertToColumn() }
            current = min(current, pageCount - 1)
            refreshPictureState()
            loaded = true
            dirty = pendingLocked != nil || encoded.contains { $0 == nil }
            error = nil
            revision += 1
            status = "Previous saved version restored"
        } catch { self.error = error.localizedDescription }
    }

    /// One page as a picture: the newspaper page if there is one, then the
    /// markup over it.
    func render(_ index: Int, width: CGFloat, markup: PaperMarkup? = nil) async -> CGImage? {
        if isColumn { return await renderSlot(index, width: width, markup: markup ?? pages.first) }
        guard pages.indices.contains(index) || markup != nil else { return nil }
        let bounds = pageBounds(index)
        let size = CGSize(width: width.rounded(), height: (bounds.height * width / bounds.width).rounded())
        guard let context = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let frame = CGRect(origin: .zero, size: size)
        context.setFillColor(UIColor.white.cgColor)
        context.fill(frame)
        if let page = pdfPage(index), let background = page.thumbnail(of: size, for: .cropBox).cgImage {
            context.draw(background, in: frame)
        }
        await Self.draw([lockedLayer(index), markup ?? pages[index]], region: bounds, into: context, size: size)
        return context.makeImage()
    }

    /// Draws `region` of the markups so it fills a `size`-pixel picture.
    ///
    /// The content itself is moved into the picture's pixels, and PaperKit
    /// then draws a markup exactly the picture's size at one unit per pixel.
    /// Scaling on the context instead was right in the simulator and wrong on
    /// an iPad: there the ink came out at its own units, so a newspaper page
    /// filed at 2000 pixels had its writing shrunk to 62% towards the top-left
    /// corner, as if written on a smaller page. Nothing here asks PaperKit to
    /// honour a scale, so the two can't differ.
    static func draw(_ markups: [PaperMarkup?], region: CGRect, into context: CGContext, size: CGSize) async {
        guard region.width > 0 else { return }
        let picture = CGRect(origin: .zero, size: size)
        let toPixels = pixelTransform(region: region, width: size.width)
        context.saveGState()
        defer { context.restoreGState() }
        // PaperKit draws in UIKit's top-left space.
        context.translateBy(x: 0, y: size.height)
        context.scaleBy(x: 1, y: -1)
        context.clip(to: picture)
        for case var markup? in markups {
            markup.transformContent(toPixels)
            var sized = PaperMarkup(bounds: picture)
            sized.append(contentsOf: markup)
            await sized.draw(in: context, frame: picture)
        }
    }

    /// Canvas units in `region` to pixels of a picture `width` wide.
    static func pixelTransform(region: CGRect, width: CGFloat) -> CGAffineTransform {
        let scale = width / region.width
        return CGAffineTransform(a: scale, b: 0, c: 0, d: scale, tx: -region.minX * scale, ty: -region.minY * scale)
    }

    /// One page of the column as a picture of its own shape: the newspaper
    /// page, then the ink over it.
    private func renderSlot(_ index: Int, width: CGFloat, markup: PaperMarkup?) async -> CGImage? {
        guard let markup, slots.indices.contains(index) else { return nil }
        let slot = slots[index]
        let size = CGSize(width: width.rounded(), height: (slot.height * width / slot.width).rounded())
        guard let context = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let frame = CGRect(origin: .zero, size: size)
        context.setFillColor(UIColor.white.cgColor)
        context.fill(frame)
        if let page = pdfPage(index), let background = page.thumbnail(of: size, for: .cropBox).cgImage {
            context.draw(background, in: frame)
        }
        await Self.draw([lockedLayer(0), markup], region: slot, into: context, size: size)
        return context.makeImage()
    }

    /// Which pages of a column carry anything — ink, pictures, text — from one
    /// small drawing of the whole column. PaperKit has no way to ask a markup
    /// what lies where, so this draws it and looks.
    private func inkedSlots(_ markup: PaperMarkup, slots: Int) async -> Set<Int> {
        let width = 124
        let height = max(1, Int((markup.bounds.height * CGFloat(width) / markup.bounds.width).rounded()))
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return marked }
        await Self.draw([lockedLayer(0), markup], region: markup.bounds, into: context,
                        size: CGSize(width: width, height: height))
        guard let bytes = context.data?.assumingMemoryBound(to: UInt8.self) else { return marked }
        // The context's memory runs top row first.
        let rows = (0..<height).map { row in
            (0..<width).contains { bytes[row * width * 4 + $0 * 4 + 3] > 0 }
        }
        return NotebookColumn.slotsWithInk(rows: rows, unitsPerRow: markup.bounds.height / CGFloat(height),
                                           slots: Array(self.slots.prefix(slots)))
    }

    /// The locked pictures alone, transparent, for the canvas to show under the ink.
    func lockedImage(_ index: Int, width: CGFloat) async -> UIImage? {
        if isColumn { return await lockedSlotImage(index, width: width) }
        guard let layer = lockedLayer(index) else { return nil }
        let bounds = pageBounds(index)
        let size = CGSize(width: width.rounded(), height: (bounds.height * width / bounds.width).rounded())
        guard let context = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        await Self.draw([layer], region: bounds, into: context, size: size)
        return context.makeImage().map { UIImage(cgImage: $0) }
    }

    /// One frame's worth of a column's locked pictures.
    private func lockedSlotImage(_ index: Int, width: CGFloat) async -> UIImage? {
        guard let layer = lockedLayer(0) else { return nil }
        guard slots.indices.contains(index) else { return nil }
        let slot = slots[index]
        let size = CGSize(width: width.rounded(), height: (slot.height * width / slot.width).rounded())
        guard let context = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        await Self.draw([layer], region: slot, into: context, size: size)
        return context.makeImage().map { UIImage(cgImage: $0) }
    }

    /// The pages a Save files, as JPEGs. A blank notebook drops empty pages;
    /// a newspaper files its cover and whatever was marked.
    func renderForSave() async -> [(data: Data, name: String)] {
        var snapshot = notebook
        snapshot.pageCount = pageCount
        if isColumn, let ink = pages.first { marked = await inkedSlots(ink, slots: slotCount) }
        snapshot.markedPages = marked
        let prefix = isNewspaper ? notebook.title.replacingOccurrences(of: " · ", with: " ") : "Notes"
        var files: [(data: Data, name: String)] = []
        for index in snapshot.pagesToFile {
            if !isNewspaper && NotebookPage.isBlank(pages[index]) && locked[index] == nil { continue }
            // Newsprint needs the extra pixels to stay legible.
            let width: CGFloat = pdfPage(index) == nil && !isColumn ? NotebookPage.width : 2000
            guard let image = await render(index, width: width),
                  let data = UIImage(cgImage: image).jpegData(compressionQuality: 0.85) else { continue }
            files.append((data, "\(prefix) p\(index + 1).jpg"))
        }
        return files
    }

    /// What the entry says: the paper and its date, or a notebook's own name.
    var entryText: String {
        if let date = notebook.newspaperDate { return "Toronto Star, \(date)" }
        return notebook.title == "Notes" ? "" : notebook.title
    }

    func adopt(_ saved: Notebook) { notebook = saved }
}

// MARK: Canvas

/// PaperKit's markup view. A paged notebook shows one page at a time, the
/// newspaper page (if any) drawn underneath as its content view, and a finger
/// swipe turns it. A column shows the whole issue as one scroll fitted to the
/// width, each page's newsprint underneath its stretch of the column.
final class NotebookCanvasController: UIViewController, PaperMarkupViewController.Delegate, UIGestureRecognizerDelegate,
                                      PKToolPickerObserver {
    let model: NotebookEditorModel
    let paper = PaperMarkupViewController(markup: nil, supportedFeatureSet: .latest)
    let picker = PKToolPicker()
    private var shownPage = -1
    private var shownRevision = -1
    private var shownLocked: Data?
    /// The current page's newspaper picture, kept so a lock or a pasted
    /// screenshot doesn't re-render the PDF page.
    private var pdfImage: (page: Int, image: UIImage)?
    private var fittedSize: CGSize = .zero
    /// A column's pages, each filled with its newsprint only while it is near
    /// the screen: sixty broadsheet pages at reading resolution would not fit
    /// in memory together.
    private var slotViews: [UIImageView] = []
    private var slotLockedViews: [Int: UIImageView] = [:]
    private var loadedSlots: Set<Int> = []
    private let thumbnails = DispatchQueue(label: "notebook.newsprint", qos: .userInitiated)
    // Turning a notes page by finger.
    private let swipe = FingerDragObserver()
    #if DEBUG
    /// What is shown, in canvas units, for UI tests to read: PaperKit's
    /// scroll view is not in the accessibility tree.
    private let visibleProbe = UIView(frame: CGRect(x: 0, y: 0, width: 1, height: 1))
    #endif
    private let marker = NewPageMarker()
    private var swipeThreshold: CGFloat = PageSwipe.minimum

    init(model: NotebookEditorModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .secondarySystemBackground
        view.clipsToBounds = true
        overrideUserInterfaceStyle = .light
        addChild(paper)
        paper.view.frame = view.bounds
        paper.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(paper.view)
        paper.didMove(toParent: self)
        paper.delegate = self
        paper.isEditable = model.loaded
        // The Pencil writes; a finger scrolls, zooms and moves pictures —
        // and turns a notes page.
        paper.directTouchAutomaticallyDraws = false
        // Sideways only ever means zoomed in; a scroll bar along the bottom
        // would suggest otherwise.
        if #available(iOS 26.1, *) { paper.showsHorizontalScrollIndicator = false }
        picker.addObserver(paper)
        picker.addObserver(self)
        picker.accessoryItem = UIBarButtonItem(image: UIImage(systemName: "plus.circle"),
                                               primaryAction: UIAction { [weak self] _ in self?.showInsertMenu() })
        if !model.isColumn {
            swipe.delegate = self
            swipe.shouldTrack = { [weak self] in !(self?.isZoomedIn ?? true) }
            swipe.onBegin = { [weak self] in self?.beginSwipe() }
            swipe.onMove = { [weak self] dx in self?.moveSwipe(dx) }
            swipe.onEnd = { [weak self] dx, cancelled in self?.endSwipe(dx, cancelled: cancelled) }
            view.addGestureRecognizer(swipe)
            marker.isHidden = true
            marker.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(marker)
            NSLayoutConstraint.activate([
                marker.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -24),
                marker.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            ])
        }
        #if DEBUG
        visibleProbe.isAccessibilityElement = true
        visibleProbe.accessibilityIdentifier = "notebook-visible-frame"
        visibleProbe.alpha = 0.02
        visibleProbe.isUserInteractionEnabled = false
        view.addSubview(visibleProbe)
        #endif
        show(page: model.current, revision: model.revision)
    }

    private func reportVisibleFrame() {
        #if DEBUG
        let frame = paper.contentVisibleFrame
        visibleProbe.accessibilityValue = [frame.minX, frame.minY, frame.width, frame.height]
            .map { String(Int($0.rounded())) }.joined(separator: ",")
        #endif
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        picker.setVisible(true, forFirstResponder: paper)
        paper.becomeFirstResponder()
        // Again once the bars and the tool picker are in place: PaperKit moves
        // the content for them as they arrive, which left the top of the
        // first page hidden under the navigation bar.
        DispatchQueue.main.async { [weak self] in self?.fit() }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        picker.setVisible(false, forFirstResponder: paper)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        // A rotation or a Split View resize refits the page being read.
        if view.bounds.size != fittedSize { fit() }
    }

    func show(page: Int, revision: Int) {
        if model.isColumn { showColumn(revision: revision); return }
        guard page != shownPage || revision != shownRevision, model.pages.indices.contains(page) else { return }
        let pageChanged = page != shownPage
        let layer = model.locked.indices.contains(page) ? model.locked[page] : nil
        let layerChanged = pageChanged || layer != shownLocked
        shownPage = page
        shownRevision = revision
        shownLocked = layer
        paper.markup = model.pages[page]
        if layerChanged { paper.contentView = background(for: page) }
        if pageChanged { fit() }
    }

    /// A column's markup only changes from outside on a revision (a page
    /// added, a lock, a restore); scrolling is the canvas's own business.
    private func showColumn(revision: Int) {
        guard revision != shownRevision, let markup = model.pages.first else { return }
        let first = shownRevision == -1
        let layer = model.locked.first ?? nil
        let rebuild = first || slotViews.count != model.pageCount || layer != shownLocked
        shownRevision = revision
        shownLocked = layer
        shownPage = model.current
        paper.markup = markup
        if rebuild { paper.contentView = columnBackground() }
        if first { fit() }
        loadSlotsNear(model.current)
    }

    /// A notes page fits whole, either way up; the newspaper fills the width
    /// and scrolls down.
    private func fit() {
        guard view.bounds.width > 0 else { return }
        fittedSize = view.bounds.size
        let frame: CGRect
        if model.isColumn {
            frame = NotebookColumn.visibleFrame(slot: model.pageRect(model.current), view: view.bounds.size)
        } else {
            guard model.pages.indices.contains(shownPage) else { return }
            frame = NotebookFit.whole(model.pageBounds(shownPage), in: view.bounds.size)
        }
        show(frame)
    }

    /// PaperKit's zoom is points per canvas unit, and its range defaults to
    /// exactly 1 — at which every request to show more or less than that is
    /// ignored. The fit is the floor: zoomed out no further, nothing of the
    /// page is off screen and nothing scrolls sideways.
    private func show(_ frame: CGRect, animated: Bool = false) {
        let fitted = view.bounds.width / max(frame.width, 1)
        paper.zoomRange = fitted...(fitted * Self.maximumZoom)
        paper.setContentVisibleFrame(frame, animated: animated)
        reportVisibleFrame()
    }

    /// How far past the fit a page can be zoomed: small print on a broadsheet.
    private static let maximumZoom: CGFloat = 6

    func scroll(toSlot index: Int) {
        guard model.isColumn else { return }
        loadSlotsNear(index)
        // From wherever the reader has zoomed to, back to the fit at that page.
        show(NotebookColumn.visibleFrame(slot: model.pageRect(index), view: view.bounds.size), animated: true)
    }

    // MARK: Column

    private func columnBackground() -> UIView {
        let count = model.pageCount
        let container = UIView(frame: NotebookColumn.bounds(of: model.slots))
        container.backgroundColor = .white
        slotViews = (0..<count).map { index in
            let view = UIImageView(frame: model.pageRect(index))
            view.backgroundColor = .white
            view.contentMode = .scaleToFill
            if model.pdfPage(index) != nil {
                view.isAccessibilityElement = true
                view.accessibilityLabel = "Newspaper page \(index + 1)"
            }
            container.addSubview(view)
            return view
        }
        slotLockedViews = [:]
        loadedSlots = []
        return container
    }

    /// The page being read and one either side; anything further is let go.
    private func loadSlotsNear(_ center: Int) {
        let wanted = Set((center - 1)...(center + 1)).filter { slotViews.indices.contains($0) }
        for index in loadedSlots.subtracting(wanted) {
            slotViews[index].image = nil
            slotLockedViews[index]?.removeFromSuperview()
            slotLockedViews[index] = nil
        }
        for index in wanted.subtracting(loadedSlots) { loadSlot(index) }
        loadedSlots = wanted
    }

    private func loadSlot(_ index: Int) {
        let frame = model.pageRect(index)
        if let page = model.pdfPage(index) {
            let image = slotViews[index]
            // Off the main thread: a broadsheet at twice reading resolution is
            // a noticeable pause, and it would land mid-scroll.
            thumbnails.async { [weak self] in
                // Twice the page's size, so zooming in on small print stays sharp.
                let picture = page.thumbnail(of: CGSize(width: frame.width * 2, height: frame.height * 2), for: .cropBox)
                DispatchQueue.main.async {
                    guard let self, self.loadedSlots.contains(index), self.slotViews.indices.contains(index),
                          self.slotViews[index] === image else { return }
                    image.image = picture
                }
            }
        }
        if model.locked.first ?? nil != nil {
            let pictures = UIImageView(frame: slotViews[index].bounds)
            slotViews[index].addSubview(pictures)
            slotLockedViews[index] = pictures
            Task { [weak pictures, model] in
                pictures?.image = await model.lockedImage(index, width: frame.width * 2)
            }
        }
    }

    func paperMarkupViewControllerDidChangeContentVisibleFrame(_ paperMarkupViewController: PaperMarkupViewController) {
        reportVisibleFrame()
        guard model.isColumn else { return }
        let visible = paperMarkupViewController.contentVisibleFrame
        let index = NotebookColumn.slot(atY: visible.midY, in: model.slots)
        loadSlotsNear(index)
        model.scrolled(toSlot: index)
    }

    // MARK: Paged

    /// What sits under the ink: the newspaper page, if any, then the page's
    /// locked pictures, which can be written over but not selected.
    private func background(for index: Int) -> UIView {
        let bounds = model.pageBounds(index)
        let view = UIImageView(frame: bounds)
        view.backgroundColor = .white
        if let page = model.pdfPage(index) {
            if pdfImage?.page != index {
                // Twice the page width, so zooming in on small print stays sharp.
                pdfImage = (index, page.thumbnail(of: CGSize(width: bounds.width * 2, height: bounds.height * 2), for: .cropBox))
            }
            view.image = pdfImage?.image
            view.isAccessibilityElement = true
            view.accessibilityLabel = "Newspaper page \(index + 1)"
        }
        if model.locked.indices.contains(index), model.locked[index] != nil {
            let pictures = UIImageView(frame: view.bounds)
            pictures.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            view.addSubview(pictures)
            Task { [weak pictures, model] in
                pictures?.image = await model.lockedImage(index, width: bounds.width * 2)
            }
        }
        return view
    }

    /// Zoomed in past the whole-page fit, a sideways drag is looking around
    /// the page, not asking to turn it.
    private var isZoomedIn: Bool {
        guard model.pages.indices.contains(shownPage), view.bounds.width > 0 else { return false }
        let fitted = NotebookFit.whole(model.pageBounds(shownPage), in: view.bounds.size)
        return paper.contentVisibleFrame.width < fitted.width * 0.97
    }

    /// The observer never claims a touch, so PaperKit's own gestures carry on
    /// beside it; letting them recognize together is what keeps it fed.
    func gestureRecognizer(_ recognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }

    private func beginSwipe() {
        let page = model.pageBounds(shownPage)
        let fitted = NotebookFit.whole(page, in: view.bounds.size)
        // The page's on-screen width: a third of that is a deliberate turn.
        swipeThreshold = PageSwipe.threshold(pageWidth: view.bounds.width * page.width / max(fitted.width, 1))
    }

    private func moveSwipe(_ dx: CGFloat) {
        let preview = PageSwipe.preview(dx: dx, index: model.current, count: model.pageCount, threshold: swipeThreshold)
        paper.view.transform = CGAffineTransform(translationX: preview.offset, y: 0)
        marker.update(preview)
    }

    private func endSwipe(_ dx: CGFloat, cancelled: Bool) {
        finishSwipe(cancelled ? .stay
                    : PageSwipe.outcome(dx: dx, index: model.current, count: model.pageCount, threshold: swipeThreshold))
    }

    private func finishSwipe(_ outcome: PageSwipe.Outcome) {
        marker.update(.idle)
        let width = view.bounds.width
        let turn: (CGFloat, () -> Void)?
        switch outcome {
        case .stay: turn = nil
        case .next: turn = (-1, { [model] in model.go(to: model.current + 1) })
        case .previous: turn = (1, { [model] in model.go(to: model.current - 1) })
        case .newPage: turn = (-1, { [model] in model.addPage() })
        }
        guard let (direction, change) = turn else {
            UIView.animate(withDuration: 0.2, delay: 0, options: .curveEaseOut) { self.paper.view.transform = .identity }
            return
        }
        // Out the side it was pushed, in from the other with the new page.
        UIView.animate(withDuration: 0.16, delay: 0, options: .curveEaseIn) {
            self.paper.view.transform = CGAffineTransform(translationX: direction * width, y: 0)
        } completion: { _ in
            change()
            self.paper.view.transform = CGAffineTransform(translationX: -direction * width, y: 0)
            UIView.animate(withDuration: 0.2, delay: 0, options: .curveEaseOut) { self.paper.view.transform = .identity }
        }
    }

    /// The tool picker arriving (or moving) makes PaperKit shift the content
    /// to clear it, which left the top of the first page under the navigation
    /// bar. Put the fit back — unless the reader has already scrolled away.
    func toolPickerFramesObscuredDidChange(_ toolPicker: PKToolPicker) {
        guard view.bounds.width > 0 else { return }
        let page = model.isColumn ? model.pageRect(model.current) : model.pageBounds(shownPage)
        let fitted = model.isColumn ? NotebookColumn.visibleFrame(slot: page, view: view.bounds.size)
                                    : NotebookFit.whole(page, in: view.bounds.size)
        let shown = paper.contentVisibleFrame
        if abs(shown.minY - fitted.minY) < 80, abs(shown.width - fitted.width) < 2 { fit() }
    }

    private func showInsertMenu() {
        let menu = MarkupEditViewController(supportedFeatureSet: .latest)
        menu.delegate = paper
        menu.modalPresentationStyle = .popover
        menu.popoverPresentationController?.sourceItem = picker.accessoryItem
        present(menu, animated: true)
    }

    func paperMarkupViewControllerDidChangeMarkup(_ paperMarkupViewController: PaperMarkupViewController) {
        guard let markup = paperMarkupViewController.markup else { return }
        model.canvasChanged(markup, page: model.isColumn ? 0 : shownPage)
    }
}

/// Watches one finger drag across a notes page without ever claiming the
/// touch. A pan recognizer would compete with PaperKit's own — drawing,
/// moving a picture, scrolling — and lose to whichever claims the touch
/// first; this stays a bystander, so they keep working and it still sees the
/// whole drag. A second finger makes it a pinch, and it lets go.
final class FingerDragObserver: UIGestureRecognizer {
    /// Asked once the drag has a direction; false leaves the drag alone.
    var shouldTrack: () -> Bool = { true }
    var onBegin: () -> Void = {}
    var onMove: (CGFloat) -> Void = { _ in }
    var onEnd: (CGFloat, Bool) -> Void = { _, _ in }
    private var start: CGPoint?
    /// Nil until the finger has moved far enough to say which way it is going.
    private var sideways: Bool?
    private static let decideAfter: CGFloat = 12

    init() {
        super.init(target: nil, action: nil)
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
        allowedTouchTypes = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
    }

    override func canPrevent(_ preventedGestureRecognizer: UIGestureRecognizer) -> Bool { false }
    override func canBePrevented(by preventingGestureRecognizer: UIGestureRecognizer) -> Bool { false }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        guard start == nil, numberOfTouches == 1, let touch = touches.first else { abandon(); return }
        start = touch.location(in: view?.window)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        guard let start, numberOfTouches == 1, let touch = touches.first else { return }
        let point = touch.location(in: view?.window)
        let dx = point.x - start.x, dy = point.y - start.y
        if sideways == nil, hypot(dx, dy) >= Self.decideAfter {
            sideways = abs(dx) > abs(dy) && shouldTrack()
            if sideways == true { onBegin() }
        }
        if sideways == true { onMove(dx) }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        finish(touches, cancelled: false)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        finish(touches, cancelled: true)
    }

    private func finish(_ touches: Set<UITouch>, cancelled: Bool) {
        if sideways == true, let start, let touch = touches.first {
            onEnd(touch.location(in: view?.window).x - start.x, cancelled)
        }
        state = .failed
    }

    /// A second finger: a pinch, not a page turn.
    private func abandon() {
        if sideways == true { onEnd(0, true) }
        sideways = false
        state = .failed
    }

    override func reset() {
        start = nil
        sideways = nil
    }
}

/// Shown while a finger drags forwards off the last notes page: what lifting
/// will do, filling as the drag nears the threshold. Never takes a touch.
final class NewPageMarker: UIView {
    private let circle = UIImageView(image: UIImage(systemName: "plus.circle.fill"))
    private let label = UILabel()

    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        isAccessibilityElement = true
        accessibilityIdentifier = "notebook-new-page-marker"
        circle.preferredSymbolConfiguration = .init(pointSize: 56, weight: .regular)
        label.font = .preferredFont(forTextStyle: .footnote).withTraits(.traitBold)
        label.textAlignment = .center
        let stack = UIStackView(arrangedSubviews: [circle, label])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: topAnchor), stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor), stack.trailingAnchor.constraint(equalTo: trailingAnchor),
        ])
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    func update(_ preview: PageSwipe.Preview) {
        isHidden = !preview.creating
        guard preview.creating else { return }
        let text = preview.armed ? "Release for a new page" : "New page"
        label.text = text
        accessibilityLabel = text
        circle.tintColor = preview.armed ? .tintColor : .secondaryLabel
        alpha = 0.35 + 0.65 * preview.progress
        let scale = 0.7 + 0.3 * preview.progress
        circle.transform = CGAffineTransform(scaleX: scale, y: scale)
    }
}

private extension UIFont {
    func withTraits(_ traits: UIFontDescriptor.SymbolicTraits) -> UIFont {
        fontDescriptor.withSymbolicTraits(traits).map { UIFont(descriptor: $0, size: 0) } ?? self
    }
}

private struct NotebookCanvas: UIViewControllerRepresentable {
    @ObservedObject var model: NotebookEditorModel

    func makeUIViewController(context: Context) -> NotebookCanvasController {
        let controller = NotebookCanvasController(model: model)
        model.canvas = controller
        return controller
    }

    func updateUIViewController(_ controller: NotebookCanvasController, context: Context) {
        controller.show(page: model.current, revision: model.revision)
    }
}

// MARK: Editor

/// The full-window notebook. Back saves it (its preview shows in Draw, where
/// it can be continued); Save files it as a journal entry.
struct NotebookEditor: View {
    @ObservedObject var owner: CaptureModel
    @StateObject private var model: NotebookEditorModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var phase
    @State private var editingLink = false
    @State private var linkText = ""
    @State private var showHelp = false
    @State private var choosingPhotos = false
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var confirmResave = false
    @State private var saving = false

    init(owner: CaptureModel, notebook: Notebook) {
        self.owner = owner
        _model = StateObject(wrappedValue: NotebookEditorModel(store: owner.notebooks, notebook: notebook))
    }

    var body: some View {
        VStack(spacing: 0) {
            if let error = model.error {
                VStack(spacing: 6) {
                    Text(error).font(.footnote).foregroundStyle(.red)
                    Button("Restore previous saved version") { model.restorePrevious() }.font(.footnote)
                }
                .padding(8)
            }
            if let notice = model.notice {
                Text(notice).font(.footnote).foregroundStyle(.secondary)
                    .padding(6)
                    .accessibilityIdentifier("notebook-notice")
            }
            NotebookCanvas(model: model)
        }
        .navigationTitle(model.notebook.title)
        .navigationSubtitle(model.status)
        // Presented from the camera menu; a PhotosPicker inside a Menu
        // closes with the menu before it can open.
        .photosPicker(isPresented: $choosingPhotos, selection: $photoItems, matching: .images)
        .onChange(of: photoItems) { _, items in
            guard !items.isEmpty else { return }
            photoItems = []
            Task { await model.insertFromLibrary(items) }
        }
        .navigationBarTitleDisplayMode(.inline)
        // Full window: the iPad's floating tab bar would sit over the page.
        .toolbar(.hidden, for: .tabBar)
        .toolbar {
            // Up here rather than along the bottom, where PaperKit's floating
            // tool picker would cover them.
            ToolbarItemGroup(placement: .topBarLeading) { pageControls }
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button { linkText = model.notebook.youtubeURL ?? ""; editingLink = true } label: {
                    Label(model.notebook.youtubeURL == nil ? "Add YouTube video" : "YouTube video added",
                          systemImage: model.notebook.youtubeURL == nil ? "play.rectangle" : "play.rectangle.fill")
                }
                .accessibilityIdentifier("notebook-youtube")
                Menu {
                    Button("Paste image", systemImage: "doc.on.clipboard") { Task { await model.pasteImage() } }
                    Button("Insert from library", systemImage: "photo.on.rectangle") { choosingPhotos = true }
                        .accessibilityIdentifier("notebook-insert-photo")
                    if model.currentHasPictures {
                        Button("Lock pictures on this page", systemImage: "lock") { Task { await model.lockPictures() } }
                    }
                    if model.currentIsLocked {
                        Button("Unlock pictures on this page", systemImage: "lock.open") { Task { await model.unlockPictures() } }
                    }
                    Button("How to add screenshots…", systemImage: "questionmark.circle") { showHelp = true }
                } label: { Label("Screenshot", systemImage: "camera.viewfinder") }
                Button {
                    if model.notebook.savedCaptureIDs.isEmpty { save() } else { confirmResave = true }
                } label: {
                    if saving { ProgressView() } else { Text("Save") }
                }
                .buttonStyle(.borderedProminent)
                .disabled(saving || !model.loaded)
                .accessibilityIdentifier("notebook-save")
            }
        }
        .alert("YouTube video", isPresented: $editingLink) {
            TextField("YouTube video URL", text: $linkText)
                .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
            Button(model.notebook.youtubeURL == nil ? "Add" : "Replace") { _ = model.setYouTube(linkText) }
            if model.notebook.youtubeURL != nil {
                Button("Remove", role: .destructive) { _ = model.setYouTube(nil) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("One video goes with this notebook's journal entry.") }
        .confirmationDialog("This notebook is already in your journal.", isPresented: $confirmResave, titleVisibility: .visible) {
            Button("Save another entry") { save() }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showHelp) { ScreenshotHelp(waiting: owner.notebooks.inboxCount()) }
        .onAppear {
            NotebookSession.shared.editor = model
            model.drainInbox()
        }
        .onDisappear {
            if NotebookSession.shared.editor === model { NotebookSession.shared.editor = nil }
            Task { await model.checkpoint() }
        }
        .onChange(of: phase) { _, value in if value != .active { Task { await model.checkpoint() } } }
    }

    @ViewBuilder private var pageControls: some View {
        Button { model.go(to: model.current - 1) } label: { Label("Previous page", systemImage: "chevron.left") }
            .disabled(model.current == 0)
        Menu {
            ForEach(0..<model.pageCount, id: \.self) { index in
                Button { model.go(to: index) } label: {
                    if model.marked.contains(index) { Label("Page \(index + 1)", systemImage: "pencil") }
                    else { Text("Page \(index + 1)") }
                }
            }
            Divider()
            if model.canDeleteCurrent {
                Button("Delete this page", systemImage: "trash", role: .destructive) { model.deleteCurrentPage() }
            }
        } label: { Text(model.pageLabel).monospacedDigit() }
        .accessibilityIdentifier("notebook-page")
        if model.currentIsLocked {
            Image(systemName: "lock.fill").font(.caption).foregroundStyle(.secondary)
                .accessibilityLabel("Pictures on this page are locked")
        }
        Button { model.go(to: model.current + 1) } label: { Label("Next page", systemImage: "chevron.right") }
            .disabled(model.current >= model.pageCount - 1)
        Button { model.addPage() } label: { Label("Add page", systemImage: "plus.rectangle.portrait") }
            .accessibilityIdentifier("notebook-add-page")
    }

    private func save() {
        saving = true
        Task {
            await model.checkpoint()
            let files = await model.renderForSave()
            defer { saving = false }
            guard !files.isEmpty || model.notebook.youtubeURL != nil else {
                model.error = "Nothing to save yet. Write or paste something first."
                return
            }
            if let saved = owner.saveNotebook(model.notebook, text: model.entryText, pages: files) {
                model.adopt(saved)
                dismiss()
            }
        }
    }
}

private struct ScreenshotHelp: View {
    let waiting: Int
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("iPadOS doesn't let an app capture another app's screen, so take the screenshot yourself: Lunaschal cuts its own half out and puts the rest on the page you're on.")
                }
                Section("Without any setup") {
                    Label("Take a screenshot: swipe up from a bottom corner with the Pencil, or press the top and volume buttons.", systemImage: "1.circle")
                    Label("In the screenshot editor, choose Copy and Delete.", systemImage: "2.circle")
                    Label("Here, choose Paste image. Lunaschal's half is cut off; a copied picture that isn't a screenshot goes in whole.", systemImage: "3.circle")
                }
                Section("One tap, with a shortcut") {
                    Label("In Shortcuts, make a shortcut: Take Screenshot, then Add Screenshot to Lunaschal Notes.", systemImage: "1.circle")
                    Label("Run it from AssistiveTouch (Settings › Accessibility › Touch) or a Full Keyboard Access command.", systemImage: "2.circle")
                }
                Section {
                    Text("Running the shortcut while no notebook is open keeps the screenshot for the next one you open.")
                    if waiting > 0 { Text("\(waiting) screenshot\(waiting == 1 ? "" : "s") waiting.") }
                }
            }
            .navigationTitle("Screenshots")
            .toolbar { Button("Done") { dismiss() } }
        }
    }
}

// MARK: Draw tab

/// Notebooks in the Draw tab: where a notebook left with Back is continued.
struct NotebookSection: View {
    @ObservedObject var model: CaptureModel
    @State private var notebooks: [Notebook] = []
    @State private var deleting: Notebook?

    var body: some View {
        Section("Notebooks") {
            if notebooks.isEmpty {
                Text("Notes and newspapers opened from Capture are kept here to continue.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(notebooks) { notebook in
                NavigationLink { NotebookEditor(owner: model, notebook: notebook) } label: {
                    HStack(spacing: 12) {
                        preview(notebook)
                        VStack(alignment: .leading) {
                            Text(notebook.title)
                            Text("\(notebook.pageCount) page\(notebook.pageCount == 1 ? "" : "s") · \(notebook.updatedAt.formatted(.dateTime.month().day().hour().minute()))")
                                .font(.caption)
                            Text(status(notebook)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .swipeActions {
                    Button("Delete", role: .destructive) { deleting = notebook }
                }
            }
        }
        .onAppear { reload() }
        .confirmationDialog("Delete this notebook from the iPad?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Delete notebook", role: .destructive) {
                if let deleting {
                    do { try model.notebooks.delete(deleting.id) } catch { model.message = error.localizedDescription }
                }
                deleting = nil
                reload()
            }
        } message: { Text("Journal entries already saved from it are kept.") }
    }

    @ViewBuilder private func preview(_ notebook: Notebook) -> some View {
        if let url = try? model.notebooks.previewURL(notebook), let image = UIImage(contentsOfFile: url.path) {
            Image(uiImage: image).resizable().scaledToFit().frame(width: 44, height: 60)
                .background(.white).clipShape(RoundedRectangle(cornerRadius: 4))
                .overlay { RoundedRectangle(cornerRadius: 4).stroke(.separator) }
        } else {
            Image(systemName: notebook.newspaperDate == nil ? "doc" : "newspaper").frame(width: 44, height: 60)
        }
    }

    private func status(_ notebook: Notebook) -> String {
        guard let savedAt = notebook.savedAt else { return "Not in the journal yet" }
        return "Saved to journal · \(savedAt.formatted(.dateTime.month().day()))"
    }

    private func reload() {
        do { notebooks = try model.notebooks.notebooks() } catch { model.message = error.localizedDescription }
    }
}
