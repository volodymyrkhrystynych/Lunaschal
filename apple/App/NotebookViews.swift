import SwiftUI
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
}

@MainActor
final class NotebookEditorModel: ObservableObject, NotebookScreenshotReceiver {
    @Published private(set) var notebook: Notebook
    @Published private(set) var pages: [PaperMarkup] = []
    @Published private(set) var current = 0
    @Published private(set) var marked: Set<Int> = []
    @Published var status = "Saved on this device"
    @Published var error: String?
    /// Bumped when the pages change from outside the canvas (a screenshot, a
    /// page added or removed, a restore), so the surface reloads its markup.
    @Published private(set) var revision = 0
    let store: NotebookStore
    let pdf: PDFDocument?
    weak var canvas: NotebookCanvasController?
    private(set) var loaded = false
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
        do {
            let data = try store.pages(notebook)
            if data.isEmpty {
                pages = (0..<max(notebook.pageCount, 1)).map { PaperMarkup(bounds: pageBounds($0)) }
                encoded = Array(repeating: nil, count: pages.count)
                // Nothing on disk yet: the first Back must still leave a notebook to continue.
                dirty = true
            } else {
                pages = try data.map(PaperMarkup.init(dataRepresentation:))
                encoded = data
            }
            loaded = true
        } catch {
            self.error = "Could not open this notebook. Its saved pages were kept. \(error.localizedDescription)"
            status = "Could not open notebook"
        }
        if notebook.newspaperDate != nil && pdf == nil {
            error = "This newspaper's PDF is missing from the iPad. Your ink was kept; delete the notebook and open the paper again to re-download it."
        }
    }

    var pageCount: Int { pages.count }
    var isNewspaper: Bool { notebook.newspaperDate != nil }
    var pageLabel: String { pages.isEmpty ? "" : "\(isNewspaper ? "p. " : "")\(current + 1) / \(pages.count)" }
    /// Issue pages stay; only pages added after them (or any page of a blank
    /// notebook, keeping one) can go.
    var canDeleteCurrent: Bool { pages.count > 1 && current >= notebook.pdfPageCount }

    func pdfPage(_ index: Int) -> PDFPage? {
        index < notebook.pdfPageCount ? pdf?.page(at: index) : nil
    }

    func pageBounds(_ index: Int) -> CGRect { NotebookPage.bounds(for: pdfPage(index)) }

    // MARK: Pages

    func go(to index: Int) {
        guard pages.indices.contains(index), index != current else { return }
        current = index
    }

    func addPage() {
        guard loaded else { return }
        pages.append(PaperMarkup(bounds: NotebookPage.blank))
        encoded.append(nil)
        current = pages.count - 1
        revision += 1
        scheduleCheckpoint()
    }

    func deleteCurrentPage() {
        guard loaded, canDeleteCurrent else { return }
        let index = current
        pages.remove(at: index)
        encoded.remove(at: index)
        marked = Set(marked.compactMap { $0 == index ? nil : ($0 > index ? $0 - 1 : $0) })
        current = min(index, pages.count - 1)
        revision += 1
        scheduleCheckpoint()
    }

    /// From the canvas. Assigning a page's markup to the canvas can echo back
    /// unchanged, which must not mark a newspaper page as written on.
    func canvasChanged(_ markup: PaperMarkup, page: Int) {
        guard loaded, pages.indices.contains(page), pages[page] != markup else { return }
        pages[page] = markup
        encoded[page] = nil
        marked.insert(page)
        scheduleCheckpoint()
    }

    // MARK: Screenshots

    /// Scaled to fit the page, below what is already written when it fits
    /// there; PaperKit lets it be moved and resized afterwards.
    func insertScreenshot(_ image: CGImage) {
        guard loaded, pages.indices.contains(current) else { return }
        let page = pages[current].bounds
        let margin: CGFloat = 40
        // Over a newspaper page, smaller, so it doesn't bury the article.
        let maxWidth = (page.width - 2 * margin) * (pdfPage(current) == nil ? 1 : 0.6)
        let maxHeight = page.height * 0.6
        let aspect = CGFloat(image.height) / CGFloat(max(image.width, 1))
        var size = CGSize(width: maxWidth, height: maxWidth * aspect)
        if size.height > maxHeight { size = CGSize(width: maxHeight / aspect, height: maxHeight) }
        let content = pages[current].contentsRenderFrame
        var y = margin
        if !content.isNull, !content.isEmpty, content.maxY + margin + size.height <= page.maxY - margin {
            y = content.maxY + margin
        }
        let x = pdfPage(current) == nil ? (page.width - size.width) / 2 : page.width - margin - size.width
        pages[current].insertNewImage(image, frame: CGRect(origin: CGPoint(x: x, y: y), size: size))
        encoded[current] = nil
        marked.insert(current)
        revision += 1
        scheduleCheckpoint()
    }

    func receiveScreenshot(_ image: CGImage) -> String {
        insertScreenshot(image)
        return "Added to page \(current + 1)."
    }

    func pasteImage() {
        guard let image = UIPasteboard.general.image?.cgImage else {
            error = "The clipboard has no image. Copy a screenshot first."
            return
        }
        insertScreenshot(image)
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
        let snapshot = pages, keep = marked
        do {
            var data: [Data] = []
            for (index, page) in snapshot.enumerated() {
                if index < encoded.count, let cached = encoded[index] { data.append(cached); continue }
                data.append(try await page.dataRepresentation())
            }
            guard let preview = await render(0, width: 400, markup: snapshot.first),
                  let png = UIImage(cgImage: preview).pngData() else { throw DrawingError.incompleteCheckpoint }
            notebook = try store.checkpoint(notebook.id, pages: data, marked: keep, preview: png)
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
            marked = notebook.markedPages
            current = min(current, pages.count - 1)
            loaded = true
            dirty = false
            error = nil
            revision += 1
            status = "Previous saved version restored"
        } catch { self.error = error.localizedDescription }
    }

    /// One page as a picture: the newspaper page if there is one, then the
    /// markup over it.
    func render(_ index: Int, width: CGFloat, markup: PaperMarkup? = nil) async -> CGImage? {
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
        // PaperKit draws in UIKit's top-left space.
        context.translateBy(x: 0, y: size.height)
        context.scaleBy(x: 1, y: -1)
        await (markup ?? pages[index]).draw(in: context, frame: frame)
        return context.makeImage()
    }

    /// The pages a Save files, as JPEGs. A blank notebook drops empty pages;
    /// a newspaper files its cover and whatever was marked.
    func renderForSave() async -> [(data: Data, name: String)] {
        var snapshot = notebook
        snapshot.pageCount = pages.count
        snapshot.markedPages = marked
        let prefix = isNewspaper ? notebook.title.replacingOccurrences(of: " · ", with: " ") : "Notes"
        var files: [(data: Data, name: String)] = []
        for index in snapshot.pagesToFile {
            if !isNewspaper && NotebookPage.isBlank(pages[index]) { continue }
            // Newsprint needs the extra pixels to stay legible.
            let width: CGFloat = pdfPage(index) == nil ? NotebookPage.width : 2000
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

/// PaperKit's markup view for the current page, with the newspaper page (if
/// any) drawn underneath as its content view.
final class NotebookCanvasController: UIViewController, PaperMarkupViewController.Delegate {
    let model: NotebookEditorModel
    let paper = PaperMarkupViewController(markup: nil, supportedFeatureSet: .latest)
    let picker = PKToolPicker()
    private var shownPage = -1
    private var shownRevision = -1
    private var fittedSize: CGSize = .zero

    init(model: NotebookEditorModel) {
        self.model = model
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .secondarySystemBackground
        overrideUserInterfaceStyle = .light
        addChild(paper)
        paper.view.frame = view.bounds
        paper.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(paper.view)
        paper.didMove(toParent: self)
        paper.delegate = self
        paper.isEditable = model.loaded
        // The Pencil writes; a finger scrolls, zooms and moves pictures.
        paper.directTouchAutomaticallyDraws = false
        picker.addObserver(paper)
        picker.accessoryItem = UIBarButtonItem(image: UIImage(systemName: "plus.circle"),
                                               primaryAction: UIAction { [weak self] _ in self?.showInsertMenu() })
        show(page: model.current, revision: model.revision)
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        picker.setVisible(true, forFirstResponder: paper)
        paper.becomeFirstResponder()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        picker.setVisible(false, forFirstResponder: paper)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        if view.bounds.size != fittedSize { fit() }
    }

    func show(page: Int, revision: Int) {
        guard page != shownPage || revision != shownRevision, model.pages.indices.contains(page) else { return }
        let pageChanged = page != shownPage
        shownPage = page
        shownRevision = revision
        paper.markup = model.pages[page]
        if pageChanged {
            paper.contentView = background(for: page)
            fit()
        }
    }

    /// Fit the page's width to the window, top of the page showing: in half a
    /// screen a whole-page fit would make the writing too small to use.
    private func fit() {
        guard view.bounds.width > 0, model.pages.indices.contains(shownPage) else { return }
        fittedSize = view.bounds.size
        let page = model.pageBounds(shownPage)
        let height = min(page.height, page.width * view.bounds.height / view.bounds.width)
        paper.setContentVisibleFrame(CGRect(x: 0, y: 0, width: page.width, height: height), animated: false)
    }

    private func background(for index: Int) -> UIView {
        let bounds = model.pageBounds(index)
        let view = UIImageView(frame: bounds)
        view.backgroundColor = .white
        if let page = model.pdfPage(index) {
            // Twice the page width, so zooming in on small print stays sharp.
            view.image = page.thumbnail(of: CGSize(width: bounds.width * 2, height: bounds.height * 2), for: .cropBox)
            view.isAccessibilityElement = true
            view.accessibilityLabel = "Newspaper page \(index + 1)"
        }
        return view
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
        model.canvasChanged(markup, page: shownPage)
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
            NotebookCanvas(model: model)
        }
        .navigationTitle(model.notebook.title)
        .navigationSubtitle(model.status)
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
                    Button("Paste image", systemImage: "doc.on.clipboard") { model.pasteImage() }
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
                    Text("iPadOS doesn't let an app capture another app's screen, so screenshots come in through Shortcuts. Lunaschal cuts its own half out and pastes the rest onto the page you're on.")
                }
                Section("Set it up once") {
                    Label("Open Shortcuts and make a new shortcut.", systemImage: "1.circle")
                    Label("Add Take Screenshot.", systemImage: "2.circle")
                    Label("Add Lunaschal's Add Screenshot to Lunaschal Notes and pass it the screenshot.", systemImage: "3.circle")
                    Label("Bind the shortcut to Back Tap or AssistiveTouch (Settings › Accessibility › Touch), or to a keyboard shortcut.", systemImage: "4.circle")
                }
                Section {
                    Text("Running it while no notebook is open keeps the screenshot for the next one you open.")
                    if waiting > 0 { Text("\(waiting) screenshot\(waiting == 1 ? "" : "s") waiting.") }
                    Text("You can also copy any image and use Paste image.")
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
