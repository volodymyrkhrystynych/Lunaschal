import SwiftUI
import LunaschalCore

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

    var body: some View {
        List {
            Text("Saved server previews. These pages are read-only; drawings made in the Draw tab are stored separately on this device.")
                .font(.footnote).foregroundStyle(.secondary)
            if pages.isEmpty {
                Text("No page metadata downloaded. Download the library on Wi-Fi to include these pages.")
            }
            ForEach(Array(pages.enumerated()), id: \.element.id) { index, page in
                NavigationLink("Page \(index + 1)") {
                    DownloadedMediaView(model: model, collection: "paper_pages", id: page.id,
                                        mime: "image/png", title: "\(paper.title) · Page \(index + 1)")
                }
            }
        }
        .navigationTitle(paper.title).navigationBarTitleDisplayMode(.inline)
        .task(id: model.downloadingLibrary) {
            do { pages = try model.replica.paperPages(paperID: paper.id) }
            catch { model.message = error.localizedDescription }
        }
    }
}
