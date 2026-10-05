import SwiftUI
import PencilKit
import PDFKit
import LunaschalCore

/// One page at a time keeps long PDFs from allocating a canvas per page.
/// Page images and ink share a fixed coordinate system inside one zoom container.
@MainActor
final class StudyAnnotationModel: ObservableObject {
    @Published private(set) var pageIndex: Int
    @Published private(set) var background: UIImage?
    @Published private(set) var pageSize = CGSize(width: 1240, height: 1754)
    @Published var error: String?
    @Published var status = "Ink saved on this iPad"
    @Published var exportURL: URL?
    let canvas = PKCanvasView()
    let store: StudyAnnotationStore
    let document: PDFDocument
    private var dirty = false
    private var loaded = false
    private var pendingSave: Task<Void, Never>?

    init(file: URL, mime: String, store: StudyAnnotationStore, initialPage: Int = 0) throws {
        self.store = store
        if mime == "application/pdf", let pdf = PDFDocument(url: file), !pdf.isLocked, pdf.pageCount > 0 {
            document = pdf
        } else if mime.hasPrefix("image/"), let image = UIImage(contentsOfFile: file.path),
                  let page = PDFPage(image: image) {
            let pdf = PDFDocument()
            pdf.insert(page, at: 0)
            document = pdf
        } else {
            throw CaptureError.invalidResponse
        }
        pageIndex = min(max(0, initialPage), document.pageCount - 1)
        canvas.backgroundColor = .clear
        canvas.isOpaque = false
        canvas.drawingPolicy = .pencilOnly
        canvas.isScrollEnabled = false
        loadPage()
    }

    private func loadPage() {
        loaded = false
        exportURL = nil
        canvas.isUserInteractionEnabled = false
        do {
            guard let page = document.page(at: pageIndex) else { throw CaptureError.invalidResponse }
            var size = page.bounds(for: .cropBox).size
            if abs(page.rotation % 180) == 90 { size = CGSize(width: size.height, height: size.width) }
            guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0 else {
                throw CaptureError.invalidResponse
            }
            let scale = min(1240 / size.width, 2480 / size.height)
            pageSize = CGSize(width: size.width * scale, height: size.height * scale)
            background = page.thumbnail(of: CGSize(width: pageSize.width * 2, height: pageSize.height * 2), for: .cropBox)
            canvas.drawing = try store.ink(page: pageIndex).map { try PKDrawing(data: $0) } ?? PKDrawing()
            canvas.undoManager?.removeAllActions()
            dirty = false
            loaded = true
            error = nil
            status = "Ink saved on this iPad"
            canvas.isUserInteractionEnabled = true
        } catch {
            self.error = "Could not load this page's ink. Existing files were kept. \(error.localizedDescription)"
        }
    }

    func changed() {
        guard loaded else { return }
        dirty = true
        exportURL = nil
        status = "Saving ink…"
        pendingSave?.cancel()
        pendingSave = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
            _ = self?.save()
        }
    }

    @discardableResult
    func save() -> Bool {
        pendingSave?.cancel()
        guard loaded else { return false }
        guard dirty else { return true }
        do {
            let bounds = CGRect(origin: .zero, size: pageSize)
            guard let preview = canvas.drawing.image(from: bounds, scale: 1).pngData() else {
                throw DrawingError.incompleteCheckpoint
            }
            try store.save(page: pageIndex, native: canvas.drawing.dataRepresentation(), preview: preview)
            dirty = false
            error = nil
            status = "Ink saved on this iPad"
            return true
        } catch {
            self.error = "Ink could not be saved. Keep this page open and retry. \(error.localizedDescription)"
            return false
        }
    }

    func go(to index: Int) {
        guard index >= 0, index < document.pageCount, index != pageIndex, save() else { return }
        pageIndex = index
        loadPage()
    }

    func exportPage() {
        guard save(), let background else { return }
        do {
            let format = UIGraphicsImageRendererFormat()
            format.scale = 1
            let bounds = CGRect(origin: .zero, size: pageSize)
            let image = UIGraphicsImageRenderer(size: pageSize, format: format).image { _ in
                background.draw(in: bounds)
                canvas.drawing.image(from: bounds, scale: 1).draw(in: bounds)
            }
            guard let data = image.pngData() else { throw DrawingError.incompleteCheckpoint }
            let url = store.root.appendingPathComponent("Annotated-page-\(pageIndex + 1).png")
            try data.write(to: url, options: .atomic)
            exportURL = url
        } catch { self.error = error.localizedDescription }
    }

    func restorePrevious() {
        do {
            let drawings = try store.drawingStore(page: pageIndex)
            guard let page = try store.drawing(page: pageIndex) else { throw DrawingError.noPreviousCheckpoint }
            _ = try drawings.restorePrevious(page.id) { _ = try PKDrawing(data: $0) }
            loadPage()
        } catch { self.error = error.localizedDescription }
    }
}

