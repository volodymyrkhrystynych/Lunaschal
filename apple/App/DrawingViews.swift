import SwiftUI
import PencilKit
import LunaschalCore
import UniformTypeIdentifiers

struct DrawingLibraryView: View {
    @ObservedObject var model: CaptureModel
    @State private var pages: [DrawingPage] = []
    @State private var naming: DrawingPage?
    @State private var title = ""
    @State private var importing = false

    var body: some View {
        List {
            Section {
                Text("Drawings are saved on this device. Export originals for backup; drawing sync is not available yet.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("New drawing", systemImage: "plus") {
                    do { _ = try model.drawings.create(); reload() }
                    catch { model.message = error.localizedDescription }
                }
                Button("Import editable ink", systemImage: "square.and.arrow.down") { importing = true }
                Text("Choose an exported .drawing file. It opens as a new editable page; existing pages are kept.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(pages) { page in
                NavigationLink { DrawingEditor(store: model.drawings, page: page) } label: {
                    VStack(alignment: .leading) {
                        Text(page.title)
                        Text(page.updatedAt, format: .dateTime.month().day().hour().minute()).font(.caption)
                    }
                }
                .contextMenu {
                    Button("Rename") { title = page.title; naming = page }
                }
            }
        }
        .navigationTitle("Drawings")
        .onAppear { reload() }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.data]) { result in
            do {
                let url = try result.get()
                let accessing = url.startAccessingSecurityScopedResource()
                defer { if accessing { url.stopAccessingSecurityScopedResource() } }
                _ = try DrawingImport.importFile(url, into: model.drawings)
                reload()
            } catch { model.message = "Could not import drawing. \(error.localizedDescription)" }
        }
        .alert("Drawing title", isPresented: Binding(get: { naming != nil }, set: { if !$0 { naming = nil } })) {
            TextField("Title", text: $title)
            Button("Save") {
                do { if let naming { try model.drawings.rename(naming.id, title: title) }; reload() }
                catch { model.message = error.localizedDescription }
                naming = nil
            }
            Button("Cancel", role: .cancel) { naming = nil }
        }
    }

    private func reload() {
        do { pages = try model.drawings.pages() }
        catch { model.message = error.localizedDescription }
    }
}

/// The importer uses the same native format as Export editable ink. PNG/PDF
/// previews are not editable ink. Decode first, then publish a new local page.
enum DrawingImport {
    static let maximumBytes = 64 * 1024 * 1024

    static func importFile(_ url: URL, into store: DrawingStore) throws -> DrawingPage {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let native = try handle.read(upToCount: maximumBytes + 1) ?? Data()
        guard native.count <= maximumBytes else { throw ImportError.tooLarge }
        return try store.importDrawing(title: url.deletingPathExtension().lastPathComponent, native: native) {
            let drawing = try PKDrawing(data: $0)
            // PencilKit can decode arbitrary bytes as an empty drawing without
            // throwing. Do not publish that as a successfully restored page.
            guard !drawing.strokes.isEmpty else { throw ImportError.noEditableInk }
            return try preview(drawing)
        }
    }

    static func preview(_ drawing: PKDrawing) throws -> Data {
        let bounds = CGRect(x: 0, y: 0, width: 2100, height: 2970)
        guard let data = drawing.image(from: bounds, scale: 1240.0 / 2100.0).pngData() else {
            throw DrawingError.incompleteCheckpoint
        }
        return data
    }

    enum ImportError: LocalizedError {
        case tooLarge, noEditableInk
        var errorDescription: String? {
            switch self {
            case .tooLarge: return "This drawing exceeds the 64 MB import limit. The original file has been kept."
            case .noEditableInk: return "No editable ink was found. Choose an exported drawing with strokes; blank drawings and previews cannot be imported."
            }
        }
    }
}

@MainActor
private final class DrawingEditorModel: ObservableObject {
    @Published var page: DrawingPage
    @Published var error: String?
    @Published var status = "Saved on this device"
    let canvas = A4Canvas()
    let store: DrawingStore
    private var loaded = false
    private var dirty = false
    private var checkpointTask: Task<Void, Never>?

    init(store: DrawingStore, page: DrawingPage) {
        self.store = store
        self.page = page
        do {
            if let url = try store.nativeURL(page) { canvas.drawing = try PKDrawing(data: Data(contentsOf: url)) }
            loaded = true
        } catch {
            self.error = "Could not open the original drawing. Existing files were kept. \(error.localizedDescription)"
            status = "Could not open drawing"
        }
        canvas.isUserInteractionEnabled = loaded
    }

