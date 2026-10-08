import SwiftUI
import LunaschalCore
import PDFKit
import AVKit
import WebKit

enum LibraryCategory: String, CaseIterable, Identifiable {
    case books = "Books and stories"
    case documents = "Documents"
    case papers = "Paper documents"
    case newspapers = "Newspaper front pages"
    case knowledge = "Knowledge articles"

    var id: String { rawValue }
    var collection: String {
        switch self {
        case .books: return "fics"
        case .documents: return "study_sources"
        case .papers: return "papers"
        case .newspapers: return "newspaper_frontpages"
        case .knowledge: return "wiki_articles"
        }
    }
    var icon: String {
        switch self {
        case .books: return "books.vertical"
        case .documents: return "doc.text"
        case .papers: return "pencil.and.outline"
        case .newspapers: return "newspaper"
        case .knowledge: return "globe"
        }
    }
}

struct StudyLibraryView: View {
    @ObservedObject var model: CaptureModel

    private var categories: [LibraryCategory] {
        LibraryCategory.allCases.filter { $0 != .books }
    }

    var body: some View {
        List(categories) { category in
            NavigationLink {
                LibraryCategoryView(model: model, category: category)
            } label: {
                Label(category.rawValue, systemImage: category.icon)
            }
            .accessibilityIdentifier("library-\(category.collection)")
        }
        .navigationTitle("Study")
    }
}

struct LibraryCategoryView: View {
    @ObservedObject var model: CaptureModel
    let category: LibraryCategory
    var title: String? = nil
    @State private var query = ""
    @State private var records: [SyncChange] = []
    @State private var limit = 50
    @State private var count = 0

