import SwiftUI
import LunaschalCore
import PDFKit
import AVKit
import WebKit

struct LibraryView: View {
    @ObservedObject var model: CaptureModel
    @State private var query = ""
    @State private var results: [SyncChange] = []
    @State private var sources: [SyncChange] = []
    @State private var confirmingRemoval = false
    @AppStorage("libraryBudgetGB") private var budget = 20
    @AppStorage("downloadKnowledge") private var knowledge = false
    @AppStorage("download-journal_attachments") private var journalMedia = true
    @AppStorage("download-study_sources") private var studyMedia = true
    @AppStorage("download-paper_pages") private var paperPreviews = true
    @AppStorage("download-paper_page_images") private var paperImages = true
    @AppStorage("download-newspaper_frontpages") private var frontpages = true

    var body: some View {
        List {
            Section {
                Button("Download library over Wi-Fi") { Task { await model.downloadLibrary() } }
                    .disabled(model.downloadingLibrary || !model.signedIn)
                if model.downloadingLibrary {
                    ProgressView()
                    Button("Pause downloads") { model.pauseLibrary() }
                }
                Stepper("Media budget: \(budget) GB", value: $budget, in: 1...150, step: 5)
                    .disabled(model.downloadingLibrary)
                Button("Remove downloaded media", role: .destructive) { confirmingRemoval = true }
                    .disabled(model.downloadingLibrary)
                if let message = model.libraryMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Include in future downloads") {
                Toggle("Journal attachments", isOn: $journalMedia)
                Toggle("Study documents", isOn: $studyMedia)
                Toggle("Paper previews", isOn: $paperPreviews)
                Toggle("Pictures on paper pages", isOn: $paperImages)
                Toggle("Newspaper front pages", isOn: $frontpages)
                Toggle("Knowledge articles", isOn: $knowledge)
                Text("Changing selection keeps existing downloads. Wikipedia ZIM packages are not yet supported.")
                    .font(.footnote).foregroundStyle(.secondary)
            }.disabled(model.downloadingLibrary)
            Section("Documents") {
                ForEach(sources) { source in
                    NavigationLink(source.title) {
                        DownloadedMediaView(model: model, collection: "study_sources", id: source.id,
                                            mime: source.data?["contentType"]?.string ?? "", title: source.title)
                    }
                }
            }
            Section("Books and stories") {
                ForEach(query.isEmpty ? model.libraryRecords : results) { book in
                    NavigationLink { BookView(model: model, book: book) } label: {
                        VStack(alignment: .leading) {
                            Text(book.title)
                            Text(book.data?["author"]?.string ?? "").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Library")
        .task { sources = (try? model.replica.records(collection: "study_sources")) ?? [] }
        .onChange(of: model.downloadingLibrary) { _, _ in
            sources = (try? model.replica.records(collection: "study_sources")) ?? []
        }
        .confirmationDialog("Remove all downloaded media copies from this device?", isPresented: $confirmingRemoval) {
            Button("Remove downloaded media", role: .destructive) { model.removeLibraryMedia() }
        }
        .searchable(text: $query, prompt: "Search downloaded titles")
        .onChange(of: query) { _, value in
            do { results = try model.replica.records(collection: "fics", query: value) }
            catch { model.message = error.localizedDescription }
        }
    }
}

private struct BookView: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange
    @State private var chapters: [SyncChange] = []

    var body: some View {
        List {
            if let description = book.data?["description"]?.string, !description.isEmpty {
                Text(description)
            }
            if chapters.isEmpty {
                Text("No chapter text downloaded. Use the Library download button on Wi-Fi.")
            }
            ForEach(chapters) { chapter in
                NavigationLink {
                    ScrollView {
                        Text(chapter.data?["contentText"]?.string ?? "")
                            .font(.system(.body, design: .serif)).textSelection(.enabled)
                            .frame(maxWidth: 760, alignment: .leading).padding()
                    }.navigationTitle(chapter.title).navigationBarTitleDisplayMode(.inline)
                } label: { Text(chapter.title) }
            }
        }
        .navigationTitle(book.title)
        .task {
            do {
                chapters = try model.replica.relatedRecords(collection: "fic_chapters", field: "ficId", value: book.id)
                    .sorted { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) }
            } catch { model.message = error.localizedDescription }
        }
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
                    DisclosureGroup("Original transcript") { Text(original).textSelection(.enabled) }
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

    var body: some View {
        Group {
            if let file {
                if mime == "application/pdf" {
                    LocalPDFView(url: file)
                } else if mime.hasPrefix("text/html") {
                    LocalArticleView(url: file)
                } else if mime.hasPrefix("image/"), let picture = UIImage(contentsOfFile: file.path) {
                    ScrollView([.horizontal, .vertical]) { Image(uiImage: picture).resizable().scaledToFit() }
                } else if mime.hasPrefix("audio/") || mime.hasPrefix("video/") {
                    VideoPlayer(player: player)
                } else {
                    ContentUnavailableView("File downloaded", systemImage: "doc", description: Text("This file type does not yet have a native reader."))
                }
            } else {
                ContentUnavailableView("Not downloaded", systemImage: "arrow.down.circle",
                                       description: Text("Download the library on Wi-Fi. Archive videos remain on the server."))
            }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .task {
            do {
                file = try model.media.downloaded(collection: collection, id: id)
                if let file, mime.hasPrefix("audio/") || mime.hasPrefix("video/") { player = AVPlayer(url: file) }
            } catch { model.message = error.localizedDescription }
        }
        .onDisappear { player?.pause() }
    }
}

private struct LocalPDFView: UIViewRepresentable {
    let url: URL
    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.document = PDFDocument(url: url)
        return view
    }
    func updateUIView(_ view: PDFView, context: Context) {}
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
