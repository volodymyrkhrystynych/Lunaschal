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
    /// Not published: every stroke changes it, and publishing it re-ran the
    /// whole editor's SwiftUI body (toolbar, page menu) once per stroke.
    /// Changes the screen must show bump `revision` or `current` instead.
    private(set) var pages: [PaperMarkup] = []
    /// Who each notes page is, parallel to `pages`, for as long as the editor
    /// is open: unlike `pageIDs` it survives the page's content changing, so
    /// the canvas can keep one PaperKit canvas per page and never load one
    /// page into another's.
    private(set) var pageKeys: [UUID] = []
    @Published private(set) var current = 0
    @Published private(set) var marked: Set<Int> = []
    /// Each page's locked pictures, serialized; nil where a page has none.
    @Published private(set) var locked: [Data?] = []
    /// Whether the current page has pictures that can still be moved.
    @Published private(set) var currentHasPictures = false
    @Published var status = "Saved on this device"
    @Published var error: String?
    /// A passing word about the last paste. Not `status`, which the save that
    /// follows every edit overwrites within seconds.
    @Published var notice: String?
    private var noticeTask: Task<Void, Never>?
    /// Bumped when the pages change from outside the canvas (a screenshot, a
    /// page added or removed, a restore), so the surface reloads its markup.
    @Published private(set) var revision = 0
    /// Whether a pinch zooms the page. Off each time a notebook opens: a palm
    /// the Pencil's palm rejection misses lands as a pinch and zooms the page
    /// out from under the nib, and zooming is rarely wanted.
    @Published var zoomEnabled = false
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
    /// Who each markup is, parallel to `pages`: a page gets a new identity
    /// whenever its content changes, and keeps it through pages being added
    /// or deleted around it. `onDisk` says which file of the current
    /// checkpoint holds each identity unchanged, so a checkpoint encodes and
    /// writes only the pages touched since the last one and links the rest.
    private var pageIDs: [UUID] = []
    private var onDisk: [UUID: Int] = [:]
    /// The same for the locked layers, parallel to `locked`.
    private var lockedIDs: [UUID] = []
    private var lockedOnDisk: [UUID: Int] = [:]
    /// Decoded locked layers, so drawing a page doesn't parse its pictures again.
    private var decodedLocked: [UUID: PaperMarkup] = [:]
    /// What the current preview was drawn from: the first page and its locked layer.
    private var previewOf: [UUID]?
    /// A column's ink was saved by an autosave, which leaves which pages are
    /// written on as it was: the next final checkpoint must look again even
    /// with nothing new to write.
    private var marksBehind = false
    /// The cover's newsprint at preview size. The PDF page never changes, and
    /// rendering a broadsheet front page was most of every save's cost.
    private var coverNewsprint: (index: Int, size: CGSize, image: CGImage?)?
    private var dirty = false
    private var autosave: Task<Void, Never>?
    private var lastCheckpoint: Task<Void, Never>?
    /// How long ink waits before it is saved: often enough that a crash loses
    /// little, rarely enough that writing isn't spent re-encoding the page.
    /// Leaving the notebook or the app saves at once, whatever the timer says.
    static let autosaveInterval: Duration = .seconds(5)

    /// The ink log beside the notebooks (Settings in the notebook's menu shows it).
    let log: NotebookLog
    /// Where each page's ink came from since the last checkpoint, for the log:
    /// the canvas on screen, a page's canvas after it was turned away from, or
    /// only the read taken before a save or a page change.
    enum InkSource { case shown, turnedAway, read }
    private var arrivals: [InkSource: [Int: Int]] = [:]

    /// Editors still alive, by notebook. A notebook left with Back finishes
    /// its save in the background; reopening it straight away must pick up
    /// that editor, not read a disk the save hasn't reached yet — whose next
    /// save would then land on top of the one still under way.
    private static var live: [String: WeakEditor] = [:]

    /// The notebook's editor: the one still alive if there is one, else a new one.
    static func open(store: NotebookStore, notebook: Notebook) -> NotebookEditorModel {
        live = live.filter { $0.value.model != nil }
        if let model = live[notebook.id]?.model, model.loaded, model.store === store {
            model.zoomEnabled = false
            model.notice = nil
            return model
        }
        let model = NotebookEditorModel(store: store, notebook: notebook)
        if model.loaded { live[notebook.id] = WeakEditor(model: model) }
        return model
    }

    init(store: NotebookStore, notebook: Notebook) {
        self.store = store
        self.notebook = notebook
        log = NotebookLog(url: store.root.appendingPathComponent("ink-log.txt"))
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
                // Nothing on disk yet: the first Back must still leave a notebook to continue.
                dirty = true
            } else {
                pages = try data.map(PaperMarkup.init(dataRepresentation:))
            }
            locked = Self.padded((try? store.lockedLayers(notebook)) ?? [], to: pages.count)
            adoptDisk(fresh: data.isEmpty)
            if notebook.newspaperDate != nil && !notebook.isColumn { convertToColumn() }
            loaded = true
            refreshPictureState()
            log.record("open \(notebook.title) · \(notebook.isColumn ? "column" : "paged") · \(pageCount) pages")
        } catch {
            log.record("open \(notebook.title) failed: \(error.localizedDescription)")
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
        locked = [nil]
        pageIDs = [UUID()]
        pageKeys = [UUID()]
        lockedIDs = [UUID()]
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
        let id = lockedIDs[index]
        if let decoded = decodedLocked[id] { return decoded }
        let decoded = try? PaperMarkup(dataRepresentation: data)
        decodedLocked[id] = decoded
        return decoded
    }

    /// The pages and layers as just read from the current checkpoint: each one
    /// a new identity, and each found in its own file there — or, with nothing
    /// on disk yet, in none.
    private func adoptDisk(fresh: Bool) {
        pageIDs = pages.map { _ in UUID() }
        pageKeys = pages.map { _ in UUID() }
        lockedIDs = locked.map { _ in UUID() }
        decodedLocked = [:]
        onDisk = fresh ? [:] : Dictionary(uniqueKeysWithValues: pageIDs.enumerated().map { ($1, $0) })
        lockedOnDisk = fresh ? [:] : Dictionary(uniqueKeysWithValues: lockedIDs.enumerated()
            .filter { locked[$0.offset] != nil }.map { ($1, $0) })
        previewOf = fresh ? nil : coverIdentity
    }

    /// Page `index` changed: it is no longer what any saved file holds.
    private func touch(_ index: Int) {
        guard pageIDs.indices.contains(index) else { return }
        pageIDs[index] = UUID()
    }

    private func touchLocked(_ index: Int) {
        guard lockedIDs.indices.contains(index) else { return }
        decodedLocked[lockedIDs[index]] = nil
        lockedIDs[index] = UUID()
    }

    /// What the preview is drawn from.
    private var coverIdentity: [UUID] { [pageIDs.first, lockedIDs.first].compactMap { $0 } }

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
        syncFromCanvas()
        log.record("turn p\(current + 1) → p\(index + 1)")
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
        syncFromCanvas()
        if isColumn {
            // A blank frame at the foot of the column.
            slotCount += 1
            slots = columnSlots(count: slotCount)
            pages[0].bounds = NotebookColumn.bounds(of: slots)
            touch(0)
            current = slotCount - 1
            revision += 1
            canvas?.scroll(toSlot: current)
            scheduleCheckpoint()
            return
        }
        pages.append(PaperMarkup(bounds: NotebookPage.blank))
        locked.append(nil)
        pageIDs.append(UUID())
        pageKeys.append(UUID())
        lockedIDs.append(UUID())
        log.record("add p\(pages.count) after p\(current + 1)")
        current = pages.count - 1
        refreshPictureState()
        revision += 1
        scheduleCheckpoint()
    }

    func deleteCurrentPage() {
        guard loaded, canDeleteCurrent else { return }
        syncFromCanvas()
        let index = current
        pages.remove(at: index)
        locked.remove(at: index)
        pageIDs.remove(at: index)
        pageKeys.remove(at: index)
        lockedIDs.remove(at: index)
        marked = Set(marked.compactMap { $0 == index ? nil : ($0 > index ? $0 - 1 : $0) })
        current = min(index, pages.count - 1)
        refreshPictureState()
        revision += 1
        scheduleCheckpoint()
    }

    /// What every page's canvas holds, taken into the model before a save, a
    /// page change, a lock or a pasted picture. The canvas reports ink only
    /// through its delegate, in PaperKit's own time.
    private func syncFromCanvas() { canvas?.flushInk() }

    /// From a canvas. Assigning a page's markup to the canvas can echo back
    /// unchanged, which must not mark a newspaper page as written on.
    func canvasChanged(_ markup: PaperMarkup, page: Int, source: InkSource = .shown) {
        guard loaded, pages.indices.contains(page), pages[page] != markup else { return }
        arrivals[source, default: [:]][page, default: 0] += 1
        pages[page] = markup
        touch(page)
        // A column's written-on pages are found from the ink at checkpoint.
        if !isColumn && !marked.contains(page) { marked.insert(page) }
        // Whether the page has pictures is checked at the next save, not per
        // stroke: on a newspaper that meant copying the whole issue's ink.
        scheduleCheckpoint()
    }

    // MARK: Locking pictures

    func lockPictures() async {
        guard loaded, pages.indices.contains(markupIndex) else { return }
        syncFromCanvas()
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
            touch(index)
            touchLocked(index)
            finishLayerChange()
            status = "Pictures on this page locked"
        } catch { self.error = "Couldn't lock the pictures. \(error.localizedDescription)" }
    }

    func unlockPictures() async {
        guard loaded, pages.indices.contains(markupIndex), let layer = lockedLayer(markupIndex) else { return }
        syncFromCanvas()
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
            touch(index)
            touchLocked(index)
            finishLayerChange()
            status = "Pictures on this page unlocked"
        } catch { self.error = "Couldn't unlock the pictures. \(error.localizedDescription)" }
    }

    private func finishLayerChange() {
        refreshPictureState()
        revision += 1
        dirty = true
        autosave?.cancel()
        autosave = nil
        Task { [weak self] in await self?.checkpoint() }
    }

    // MARK: Screenshots

    /// Scaled to fit the page, below what is already written when it fits
    /// there; PaperKit lets it be moved and resized afterwards.
    func insertScreenshot(_ image: CGImage) {
        guard loaded, pages.indices.contains(markupIndex) else { return }
        syncFromCanvas()
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
        touch(index)
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

    // MARK: For review

    /// The menu's switch: whether this notebook's Save also files its pages
    /// as a learning card to read again later.
    func setForReview(_ on: Bool) {
        do { notebook = try store.setForReview(notebook.id, on) }
        catch { self.error = error.localizedDescription }
    }

    // MARK: Saving

    /// Ink changed. Saving waits for the autosave tick rather than following
    /// every stroke: a save re-encoded the page and drew a preview, and a
    /// pause in handwriting made one every second or so.
    func scheduleCheckpoint() {
        dirty = true
        // Only on a change, or every stroke re-renders the editor around the canvas.
        if status != Self.saving { status = Self.saving }
        guard autosave == nil else { return }
        autosave = Task { [weak self] in
            do { try await Task.sleep(for: Self.autosaveInterval) } catch { return }
            self?.autosave = nil
            await self?.checkpoint(final: false)
        }
    }

    private static let saving = "Saving on device…"

    /// Saves now. Checkpoints run one after another, never interleaved.
    /// `final` is for leaving — Back, the app going to the background, Save:
    /// it also redraws the preview and works out which newspaper pages carry
    /// ink, both of which an autosave leaves as they were.
    func checkpoint(final: Bool = true) async {
        syncFromCanvas()
        autosave?.cancel()
        autosave = nil
        let previous = lastCheckpoint
        let task = Task { [weak self] in
            await previous?.value
            await self?.performCheckpoint(final: final)
        }
        lastCheckpoint = task
        await task.value
    }

    /// Leaving the app or the notebook. iOS suspends a backgrounded app within
    /// seconds, which could freeze a save halfway; asking for background time
    /// lets it finish. The checkpoint itself is no different from Back's.
    func saveBeforeLeaving() {
        // Now, while the canvases still exist: the checkpoint below runs in a
        // task, by which time Back may have let them go.
        syncFromCanvas()
        let app = UIApplication.shared
        let ticket = BackgroundTicket()
        ticket.id = app.beginBackgroundTask(withName: "Save notebook") { ticket.end() }
        // Strongly: Back lets go of the editor before this task even starts,
        // and a weak capture would find nothing left to save.
        Task {
            await checkpoint()
            ticket.end()
        }
    }

    private func performCheckpoint(final: Bool) async {
        guard loaded, dirty || (final && (previewOf != coverIdentity || marksBehind)) else { return }
        dirty = false
        if let pictures = pendingLocked {
            do {
                locked = [try await pictures.dataRepresentation()]
                touchLocked(0)
                pendingLocked = nil
            } catch { dirty = true; self.error = error.localizedDescription; return }
        }
        refreshPictureState()
        let snapshot = pages, ids = pageIDs, layers = locked, layerIDs = lockedIDs
        let column = isColumn, slots = slotCount, cover = coverIdentity
        let hasCheckpoint = notebook.checkpoint != nil
        var keep = marked
        let arrived = arrivals
        arrivals = [:]
        let started = Date()
        do {
            // Only what changed since the last checkpoint is encoded; the rest is linked.
            var files: [NotebookFile] = []
            var encoded: [Int] = []
            for (index, page) in snapshot.enumerated() {
                if let saved = onDisk[ids[index]] { files.append(.kept(saved)); continue }
                files.append(.bytes(try await page.dataRepresentation()))
                encoded.append(index)
            }
            let encoding = Date().timeIntervalSince(started)
            let lockedFiles: [NotebookFile?] = layers.enumerated().map { index, data in
                guard let data else { return nil }
                return lockedOnDisk[layerIDs[index]].map(NotebookFile.kept) ?? .bytes(data)
            }
            let scanned = final && column
            if scanned, let ink = snapshot.first {
                keep = await inkedSlots(ink, slots: slots)
                marked = keep
            }
            var preview: Data?
            if previewOf != cover || !hasCheckpoint {
                if final || !hasCheckpoint {
                    guard let image = await render(0, width: 400, markup: snapshot.first, cacheNewsprint: true),
                          let png = await Self.png(image) else { throw DrawingError.incompleteCheckpoint }
                    preview = png
                }
            }
            let id = notebook.id, store = store, marks = keep
            let drawn = preview
            // The writing itself, off the main thread; the store takes turns with
            // the main thread's renames and settings.
            let saved = try await Task.detached(priority: .utility) {
                try store.checkpoint(id, files: files, locked: lockedFiles, marked: marks, preview: drawn,
                                     column: column ? slots : nil)
            }.value
            notebook = saved
            // The new checkpoint holds the snapshot, file for file. A page
            // changed during the write already has a new identity, so it isn't here.
            onDisk = Dictionary(uniqueKeysWithValues: ids.enumerated().map { ($1, $0) })
            lockedOnDisk = Dictionary(uniqueKeysWithValues: layerIDs.enumerated()
                .filter { layers[$0.offset] != nil }.map { ($1, $0) })
            if drawn != nil { previewOf = cover }
            if column { marksBehind = !scanned }
            error = nil
            status = dirty ? Self.saving : "Saved on this device"
            var line = "\(final ? "save" : "autosave"): wrote \(encoded.map { "p\($0 + 1)" }.joined(separator: " "))"
            if encoded.isEmpty { line = "\(final ? "save" : "autosave"): nothing new" }
            line += String(format: " · encode %.0f ms · total %.0f ms", encoding * 1000, Date().timeIntervalSince(started) * 1000)
            if let shown = arrived[.shown] { line += " · reported \(NotebookLog.pages(shown))" }
            if let late = arrived[.turnedAway] { line += " · late from turned-away page \(NotebookLog.pages(late))" }
            if let read = arrived[.read] { line += " · unreported until read \(NotebookLog.pages(read))" }
            if scanned { line += " · written on \(keep.sorted().map { "p\($0 + 1)" }.joined(separator: " "))" }
            log.record(line)
        } catch {
            dirty = true
            for (source, pages) in arrived { for (page, count) in pages { arrivals[source, default: [:]][page, default: 0] += count } }
            log.record("\(final ? "save" : "autosave") failed: \(error.localizedDescription)")
            self.error = error.localizedDescription
            status = "Save failed · keep this notebook open"
        }
    }

    func restorePrevious() async {
        // A save already under way would land on top of the restore.
        autosave?.cancel()
        autosave = nil
        await lastCheckpoint?.value
        do {
            notebook = try store.restorePrevious(notebook.id)
            let data = try store.pages(notebook)
            pages = try data.map(PaperMarkup.init(dataRepresentation:))
            locked = Self.padded(try store.lockedLayers(notebook), to: pages.count)
            adoptDisk(fresh: false)
            marked = notebook.markedPages
            slotCount = max(notebook.pageCount, 1)
            slots = columnSlots(count: slotCount)
            pendingLocked = nil
            if notebook.newspaperDate != nil && !notebook.isColumn { convertToColumn() }
            current = min(current, pageCount - 1)
            refreshPictureState()
            loaded = true
            dirty = pendingLocked != nil || pageIDs.contains { onDisk[$0] == nil }
            error = nil
            revision += 1
            log.record("restored the previous saved version")
            status = "Previous saved version restored"
        } catch { self.error = error.localizedDescription }
    }

    /// One page as a picture: the newspaper page if there is one, then the
    /// markup over it. Drawn off the main thread.
    func render(_ index: Int, width: CGFloat, markup: PaperMarkup? = nil, cacheNewsprint: Bool = false) async -> CGImage? {
        let region: CGRect
        let layers: [PaperMarkup?]
        if isColumn {
            guard let ink = markup ?? pages.first, slots.indices.contains(index) else { return nil }
            region = slots[index]
            layers = [lockedLayer(0), ink]
        } else {
            guard pages.indices.contains(index) || markup != nil else { return nil }
            region = pageBounds(index)
            layers = [lockedLayer(index), markup ?? pages[index]]
        }
        let size = CGSize(width: width.rounded(), height: (region.height * width / region.width).rounded())
        let background = await newsprint(index, size: size, cache: cacheNewsprint)
        return await Self.picture(size: size, opaque: true, background: background, markups: layers, region: region)
    }

    /// The PDF page under page `index` at `size`, rendered off the main thread.
    private func newsprint(_ index: Int, size: CGSize, cache: Bool) async -> CGImage? {
        guard let page = pdfPage(index) else { return nil }
        if cache, let hit = coverNewsprint, hit.index == index, hit.size == size { return hit.image }
        let image = await Task.detached(priority: .utility) { page.thumbnail(of: size, for: .cropBox).cgImage }.value
        if cache { coverNewsprint = (index, size, image) }
        return image
    }

    /// A `size`-pixel picture of `region`: white (when opaque) with the
    /// background under the markups. Nonisolated, so it draws off the main thread.
    nonisolated static func picture(size: CGSize, opaque: Bool, background: CGImage?, markups: [PaperMarkup?],
                                    region: CGRect) async -> CGImage? {
        guard let context = CGContext(data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8,
                                      bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        let frame = CGRect(origin: .zero, size: size)
        if opaque {
            context.setFillColor(UIColor.white.cgColor)
            context.fill(frame)
        }
        if let background { context.draw(background, in: frame) }
        await draw(markups, region: region, into: context, size: size)
        return context.makeImage()
    }

    nonisolated static func png(_ image: CGImage) async -> Data? {
        await Task.detached(priority: .utility) { UIImage(cgImage: image).pngData() }.value
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
    nonisolated static func draw(_ markups: [PaperMarkup?], region: CGRect, into context: CGContext, size: CGSize) async {
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
    nonisolated static func pixelTransform(region: CGRect, width: CGFloat) -> CGAffineTransform {
        let scale = width / region.width
        return CGAffineTransform(a: scale, b: 0, c: 0, d: scale, tx: -region.minX * scale, ty: -region.minY * scale)
    }

    /// Which pages of a column carry anything — ink, pictures, text. PaperKit
    /// has no way to ask a markup what lies where, so this draws it and looks,
    /// a page at a time and only the pages under something. It used to draw
    /// the whole column at a tenth of its size, where PaperKit leaves out a
    /// fine pen or a light pencil line altogether: the page went unfiled.
    private func inkedSlots(_ markup: PaperMarkup, slots count: Int) async -> Set<Int> {
        let layers = [lockedLayer(0), markup]
        let content = layers.compactMap { $0?.contentsRenderFrame }.reduce(CGRect.null) { $0.union($1) }
        var found = Set<Int>()
        for index in NotebookColumn.slots(under: content, in: Array(slots.prefix(count))) {
            let size = CGSize(width: (slots[index].width * Self.inkScanScale).rounded(),
                              height: (slots[index].height * Self.inkScanScale).rounded())
            guard let inked = await Self.hasInk(layers, region: slots[index], size: size) else { return marked }
            if inked { found.insert(index) }
        }
        return found
    }

    /// How large a page is drawn to look for ink. Half size keeps a 1-point
    /// pen line; a tenth lost it.
    private static let inkScanScale: CGFloat = 0.5

    /// Whether anything at all was drawn in `region`.
    nonisolated private static func hasInk(_ markups: [PaperMarkup?], region: CGRect, size: CGSize) async -> Bool? {
        let width = max(1, Int(size.width)), height = max(1, Int(size.height))
        guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                      space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        await draw(markups, region: region, into: context, size: CGSize(width: width, height: height))
        guard let bytes = context.data?.assumingMemoryBound(to: UInt8.self) else { return nil }
        return (0..<width * height).contains { bytes[$0 * 4 + 3] > 0 }
    }

    /// The locked pictures alone, transparent, for the canvas to show under the
    /// ink: a page's own, or one frame's worth of a column's.
    func lockedImage(_ index: Int, width: CGFloat) async -> UIImage? {
        let region: CGRect
        let layer: PaperMarkup?
        if isColumn {
            guard slots.indices.contains(index) else { return nil }
            region = slots[index]
            layer = lockedLayer(0)
        } else {
            region = pageBounds(index)
            layer = lockedLayer(index)
        }
        guard let layer else { return nil }
        let size = CGSize(width: width.rounded(), height: (region.height * width / region.width).rounded())
        return await Self.picture(size: size, opaque: false, background: nil, markups: [layer], region: region)
            .map { UIImage(cgImage: $0) }
    }

    /// The pages a Save files, as JPEGs. A blank notebook drops empty pages;
    /// a newspaper files its cover and whatever was marked.
    func renderForSave() async -> [(data: Data, name: String)] {
        syncFromCanvas()
        var snapshot = notebook
        snapshot.pageCount = pageCount
        if isColumn, let ink = pages.first { marked = await inkedSlots(ink, slots: slotCount) }
        snapshot.markedPages = marked
        let prefix = isNewspaper ? notebook.title.replacingOccurrences(of: " · ", with: " ") : "Notes"
        var files: [(data: Data, name: String)] = []
        var blank: [Int] = [], failed: [Int] = []
        let started = Date()
        for index in snapshot.pagesToFile {
            if !isNewspaper && NotebookPage.isBlank(pages[index]) && locked[index] == nil { blank.append(index); continue }
            // Newsprint needs the extra pixels to stay legible.
            let width: CGFloat = pdfPage(index) == nil && !isColumn ? NotebookPage.width : 2000
            guard let image = await render(index, width: width),
                  let data = UIImage(cgImage: image).jpegData(compressionQuality: 0.85) else { failed.append(index); continue }
            files.append((data, "\(prefix) p\(index + 1).jpg"))
        }
        func named(_ pages: [Int]) -> String { pages.map { "p\($0 + 1)" }.joined(separator: " ") }
        var line = "entry pictures: \(files.isEmpty ? "none" : files.map { String($0.name.split(separator: " ").last ?? "") }.joined(separator: " "))"
        if !blank.isEmpty { line += " · blank, left out \(named(blank))" }
        if !failed.isEmpty { line += " · could not draw \(named(failed))" }
        log.record(line + String(format: " · %.0f ms", Date().timeIntervalSince(started) * 1000))
        return files
    }

    /// What the entry says: the paper and its date, or a notebook's own name.
    var entryText: String {
        if let date = notebook.newspaperDate { return "Toronto Star, \(date)" }
        return notebook.title == "Notes" ? "" : notebook.title
    }

    func adopt(_ saved: Notebook) { notebook = saved }
}

private struct WeakEditor {
    weak var model: NotebookEditorModel?
}

/// A background-time request, ended once: by the save finishing or by iOS
/// running out of patience, whichever comes first.
@MainActor
private final class BackgroundTicket {
    var id = UIBackgroundTaskIdentifier.invalid
    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}

// MARK: Canvas

/// PaperKit's markup view. A paged notebook shows one page at a time, each
/// page in its own canvas, turned by the toolbar's buttons. A column shows the
/// whole issue as one scroll fitted to the width, each page's newsprint
/// underneath its stretch of the column.
final class NotebookCanvasController: UIViewController, PaperMarkupViewController.Delegate {
    let model: NotebookEditorModel
    /// The canvas on screen: a column's only one, or the current notes page's.
    private(set) var paper = PaperMarkupViewController(markup: nil, supportedFeatureSet: .latest)
    /// Each notes page's canvas, by `model.pageKeys`. One canvas reloaded with
    /// every page lost writing on the iPad: a turn read the canvas and then
    /// loaded the next page into it, and whatever PaperKit had not handed over
    /// yet went with the swap. A page's canvas now keeps its page for as long
    /// as the editor is open; turning away only hides it, so ink it reports
    /// late still lands on its own page. Hidden, it draws nothing.
    private(set) var papers: [UUID: PaperMarkupViewController] = [:]
    /// The locked layer each notes canvas's background was drawn from.
    private var papersLocked: [UUID: Data] = [:]
    /// Whether viewDidLoad's canvas has been given to a page yet.
    private var firstCanvasTaken = false
    private(set) lazy var drawingToolbar = DrawingToolbar(presenter: self)
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
    /// Newsprint already rendered, so scrolling back to a page shows it again
    /// instead of rendering a broadsheet over. Bounded, and emptied by the
    /// system under memory pressure; the three pages on screen are held by
    /// their image views regardless.
    private let newsprint: NSCache<NSNumber, UIImage> = {
        let cache = NSCache<NSNumber, UIImage>()
        cache.totalCostLimit = 192 * 1024 * 1024
        return cache
    }()
    #if DEBUG
    /// What is shown, in canvas units, for UI tests to read: PaperKit's
    /// scroll view is not in the accessibility tree.
    private let visibleProbe = UIView(frame: CGRect(x: 0, y: 0, width: 1, height: 1))
    #endif
    private var disabledBackSwipes: [UIGestureRecognizer] = []

    /// SwiftUI's navigation stack. A representable's controller is not always
    /// parented under it, so `navigationController` can be nil; the responder
    /// chain from the view always reaches it.
    private var hostNavigation: UINavigationController? {
        var responder: UIResponder? = view
        while let current = responder {
            if let stack = current as? UINavigationController { return stack }
            responder = current.next
        }
        return navigationController
    }
    /// Canvases whose scroll views have been told one finger doesn't scroll.
    private var heldCanvases = Set<ObjectIdentifier>()
    /// The zoom switch as last applied to the canvas.
    private var zoomApplied = false

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
        install(paper)
        drawingToolbar.undoManagerProvider = { [weak self] in self?.paper.undoManager }
        // The system picker used to own these gestures. Our compact toolbar
        // must receive them on the canvas, even when the Pencil is away from it.
        view.addInteraction(UIPencilInteraction(delegate: drawingToolbar))
        drawingToolbar.onSelect = { [weak self] favorite in
            guard let self else { return }
            paper.becomeFirstResponder()
            if let tool = favorite.tool { paper.drawingTool = tool }
            else if favorite.kind == "ruler" { paper.isRulerActive.toggle() }
            else if favorite.kind == "insert" { showInsertMenu() }
        }
        #if DEBUG
        visibleProbe.isAccessibilityElement = true
        visibleProbe.accessibilityIdentifier = "notebook-visible-frame"
        visibleProbe.alpha = 0.02
        visibleProbe.isUserInteractionEnabled = false
        view.addSubview(visibleProbe)
        #endif
        show(page: model.current, revision: model.revision)
        view.addSubview(drawingToolbar)
        drawingToolbar.activateInitialTool()
    }

    /// One canvas, set up as a child under the toolbar.
    private func install(_ canvas: PaperMarkupViewController) {
        // The page and its ink colors stay light even when the app is dark.
        // SwiftUI can override the representable controller's traits after
        // viewDidLoad. Pin the PaperKit child itself or black ink is rendered
        // white (and white black) over our permanently white page.
        canvas.overrideUserInterfaceStyle = .light
        addChild(canvas)
        canvas.view.frame = view.bounds
        canvas.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.insertSubview(canvas.view, at: 0)
        canvas.didMove(toParent: self)
        canvas.delegate = self
        canvas.isEditable = model.loaded
        // The Pencil writes; a finger scrolls, zooms and moves pictures.
        canvas.directTouchAutomaticallyDraws = false
        // Sideways only ever means zoomed in; a scroll bar along the bottom
        // would suggest otherwise.
        if #available(iOS 26.1, *) { canvas.showsHorizontalScrollIndicator = false }
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
        holdPageStill()
        applyZoomGestures()
        // Nothing on a notes page moves sideways — the toolbar turns and adds
        // pages — and the system's swipe back from the left edge would take
        // the whole notebook away under a stray palm. The Back button stays.
        if !model.isColumn, let stack = hostNavigation {
            var backSwipes = [stack.interactivePopGestureRecognizer]
            if #available(iOS 26.0, *) { backSwipes.append(stack.interactiveContentPopGestureRecognizer) }
            for case let recognizer? in backSwipes where recognizer.isEnabled {
                recognizer.isEnabled = false
                disabledBackSwipes.append(recognizer)
            }
        }
        paper.becomeFirstResponder()
        drawingToolbar.refreshUndo()
        // Fit again after the surrounding navigation bars have settled.
        DispatchQueue.main.async { [weak self] in self?.fit() }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        for recognizer in disabledBackSwipes { recognizer.isEnabled = true }
        disabledBackSwipes = []
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        // PaperKit puts its view back on top whenever a page's markup or
        // newsprint is swapped in, which buried the toolbar under the page
        // on every page turn.
        if view.subviews.last !== drawingToolbar { view.bringSubviewToFront(drawingToolbar) }
        drawingToolbar.position()
        holdPageStill()
        // A rotation or a Split View resize refits the page being read.
        if view.bounds.size != fittedSize { fit() }
    }

    /// Hands the model whatever ink every canvas holds now. PaperKit reports
    /// changes through the delegate in its own time; the model asks for this
    /// before a save, a page change, a lock or a pasted picture.
    func flushInk() {
        guard isViewLoaded, shownRevision != -1 else { return }
        if model.isColumn {
            if let markup = paper.markup { model.canvasChanged(markup, page: 0, source: .read) }
            return
        }
        for (key, canvas) in papers {
            guard let page = model.pageKeys.firstIndex(of: key), let markup = canvas.markup else { continue }
            model.canvasChanged(markup, page: page, source: .read)
        }
    }

    func show(page: Int, revision: Int) {
        if model.isColumn { showColumn(revision: revision); return }
        guard page != shownPage || revision != shownRevision, model.pages.indices.contains(page),
              model.pageKeys.indices.contains(page) else { return }
        let pageChanged = page != shownPage
        let revised = revision != shownRevision
        if revised { dropCanvasesOfRemovedPages() }
        let key = model.pageKeys[page]
        let existing = papers[key]
        let target = existing ?? newCanvas(for: key)
        shownPage = page
        shownRevision = revision
        // A canvas is loaded from the model when it is made, and after that
        // only when the model changed its page from outside — a pasted
        // picture, a lock — which it does only to the page on screen, right
        // after reading it. Never on a turn: a canvas turned back to may hold
        // ink it hasn't reported, and loading the model's copy would erase it.
        if existing == nil || (revised && target === paper) { target.markup = model.pages[page] }
        let layer = model.locked.indices.contains(page) ? model.locked[page] : nil
        if existing == nil || layer != papersLocked[key] {
            papersLocked[key] = layer
            target.contentView = background(for: page)
        }
        if target !== paper { bringForward(target) }
        if pageChanged { fit() }
        view.setNeedsLayout()
    }

    /// A page's own canvas, made the first time the page is shown.
    private func newCanvas(for key: UUID) -> PaperMarkupViewController {
        let canvas: PaperMarkupViewController
        if !firstCanvasTaken {
            canvas = paper
            firstCanvasTaken = true
        } else {
            canvas = PaperMarkupViewController(markup: nil, supportedFeatureSet: .latest)
            install(canvas)
            canvas.view.isHidden = true
        }
        papers[key] = canvas
        return canvas
    }

    /// Shows `canvas` in place of the one on screen, with the same tool.
    private func bringForward(_ canvas: PaperMarkupViewController) {
        let ruler = paper.isRulerActive
        paper.view.isHidden = true
        canvas.view.isHidden = false
        paper = canvas
        if let tool = drawingToolbar.activeTool?.tool { canvas.drawingTool = tool }
        canvas.isRulerActive = ruler
        holdPageStill()
        applyZoomGestures()
        if view.window != nil { canvas.becomeFirstResponder() }
        drawingToolbar.refreshUndo()
    }

    /// Deleted pages, or every page after a restore: their canvases go.
    private func dropCanvasesOfRemovedPages() {
        let kept = Set(model.pageKeys)
        for (key, canvas) in papers where !kept.contains(key) {
            canvas.delegate = nil
            canvas.willMove(toParent: nil)
            canvas.view.removeFromSuperview()
            canvas.removeFromParent()
            papers[key] = nil
            papersLocked[key] = nil
            heldCanvases.remove(ObjectIdentifier(canvas))
        }
    }

    /// The notes page a canvas belongs to, if it still has one.
    private func page(of canvas: PaperMarkupViewController) -> Int? {
        guard let key = papers.first(where: { $0.value === canvas })?.key else { return nil }
        return model.pageKeys.firstIndex(of: key)
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
        paper.zoomRange = NotebookFit.zoomRange(fitted: fitted, maximum: Self.maximumZoom, enabled: model.zoomEnabled)
        paper.setContentVisibleFrame(frame, animated: animated)
        reportVisibleFrame()
    }

    /// How far past the fit a page can be zoomed: small print on a broadsheet.
    private static let maximumZoom: CGFloat = 6

    /// The menu's zoom switch. Turning it off goes back to the fit: a notes
    /// page whole again, the newspaper at full width where it was being read.
    func setZoom(_ enabled: Bool) {
        guard isViewLoaded, enabled != zoomApplied else { return }
        zoomApplied = enabled
        applyZoomGestures()
        guard view.bounds.width > 0 else { return }
        if model.isColumn {
            let fit = NotebookColumn.visibleFrame(slot: model.pageRect(model.current), view: view.bounds.size)
            show(enabled ? paper.contentVisibleFrame : NotebookFit.unzoomed(paper.contentVisibleFrame, fit: fit))
        } else if model.pages.indices.contains(shownPage) {
            let fit = NotebookFit.whole(model.pageBounds(shownPage), in: view.bounds.size)
            // On: the range opens from the fit without moving the page.
            show(enabled ? paper.contentVisibleFrame : fit)
        }
    }

    /// The zoom range alone still lets a pinch tug at the page before it
    /// bounces back; with zoom off, PaperKit's scroll views stop listening.
    private func applyZoomGestures() {
        guard isViewLoaded else { return }
        var pending: [UIView] = [paper.view]
        while let next = pending.popLast() {
            if let scroll = next as? UIScrollView {
                scroll.pinchGestureRecognizer?.isEnabled = zoomApplied
                scroll.bouncesZoom = zoomApplied
            }
            pending.append(contentsOf: next.subviews)
        }
    }

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
            if let cached = newsprint.object(forKey: index as NSNumber) {
                image.image = cached
            } else {
                // Off the main thread: a broadsheet at twice reading resolution is
                // a noticeable pause, and it would land mid-scroll.
                thumbnails.async { [weak self] in
                    // Gone off screen again before its turn: not worth rendering.
                    guard let self, DispatchQueue.main.sync(execute: { self.loadedSlots.contains(index) }) else { return }
                    // Twice the page's size, so zooming in on small print stays sharp.
                    let picture = page.thumbnail(of: CGSize(width: frame.width * 2, height: frame.height * 2), for: .cropBox)
                    DispatchQueue.main.async {
                        let pixels = picture.size.width * picture.scale * picture.size.height * picture.scale
                        self.newsprint.setObject(picture, forKey: index as NSNumber, cost: Int(pixels) * 4)
                        guard self.loadedSlots.contains(index), self.slotViews.indices.contains(index),
                              self.slotViews[index] === image else { return }
                        image.image = picture
                    }
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
        guard paperMarkupViewController === paper else { return }
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

    /// A notes page fits whole, so a finger has nothing to scroll to — and a
    /// palm the Pencil's palm rejection missed slid the page out from under
    /// the nib. One finger leaves the page where it is; two still pinch and,
    /// zoomed in, pan. PaperKit has no setting for this, so it is set on its
    /// scroll views, found in its view tree. The newspaper column is read by
    /// scrolling, and keeps one-finger scrolling.
    private func holdPageStill() {
        guard !model.isColumn, isViewLoaded, !heldCanvases.contains(ObjectIdentifier(paper)) else { return }
        var pending: [UIView] = [paper.view]
        var found = false
        while let next = pending.popLast() {
            if let scroll = next as? UIScrollView {
                scroll.panGestureRecognizer.minimumNumberOfTouches = 2
                scroll.alwaysBounceVertical = false
                scroll.alwaysBounceHorizontal = false
                found = true
            }
            pending.append(contentsOf: next.subviews)
        }
        // PaperKit builds its scroll views lazily; until they exist, look again next layout.
        if found { heldCanvases.insert(ObjectIdentifier(paper)) }
    }

    private func showInsertMenu() {
        let menu = MarkupEditViewController(supportedFeatureSet: .latest)
        menu.delegate = paper
        menu.modalPresentationStyle = .popover
        menu.popoverPresentationController?.sourceView = drawingToolbar
        menu.popoverPresentationController?.sourceRect = drawingToolbar.bounds
        present(menu, animated: true)
    }

    func paperMarkupViewControllerDidChangeMarkup(_ paperMarkupViewController: PaperMarkupViewController) {
        if paperMarkupViewController === paper { drawingToolbar.refreshUndo() }
        guard let markup = paperMarkupViewController.markup else { return }
        if model.isColumn { model.canvasChanged(markup, page: 0); return }
        guard let page = page(of: paperMarkupViewController) else { return }
        model.canvasChanged(markup, page: page, source: paperMarkupViewController === paper ? .shown : .turnedAway)
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
        controller.setZoom(model.zoomEnabled)
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
    @State private var showingLog = false

    init(owner: CaptureModel, notebook: Notebook) {
        self.owner = owner
        _model = StateObject(wrappedValue: NotebookEditorModel.open(store: owner.notebooks, notebook: notebook))
    }

    var body: some View {
        VStack(spacing: 0) {
            if let error = model.error {
                VStack(spacing: 6) {
                    Text(error).font(.footnote).foregroundStyle(.red)
                    Button("Restore previous saved version") { Task { await model.restorePrevious() } }.font(.footnote)
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
                // What goes with the entry rather than on the page. The icon
                // fills when anything here is set, so it isn't hidden away.
                Menu {
                    Button { linkText = model.notebook.youtubeURL ?? ""; editingLink = true } label: {
                        Label(model.notebook.youtubeURL == nil ? "Add YouTube video" : "YouTube video added",
                              systemImage: model.notebook.youtubeURL == nil ? "play.rectangle" : "play.rectangle.fill")
                    }
                    .accessibilityIdentifier("notebook-youtube")
                    Toggle(isOn: Binding(get: { model.notebook.isForReview }, set: { model.setForReview($0) })) {
                        Label("For review", systemImage: "graduationcap")
                    }
                    .accessibilityIdentifier("notebook-for-review")
                    Toggle(isOn: $model.zoomEnabled) {
                        Label("Zoom", systemImage: "plus.magnifyingglass")
                    }
                    .accessibilityIdentifier("notebook-zoom")
                    Button { showingLog = true } label: { Label("Ink log", systemImage: "list.bullet.rectangle") }
                        .accessibilityIdentifier("notebook-ink-log")
                } label: {
                    Label("More", systemImage: model.notebook.youtubeURL != nil || model.notebook.isForReview || model.zoomEnabled
                          ? "line.3.horizontal.circle.fill" : "line.3.horizontal")
                }
                .accessibilityIdentifier("notebook-more")
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
                    // A newspaper filed again replaces its entry; nothing to confirm.
                    if model.notebook.savedCaptureIDs.isEmpty || model.isNewspaper { save() } else { confirmResave = true }
                } label: {
                    if saving { ProgressView() } else { Text(saveTitle) }
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
        .sheet(isPresented: $showingLog) { NotebookLogSheet(log: model.log) }
        .onAppear {
            NotebookSession.shared.editor = model
            model.drainInbox()
        }
        .onDisappear {
            if NotebookSession.shared.editor === model { NotebookSession.shared.editor = nil }
            model.saveBeforeLeaving()
        }
        // Switching apps or locking the screen saves now, not at the next tick.
        .onChange(of: phase) { _, value in if value != .active { model.saveBeforeLeaving() } }
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

    /// The entry draft's notes file with the composer's words, as one entry.
    private var isDraftNotes: Bool {
        owner.isDraftNotebook(model.notebook) && model.notebook.savedCaptureIDs.isEmpty
    }

    private var saveTitle: String {
        if isDraftNotes { return "Save" }
        if model.isNewspaper && !model.notebook.savedCaptureIDs.isEmpty { return "Update entry" }
        return "Save"
    }

    private func save() {
        saving = true
        Task {
            await model.checkpoint()
            let files = await model.renderForSave()
            defer { saving = false }
            if isDraftNotes {
                guard !files.isEmpty || owner.composerHasContent || model.notebook.youtubeURL != nil else {
                    model.error = "Nothing to save yet. Write or paste something first."
                    return
                }
                if let saved = owner.saveComposerDraft(notebook: model.notebook, pages: files) {
                    model.adopt(saved)
                    dismiss()
                }
                return
            }
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

/// The ink log, newest at the bottom, to read or copy into a bug report.
private struct NotebookLogSheet: View {
    let log: NotebookLog
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            ScrollViewReader { reader in
                ScrollView {
                    Text(text.isEmpty ? "Nothing logged yet." : text)
                        .font(.footnote.monospaced())
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding()
                        .accessibilityIdentifier("notebook-ink-log-text")
                    Color.clear.frame(height: 1).id("end")
                }
                .onAppear { reader.scrollTo("end", anchor: .bottom) }
            }
            .navigationTitle("Ink log")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Clear", role: .destructive) { log.clear(); text = "" }.disabled(text.isEmpty)
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button("Copy") { UIPasteboard.general.string = text }.disabled(text.isEmpty)
                    Button("Done") { dismiss() }
                }
            }
        }
        .onAppear { text = log.text }
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
        if model.isDraftNotebook(notebook) && notebook.savedCaptureIDs.isEmpty { return "In the journal draft" }
        guard let savedAt = notebook.savedAt else { return "Not in the journal yet" }
        return "Saved to journal · \(savedAt.formatted(.dateTime.month().day()))"
    }

    private func reload() {
        do { notebooks = try model.notebooks.notebooks() } catch { model.message = error.localizedDescription }
    }
}