    func changed() {
        guard loaded else { return }
        dirty = true
        status = "Saving on device…"
        checkpointTask?.cancel()
        checkpointTask = Task { [weak self] in
            do { try await Task.sleep(for: .milliseconds(700)) } catch { return }
            self?.checkpoint()
        }
    }

    func checkpoint() {
        checkpointTask?.cancel()
        guard loaded, dirty else { return }
        do {
            let preview = try DrawingImport.preview(canvas.drawing)
            page = try store.checkpoint(page.id, native: canvas.drawing.dataRepresentation(), preview: preview)
            dirty = false
            error = nil
            status = "Saved on this device"
        } catch { self.error = error.localizedDescription; status = "Save failed · keep this drawing open" }
    }

    func restorePrevious() {
        do {
            var recovered = PKDrawing()
            page = try store.restorePrevious(page.id) { recovered = try PKDrawing(data: $0) }
            canvas.drawing = recovered
            loaded = true
            canvas.isUserInteractionEnabled = true
            dirty = false
            error = nil
            status = "Previous saved version restored"
        } catch { self.error = error.localizedDescription }
    }
}

private struct DrawingEditor: View {
    @StateObject private var model: DrawingEditorModel
    @Environment(\.scenePhase) private var phase

    init(store: DrawingStore, page: DrawingPage) {
        _model = StateObject(wrappedValue: DrawingEditorModel(store: store, page: page))
    }

    var body: some View {
        VStack(spacing: 0) {
            Text(model.status).font(.caption).frame(maxWidth: .infinity).padding(6)
            if let error = model.error {
                Text(error).foregroundStyle(.red).padding()
                Button("Restore previous saved version") { model.restorePrevious() }
            }
            PencilSurface(model: model)
        }
        .navigationTitle(model.page.title).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button("Undo", systemImage: "arrow.uturn.backward") { model.canvas.undoManager?.undo() }
                Button("Save locally") { model.checkpoint() }
                if let url = try? model.store.nativeURL(model.page) { ShareLink("Export editable ink", item: url) }
                if let url = try? model.store.previewURL(model.page) { ShareLink("Export PNG", item: url) }
            }
        }
        .onDisappear { model.checkpoint() }
        .onChange(of: phase) { _, value in if value != .active { model.checkpoint() } }
    }
}

private struct PencilSurface: UIViewRepresentable {
    @ObservedObject var model: DrawingEditorModel
    func makeCoordinator() -> Coordinator { Coordinator(model: model) }
    func makeUIView(context: Context) -> A4Canvas {
        let canvas = model.canvas
        canvas.delegate = context.coordinator
        canvas.backgroundColor = .white
        canvas.overrideUserInterfaceStyle = .light
        canvas.contentSize = CGSize(width: 2100, height: 2970)
        canvas.drawingPolicy = .pencilOnly
        canvas.alwaysBounceVertical = true
        canvas.maximumZoomScale = 3
        context.coordinator.picker.addObserver(canvas)
        DispatchQueue.main.async {
            context.coordinator.picker.setVisible(true, forFirstResponder: canvas)
            canvas.becomeFirstResponder()
        }
        return canvas
    }
    func updateUIView(_ canvas: A4Canvas, context: Context) {}
    static func dismantleUIView(_ canvas: A4Canvas, coordinator: Coordinator) {
        coordinator.model.checkpoint()
        coordinator.picker.setVisible(false, forFirstResponder: canvas)
        coordinator.picker.removeObserver(canvas)
        canvas.delegate = nil
    }
    final class Coordinator: NSObject, PKCanvasViewDelegate {
        let model: DrawingEditorModel
        let picker = PKToolPicker()
        init(model: DrawingEditorModel) { self.model = model }
        func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) { model.changed() }
        func canvasViewDidEndUsingTool(_ canvasView: PKCanvasView) { model.changed() }
    }
}

private final class A4Canvas: PKCanvasView {
    private var fittedWidth: CGFloat = 0
    override func layoutSubviews() {
        super.layoutSubviews()
        guard bounds.width > 0, bounds.width != fittedWidth else { return }
        let oldMinimum = minimumZoomScale
        let fit = bounds.width / 2100
        let wasFitted = fittedWidth == 0 || abs(zoomScale - oldMinimum) < 0.001
        fittedWidth = bounds.width
        minimumZoomScale = fit
        if wasFitted { setZoomScale(fit, animated: false) }
    }
}