struct StudyAnnotationView: View {
    @StateObject private var model: StudyAnnotationModel
    @Environment(\.scenePhase) private var phase
    let onPageChanged: (Int) -> Void

    init(model: StudyAnnotationModel, onPageChanged: @escaping (Int) -> Void) {
        _model = StateObject(wrappedValue: model)
        self.onPageChanged = onPageChanged
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button("Previous", systemImage: "chevron.left") { model.go(to: model.pageIndex - 1) }
                    .disabled(model.pageIndex == 0)
                Spacer()
                Text("Page \(model.pageIndex + 1) of \(model.document.pageCount)")
                Spacer()
                Button("Next", systemImage: "chevron.right") { model.go(to: model.pageIndex + 1) }
                    .disabled(model.pageIndex + 1 == model.document.pageCount)
            }.padding()
            Text(model.status).font(.caption)
            Text("Pencil to draw · Fingers to pan and zoom. Ink stays on this iPad.")
                .font(.caption).foregroundStyle(.secondary).padding(.bottom, 6)
            if let error = model.error {
                Text(error).foregroundStyle(.red).padding()
                HStack {
                    Button("Retry save") { model.save() }
                    Button("Restore previous ink") { model.restorePrevious() }
                }
            }
            StudyPencilSurface(model: model)
        }
        // As in DrawingEditor: the tab bar would push these into an overflow menu.
        .toolbar(.hidden, for: .tabBar)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button("Undo", systemImage: "arrow.uturn.backward") { model.canvas.undoManager?.undo() }
                Button("Save ink") { model.save() }
                Button("Prepare page export") { model.exportPage() }
                if let url = model.exportURL { ShareLink("Share annotated page", item: url) }
                if let page = try? model.store.drawing(page: model.pageIndex),
                   let drawingStore = try? model.store.drawingStore(page: model.pageIndex),
                   let url = try? drawingStore.nativeURL(page) {
                    ShareLink("Export page ink", item: url)
                }
            }
        }
        .onDisappear { model.save() }
        .onChange(of: phase) { _, value in if value != .active { model.save() } }
        .onChange(of: model.pageIndex) { _, index in onPageChanged(index) }
    }
}

private struct StudyPencilSurface: UIViewRepresentable {
    @ObservedObject var model: StudyAnnotationModel
    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeUIView(context: Context) -> StudyZoomView {
        let scroll = StudyZoomView()
        scroll.delegate = context.coordinator
        scroll.panGestureRecognizer.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        scroll.maximumZoomScale = 4
        scroll.backgroundColor = .secondarySystemBackground
        scroll.content.addSubview(scroll.image)
        scroll.content.addSubview(model.canvas)
        scroll.addSubview(scroll.content)
        model.canvas.delegate = context.coordinator
        context.coordinator.picker.addObserver(model.canvas)
        DispatchQueue.main.async {
            context.coordinator.picker.setVisible(true, forFirstResponder: model.canvas)
            model.canvas.becomeFirstResponder()
        }
        return scroll
    }

    func updateUIView(_ scroll: StudyZoomView, context: Context) {
        scroll.image.image = model.background
        if scroll.pageIndex != model.pageIndex {
            scroll.pageIndex = model.pageIndex
            scroll.setZoomScale(1, animated: false)
            scroll.content.frame = CGRect(origin: .zero, size: model.pageSize)
            scroll.image.frame = scroll.content.bounds
            model.canvas.frame = scroll.content.bounds
            scroll.contentSize = model.pageSize
            scroll.needsFit = true
            scroll.setNeedsLayout()
        }
    }

    static func dismantleUIView(_ scroll: StudyZoomView, coordinator: Coordinator) {
        coordinator.model.save()
        coordinator.picker.setVisible(false, forFirstResponder: coordinator.model.canvas)
        coordinator.picker.removeObserver(coordinator.model.canvas)
        coordinator.model.canvas.delegate = nil
    }

    final class Coordinator: NSObject, UIScrollViewDelegate, PKCanvasViewDelegate {
        let model: StudyAnnotationModel
        let picker = PKToolPicker()
        init(model: StudyAnnotationModel) { self.model = model }
        func viewForZooming(in scrollView: UIScrollView) -> UIView? { (scrollView as? StudyZoomView)?.content }
        func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) { model.changed() }
        func canvasViewDidEndUsingTool(_ canvasView: PKCanvasView) { model.changed() }
    }
}

private final class StudyZoomView: UIScrollView {
    let content = UIView()
    let image = UIImageView()
    var pageIndex = -1
    var needsFit = true
    private var fittedWidth: CGFloat = 0
    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 0, content.bounds.width > 0 else { return }
        if needsFit || fittedWidth != bounds.width {
            let wasFitted = needsFit || abs(zoomScale - minimumZoomScale) < 0.001
            minimumZoomScale = min(1, bounds.width / content.bounds.width)
            if wasFitted { setZoomScale(minimumZoomScale, animated: false) }
            fittedWidth = bounds.width
            needsFit = false
        }
    }
}