    var body: some View {
        List {
            if records.isEmpty {
                ContentUnavailableView(
                    query.isEmpty ? "No saved items" : "No matching items",
                    systemImage: category.icon,
                    description: Text(query.isEmpty
                        ? "Sync with your server or use Settings → Library downloads to save content for offline reading."
                        : "Try a different search in \(category.rawValue.lowercased())."))
            }
            ForEach(records) { record in
                NavigationLink {
                    destination(record)
                } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(record.title).lineLimit(2)
                        if category == .books, let author = record.data?["author"]?.string, !author.isEmpty {
                            Text(author).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        if category == .knowledge, let summary = record.data?["summary"]?.string, !summary.isEmpty {
                            Text(summary).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                }
            }
            if records.count < count {
                Button("Load more (\(records.count) of \(count))") { limit += 50; refresh() }
            }
            if category == .newspapers {
                Text("Front-page images only. Complete newspaper PDFs and annotations are not available here yet.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle(title ?? category.rawValue)
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Search \(category.rawValue.lowercased())")
        .task(id: model.downloadingLibrary) { refresh() }
        .onChange(of: model.syncing) { _, syncing in if !syncing { refresh() } }
        .onChange(of: query) { _, _ in limit = 50 }
        .task(id: query) {
            guard !query.isEmpty else { return refresh() }
            try? await Task.sleep(nanoseconds: BookListView.typingPause)
            guard !Task.isCancelled else { return }
            refresh()
        }
    }

    @ViewBuilder
    private func destination(_ record: SyncChange) -> some View {
        switch category {
        case .books:
            BookView(model: model, book: record)
        case .documents:
            DownloadedMediaView(model: model, collection: category.collection, id: record.id,
                                mime: record.data?["contentType"]?.string ?? "", title: record.title)
        case .papers:
            PaperPreviewView(model: model, paper: record)
        case .newspapers:
            DownloadedMediaView(model: model, collection: category.collection, id: record.id,
                                mime: "image/jpeg", title: record.title)
        case .knowledge:
            KnowledgeArticleView(article: record)
        }
    }

    private func refresh() {
        do {
            records = try model.replica.records(collection: category.collection, query: query, limit: limit)
            count = try model.replica.count(collection: category.collection, query: query)
        } catch { model.message = error.localizedDescription }
    }
}

struct LibraryDownloadSettings: View {
    private func size(_ bytes: Int64) -> String { ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file) }

    private func importFic() {
        let text = importLink
        Task {
            if await model.importFic(text) { importLink = "" }
            countWaitingImports()
        }
    }

    private func countWaitingImports() { waitingImports = (try? model.ficImports.list().count) ?? 0 }

    @ObservedObject var model: CaptureModel
    @State private var confirmingRemoval = false
    @AppStorage("libraryBudgetGB") private var budget = 20
    @AppStorage("downloadKnowledge") private var knowledge = false
    @AppStorage("download-journal_attachments") private var journalMedia = true
    @AppStorage("download-study_sources") private var studyMedia = true
    @AppStorage("download-paper_pages") private var paperPreviews = true
    @AppStorage("download-paper_native_ink") private var nativeInk = true
    @AppStorage("download-paper_page_images") private var paperImages = true
    @AppStorage("download-newspaper_frontpages") private var frontpages = true
    @AppStorage("download-fics") private var pdfBooks = true
    @State private var importLink = ""
    @State private var waitingImports = 0

    var body: some View {
        List {
            Section {
                TextField("Link to a fic", text: $importLink)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.go)
                    .onSubmit(importFic)
                    .accessibilityIdentifier("fic-import-link")
                HStack {
                    // Borderless, or a tap anywhere in the row presses both.
                    PasteButton(payloadType: String.self) { strings in
                        Task { @MainActor in importLink = strings.first ?? importLink }
                    }
                    .labelStyle(.titleAndIcon)
                    .buttonStyle(.borderless)
                    Spacer()
                    if model.importingFic {
                        ProgressView()
                    } else {
                        Button("Import", action: importFic)
                            .buttonStyle(.borderless)
                            .disabled(importLink.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
                if waitingImports > 0 {
                    Text("\(waitingImports) \(waitingImports == 1 ? "link is" : "links are") waiting for the server.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } header: {
                Text("Import a fic")
            } footer: {
                Text("\(FicImportLink.siteNames.joined(separator: ", ")). Sharing a link to Lunaschal from Safari or another app imports it too.")
            }
            Section {
                Button("Download library over Wi-Fi") { model.startLibraryDownload() }
                    .disabled(model.downloadingLibrary || !model.signedIn)
                if model.downloadingLibrary {
                    ProgressView()
                    Button("Pause downloads") { model.pauseLibrary() }
                    Text("You can keep using the app or switch tabs while downloads continue. Leaving the app pauses downloads.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Stepper("Media budget: \(budget) GB", value: $budget, in: 1...150, step: 5)
                    .disabled(model.downloadingLibrary)
                Text("Downloaded: \(size(model.libraryTextBytes + model.libraryBytes)) (text \(size(model.libraryTextBytes)) · media \(size(model.libraryBytes)))")
                    .font(.footnote).foregroundStyle(.secondary)
                    .accessibilityIdentifier("library-downloaded-size")
                Button("Remove downloaded media", role: .destructive) { confirmingRemoval = true }
                    .disabled(model.downloadingLibrary)
                if let message = model.libraryMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Include in future downloads") {
                Toggle("PDF books", isOn: $pdfBooks)
                Toggle("Journal attachments", isOn: $journalMedia)
                Toggle("Study documents", isOn: $studyMedia)
                Toggle("Paper previews", isOn: $paperPreviews)
                Toggle("Editable native drawings", isOn: $nativeInk)
                Toggle("Pictures on paper pages", isOn: $paperImages)
                Toggle("Newspaper front pages", isOn: $frontpages)
                Toggle("Knowledge articles", isOn: $knowledge)
                Text("Changing selection keeps existing downloads. Wikipedia ZIM packages are not yet supported.")
                    .font(.footnote).foregroundStyle(.secondary)
            }.disabled(model.downloadingLibrary)

        }
        .navigationTitle("Library downloads")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear(perform: countWaitingImports)
        .onChange(of: model.syncing) { _, _ in countWaitingImports() }
        .confirmationDialog("Remove all downloaded media copies from this device?", isPresented: $confirmingRemoval) {
            Button("Remove downloaded media", role: .destructive) { model.removeLibraryMedia() }
        } message: {
            Text("Your original captures, drawings, and server records are kept.")
        }
    }
}

private struct KnowledgeArticleView: View {
    let article: SyncChange

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(article.title).font(.title).accessibilityAddTraits(.isHeader)
                Text("Downloaded article · Available offline").font(.caption).foregroundStyle(.secondary)
                if let summary = article.data?["summary"]?.string, !summary.isEmpty {
                    Text(summary).font(.headline)
                }
                // Render stored text directly: article HTML, external images,
                // scripts, and embedded links cannot trigger network requests.
                Text(article.data?["content"]?.string ?? "No article text was downloaded.")
                    .font(.system(.body, design: .serif)).textSelection(.enabled)
            }.frame(maxWidth: 760, alignment: .leading).padding()
                .frame(maxWidth: .infinity)
        }
        .navigationTitle(article.title).navigationBarTitleDisplayMode(.inline)
    }
}

struct BookView: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange
    @State private var chapters: [SyncChange] = []
    @State private var resume: (chapter: SyncChange, fraction: Double?)?
    @State private var bookmarks: [SyncChange] = []
    @State private var bookmarkEdits: [PendingEdit] = []
    @State private var onDevice = true

    var body: some View {
        List {
            if let description = book.data?["description"]?.string, !description.isEmpty {
                Text(description)
            }
            if book.data?["sourceType"]?.string == "pdf" {
                NavigationLink("Open PDF") {
                    DownloadedMediaView(model: model, collection: "fics", id: book.id,
                                        mime: "application/pdf", title: book.title)
                }
            }
            if !onDevice || model.ficDownload?.id == book.id {
                FicDownloadState(model: model, book: book)
            }
            if let resume {
                NavigationLink("Continue: \(resume.chapter.title)") {
                    TextChapterReader(owner: model, chapter: resume.chapter, initialFraction: resume.fraction, book: book)
                }
            }
            if !bookmarks.isEmpty {
                Section("Bookmarks") {
                    ForEach(bookmarks) { bookmark in
                        if let chapter = chapters.first(where: { $0.id == bookmark.data?["chapterId"]?.string }) {
                            NavigationLink {
                                TextChapterReader(owner: model, chapter: chapter,
                                    initialFraction: bookmark.data?["scrollPosition"]?.number)
                            } label: {
                                Label("\(bookmark.data?["type"]?.string == "continue" ? "Continue" : "Favorite"): \(chapter.title)",
                                      systemImage: bookmark.data?["type"]?.string == "continue" ? "play.fill" : "star.fill")
                            }
                            .swipeActions {
                                Button("Remove", role: .destructive) {
                                    do { try model.replica.deleteBookmark(bookmark); refreshChapters(); model.requestSync() }
                                    catch { model.message = error.localizedDescription }
                                }.disabled(bookmarkEdits.contains {
                                    $0.operation.recordId == bookmark.id ||
                                    (bookmark.data?["type"]?.string == "continue" && $0.original.data?["type"]?.string == "continue")
                                })
                            }
                        } else {
                            Text("Bookmarked chapter not downloaded. Download the library over Wi-Fi.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if !bookmarkEdits.isEmpty {
                Section("Bookmark sync") {
                    ForEach(bookmarkEdits) { edit in
                        Text(edit.state == "pending" ? "Bookmark saved on device · Waiting to sync" : edit.error ?? "Bookmark needs review")
                        if edit.state != "pending" {
                            Button("Keep server version and discard this pending change", role: .destructive) {
                                do { try model.replica.resolve(edit, keepLocal: false); refreshChapters() }
                                catch { model.message = error.localizedDescription }
                            }
                        }
                    }
                }
            }
            ForEach(chapters) { chapter in
                NavigationLink {
                    TextChapterReader(owner: model, chapter: chapter)
                } label: { Text(chapter.title) }
            }
        }
        .navigationTitle(book.title)
        .task(id: model.downloadingLibrary) { refreshChapters() }
        .onChange(of: model.ficDownloadRevision) { _, _ in refreshChapters() }
        .onAppear {
            do { try model.replica.markBookOpened(book.id) }
            catch { model.message = error.localizedDescription }
            refreshChapters()
            model.ensureFicOnDevice(book)
        }
        .onChange(of: model.syncing) { _, syncing in if !syncing { refreshChapters() } }
    }

    private func refreshChapters() {
        do {
            chapters = try model.replica.chapterOutline(bookID: book.id)
            resume = try model.replica.resumePoint(bookID: book.id)
            bookmarks = try model.replica.bookmarks(bookID: book.id)
            bookmarkEdits = try model.replica.bookmarkEdits(bookID: book.id)
            onDevice = model.isFicOnDevice(book)
        } catch { model.message = error.localizedDescription }
    }
}

struct JournalRecordView: View {
    @ObservedObject var model: CaptureModel
    let record: SyncChange
    @State private var content = ""
    @State private var title = ""
    @State private var editing = false
    @State private var confirmingDelete = false
    @State private var attachments: [SyncChange] = []
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            if editing {
                TextField("Title", text: $title)
                TextEditor(text: $content).frame(minHeight: 240)
                Button("Save edit on this device") {
                    if model.edit(record, content: content, title: title) { dismiss() }
                }
                Button("Cancel", role: .cancel) { editing = false }
            } else {
                Text(record.data?["content"]?.string ?? "").textSelection(.enabled)
                if let original = record.data?["rawContent"]?.string, !original.isEmpty {
                    DisclosureGroup("Original text and dictation") { Text(original).textSelection(.enabled) }
                }
                Button("Edit") {
                    content = record.data?["content"]?.string ?? ""
                    title = record.data?["title"]?.string ?? ""
                    editing = true
                }
                Button("Delete entry", role: .destructive) { confirmingDelete = true }
                Section("Attachments") {
                    ForEach(attachments) { attachment in
                        NavigationLink(attachment.data?["name"]?.string ?? "Attachment") {
                            DownloadedMediaView(model: model, collection: "journal_attachments", id: attachment.id,
                                                mime: attachment.data?["mime"]?.string ?? "", title: attachment.data?["name"]?.string ?? "Attachment")
                        }
                        if let transcript = attachment.data?["transcript"]?.string, !transcript.isEmpty {
                            DisclosureGroup("Transcript: \(attachment.data?["name"]?.string ?? "Attachment")") {
                                Text(transcript).textSelection(.enabled)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle(record.title).navigationBarTitleDisplayMode(.inline)
        .task {
            attachments = (try? model.replica.relatedRecords(collection: "journal_attachments", field: "entryId", value: record.id)) ?? []
        }
        .confirmationDialog("Delete this journal entry when the server reconnects?", isPresented: $confirmingDelete) {
            Button("Delete entry", role: .destructive) { model.delete(record); dismiss() }
        }
    }
}

struct DownloadedMediaView: View {
    @ObservedObject var model: CaptureModel
    let collection: String
    let id: String
    let mime: String
    let title: String
    @State private var file: URL?
    @State private var player: AVPlayer?
    @State private var savedPDFPage = 0
    @State private var confirmingRemoval = false
    @State private var availability: MediaAvailability = .metadataOnly
    @State private var studyAnnotation: StudyAnnotationModel?

    var body: some View {
        Group {
            if let file {
                if let studyAnnotation {
                    StudyAnnotationView(model: studyAnnotation) { page in
                        do {
                            try model.replica.saveReadingPosition(collection: collection, id: id,
                                version: file.lastPathComponent, offset: page)
                        } catch { model.message = error.localizedDescription }
                    }.id(file.lastPathComponent)
                } else if mime == "application/pdf" {
                    LocalPDFView(url: file, initialPage: savedPDFPage) { page in
                        do {
                            try model.replica.saveReadingPosition(collection: collection, id: id,
                                version: file.lastPathComponent, offset: page)
                        } catch { model.message = error.localizedDescription }
                    }
                } else if mime.hasPrefix("text/html") {
                    LocalArticleView(url: file)
                } else if mime.hasPrefix("image/"), let picture = UIImage(contentsOfFile: file.path) {
                    ScrollView([.horizontal, .vertical]) { Image(uiImage: picture).resizable().scaledToFit() }
                } else if mime.hasPrefix("audio/") || mime.hasPrefix("video/") {
                    VideoPlayer(player: player)
                } else {
                    ContentUnavailableView("File downloaded", systemImage: "doc", description: Text("This file type does not yet have a native reader."))
                }
            } else if collection == "fics", let book = try? model.replica.record(collection: "fics", id: id),
                      model.ficDownload?.id == id || model.ficQueue.contains(id) || model.ficErrors[id] != nil {
                FicDownloadState(model: model, book: book).padding()
            } else {
                ContentUnavailableView(availability.title, systemImage: "arrow.down.circle",
                                       description: Text(availability.detail))
            }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if file != nil {
                Button("Remove device copy", systemImage: "trash") { confirmingRemoval = true }
                    .disabled(model.downloadingLibrary)
            }
        }
        .confirmationDialog("Remove this downloaded copy?", isPresented: $confirmingRemoval) {
            Button("Remove device copy", role: .destructive) {
                player?.pause()
                if model.removeLibraryMedia(collection: collection, id: id) {
                    player = nil
                    file = nil
                    refresh()
                }
            }
        } message: {
            Text("The server original and your captures are kept. A future library download can download this item again.")
        }
        .task(id: model.downloadingLibrary) { refresh() }
        .onChange(of: model.ficDownloadRevision) { _, _ in if collection == "fics" { refresh() } }
        .onDisappear { player?.pause() }
    }

    private func refresh() {
        do {
            availability = try model.media.availability(collection: collection, id: id)
            let downloaded = try model.media.downloaded(collection: collection, id: id)
            if downloaded != file {
                player?.pause()
                player = nil
                studyAnnotation = nil
                if let downloaded, mime == "application/pdf" {
                    savedPDFPage = try model.replica.readingPosition(collection: collection, id: id,
                        version: downloaded.lastPathComponent) ?? 0
                }
                if let downloaded, collection == "study_sources", UIDevice.current.userInterfaceIdiom == .pad,
                   mime == "application/pdf" || mime.hasPrefix("image/") {
                    let ink = try StudyAnnotationStore(root: model.store.root.appendingPathComponent("study-annotations"),
                        sourceID: id, version: downloaded.lastPathComponent)
                    studyAnnotation = try StudyAnnotationModel(file: downloaded, mime: mime, store: ink,
                                                               initialPage: savedPDFPage)
                }
                file = downloaded
                if let file, mime.hasPrefix("audio/") || mime.hasPrefix("video/") { player = AVPlayer(url: file) }
            }
        } catch { model.message = error.localizedDescription }
    }
}

struct LocalPDFView: UIViewRepresentable {
    let url: URL
    var initialPage = 0
    var onPageChanged: ((Int) -> Void)? = nil
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeUIView(context: Context) -> PDFView {
        let view = makePDFView()
        context.coordinator.observe(view, callback: onPageChanged)
        return view
    }
    func makePDFView() -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.document = PDFDocument(url: url)
        if let document = view.document, let page = document.page(at: initialPage >= 0 && initialPage < document.pageCount ? initialPage : 0) {
            view.go(to: page)
        }
        return view
    }
    func updateUIView(_ view: PDFView, context: Context) {
        context.coordinator.callback = onPageChanged
        if view.document?.documentURL != url {
            view.document = PDFDocument(url: url)
            if let document = view.document,
               let page = document.page(at: initialPage >= 0 && initialPage < document.pageCount ? initialPage : 0) {
                view.go(to: page)
            }
        }
    }
    final class Coordinator {
        private var observer: NSObjectProtocol?
        var callback: ((Int) -> Void)?
        func observe(_ view: PDFView, callback: ((Int) -> Void)?) {
            self.callback = callback
            if let observer { NotificationCenter.default.removeObserver(observer) }
            observer = NotificationCenter.default.addObserver(forName: .PDFViewPageChanged, object: view, queue: .main) { [weak view, weak self] _ in
                guard let view, let page = view.currentPage, let document = view.document else { return }
                let index = document.index(for: page)
                if index != NSNotFound { self?.callback?(index) }
            }
        }
        deinit { if let observer { NotificationCenter.default.removeObserver(observer) } }
    }
}

private struct LocalArticleView: UIViewRepresentable {
    let url: URL
    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        let policy = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        view.loadHTMLString(policy + ((try? String(contentsOf: url, encoding: .utf8)) ?? "Article unavailable"), baseURL: nil)
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            decisionHandler(action.request.url?.scheme == "about" ? .allow : .cancel)
        }
    }
}

struct PendingEditView: View {
    @ObservedObject var model: CaptureModel
    let edit: PendingEdit
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section("Your saved change") {
                Text(edit.operation.action == "delete" ? "Delete entry" : edit.operation.data["content"]?.string ?? "Title or tags changed")
                    .textSelection(.enabled)
            }
            if let error = edit.error { Text(error).foregroundStyle(.orange) }
            if let current = edit.conflict {
                Section("Server version") {
                    Text(current.deleted ? "Deleted on server" : current.data?["content"]?.string ?? "").textSelection(.enabled)
                }
            }
            if edit.state != "pending" {
                Button("Apply my change to the latest version") { model.resolve(edit, keepLocal: true); dismiss() }
                    .disabled(edit.conflict?.deleted == true)
                if let text = edit.operation.data["content"]?.string {
                    Button("Save my text as a separate entry") {
                        if model.saveText(text) { model.resolve(edit, keepLocal: false); dismiss() }
                    }
                }
                Button("Discard my pending change", role: .destructive) { model.resolve(edit, keepLocal: false); dismiss() }
            } else { Text("This edit is saved locally and will sync when connected.") }
        }.navigationTitle("Saved edit")
    }
}
