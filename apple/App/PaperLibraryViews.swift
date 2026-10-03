import SwiftUI
import LunaschalCore
import PencilKit

/// Local replica browsing only: opening a row never fetches a server file.
struct PaperLibrarySections: View {
    @ObservedObject var model: CaptureModel
    let query: String
    @State private var papers: [SyncChange] = []
    @State private var covers: [SyncChange] = []
    @State private var paperLimit = 200
    @State private var coverLimit = 200
    @State private var paperCount = 0
    @State private var coverCount = 0

    var body: some View {
        Group {
            Section("Paper documents") {
                ForEach(papers) { paper in
                    NavigationLink(paper.title) { PaperPreviewView(model: model, paper: paper) }
                }
                if papers.count < paperCount {
                    Button("Load more papers (\(papers.count) of \(paperCount))") {
                        paperLimit += 200; refresh()
                    }
                }
            }
            Section("Newspaper front pages") {
                ForEach(covers) { cover in
                    NavigationLink(cover.title) {
                        DownloadedMediaView(model: model, collection: "newspaper_frontpages",
                                            id: cover.id, mime: "image/jpeg", title: cover.title)
                    }
                }
                if covers.count < coverCount {
                    Button("Load more front pages (\(covers.count) of \(coverCount))") {
                        coverLimit += 200; refresh()
                    }
                }
                Text("Front-page images only. Complete newspaper PDFs and annotations are not available here yet.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .task(id: model.downloadingLibrary) { refresh() }
        .onChange(of: model.syncing) { _, syncing in if !syncing { refresh() } }
        .onChange(of: query) { _, _ in paperLimit = 200; coverLimit = 200; refresh() }
    }

    private func refresh() {
        do {
            papers = try model.replica.records(collection: "papers", query: query, limit: paperLimit)
            paperCount = try model.replica.count(collection: "papers", query: query)
            covers = try model.replica.records(collection: "newspaper_frontpages", query: query, limit: coverLimit)
            coverCount = try model.replica.count(collection: "newspaper_frontpages", query: query)
        } catch { model.message = error.localizedDescription }
    }
}

private struct PaperPreviewView: View {
    @ObservedObject var model: CaptureModel
    let paper: SyncChange
    @State private var pages: [SyncChange] = []
    @State private var editing: DrawingPage?

    var body: some View {
        List {
            Text("Web drawings open as saved previews. Native drawings can be opened for editing after downloading their original and preview on Wi-Fi.")
                .font(.footnote).foregroundStyle(.secondary)
            if pages.isEmpty {
                Text("No page metadata downloaded. Download the library on Wi-Fi to include these pages.")
            }
            ForEach(Array(pages.enumerated()), id: \.element.id) { index, page in
                NavigationLink("Page \(index + 1)") {
                    DownloadedMediaView(model: model, collection: "paper_pages", id: page.id,
                                        mime: "image/png", title: "\(paper.title) · Page \(index + 1)")
                }
                if let native = try? model.replica.record(collection: "paper_native_ink", id: page.id), !native.deleted {
                    Button("Edit native page \(index + 1)") { openDrawing(page, native: native) }
                }
            }
        }
        .navigationTitle(paper.title).navigationBarTitleDisplayMode(.inline)
        .navigationDestination(item: $editing) { page in DrawingEditor(owner: model, page: page) }
        .task(id: model.downloadingLibrary) {
            do { pages = try model.replica.paperPages(paperID: paper.id) }
            catch { model.message = error.localizedDescription }
        }
    }

    private func openDrawing(_ page: SyncChange, native: SyncChange) {
        do {
            let existing = try? model.drawings.page(page.id)
            let prior = try model.drawingPublications.publication(page.id)
            if let existing {
                // A remote download cannot replace unsaved or queued ink. A
                // clean, acknowledged copy can explicitly open a newer version.
                guard let prior, prior.state == "synced", prior.checkpoint == existing.checkpoint,
                      let revision = prior.revision, native.revision > revision,
                      prior.operation.epoch == (try model.replica.epoch) else { editing = existing; return }
            }
            guard let server = model.server, let epoch = try model.replica.epoch,
                  let inkURL = try model.media.downloaded(collection: "paper_native_ink", id: page.id),
                  let previewURL = try model.media.downloaded(collection: "paper_pages", id: page.id),
                  try MediaStore.sha256(inkURL) == native.data?["sha256"]?.string,
                  try MediaStore.sha256(previewURL) == native.data?["previewSha256"]?.string else {
                throw DrawingError.incompleteCheckpoint
            }
            let ink = try Data(contentsOf: inkURL)
            _ = try PKDrawing(data: ink)
            let preview = try Data(contentsOf: previewURL)
            let imported = try existing.map { try model.drawings.checkpoint($0.id, native: ink, preview: preview) }
                ?? model.drawings.importServerDrawing(id: page.id, title: paper.title, native: ink, preview: preview)
            try model.drawingPublications.adopt(imported, paperID: paper.id, native: native,
                drawings: model.drawings, server: server, epoch: epoch, replacingCheckpoint: existing?.checkpoint)
            editing = imported
        } catch { model.message = "Could not open this native page. Download its latest original and preview first. \(error.localizedDescription)" }
    }
}
