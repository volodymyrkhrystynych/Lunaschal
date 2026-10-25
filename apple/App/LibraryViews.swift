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
    @State private var loads = 0

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
                LoadMoreRow(shown: records.count, total: count) { limit += 50; await refresh() }
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
        .task(id: model.downloadingLibrary) { await refresh() }
        .onChange(of: model.syncChanges) { _, changes in
            if changes.touches([category.collection]) { Task { await refresh() } }
        }
        .onChange(of: query) { _, _ in limit = 50 }
        .task(id: query) {
            if !query.isEmpty {
                try? await Task.sleep(nanoseconds: BookListView.typingPause)
                guard !Task.isCancelled else { return }
            }
            await refresh()
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

    /// Reads and decodes off the main thread; an older read finishing after
    /// a newer one (typing) is dropped.
    private func refresh() async {
        loads += 1
        let load = loads, collection = category.collection, query = query, limit = limit
        do {
            let (found, total) = try await model.reader.read { store in
                (try store.records(collection: collection, query: query, limit: limit),
                 try store.count(collection: collection, query: query))
            }
            guard load == loads else { return }
            records = found; count = total
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
    @AppStorage("download-food_media") private var foodMedia = true
    @AppStorage("download-spending_media") private var spendingMedia = true
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
                Toggle("Food photos and recordings", isOn: $foodMedia)
                Toggle("Receipts and spending recordings", isOn: $spendingMedia)
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

/// A book's chapter list, and the one place a chapter is opened from: the
/// reader's Chapters button comes back here rather than pushing another list,
/// so a book is never more than list → chapter deep however long it is read.
struct BookView: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange
    /// Opened from the library: carry straight on into the chapter the book
    /// was left at, as the desktop does, with this list one Back away.
    var opensResume = false
    @State private var resuming: ResumeTarget?
    @State private var triedResume = false
    @State private var chapters: [SyncChange] = []
    @State private var resume: (chapter: SyncChange, fraction: Double?)?
    @State private var bookmarks: [SyncChange] = []
    @State private var bookmarkEdits: [PendingEdit] = []
    @State private var onDevice = true
    /// The chapter-list tab picked; nil until the reader picks one, which
    /// shows the category of the chapter they'd continue.
    @State private var category: String?
    /// While the server is being asked; the chapters themselves arrive by sync.
    @State private var askingForUpdate = false
    /// The book as last read from the replica, with this device's waiting
    /// edits applied; `book` is only what the list handed over.
    @State private var latest: SyncChange?
    @State private var read: Set<String> = []
    @State private var reactions: [String: ChapterReaction] = [:]
    @State private var sheet: FicSheet?
    @Environment(\.openURL) private var openURL

    private var shown: SyncChange { latest ?? book }

    var body: some View {
        List {
            if let description = book.data?["description"]?.string, !description.isEmpty {
                Text(description)
            }
            organizeSection
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
            if FicSources.isUpdatable(book.data?["sourceType"]?.string) {
                Section {
                    Button("Get latest chapters", systemImage: "arrow.clockwise") { askForUpdate(deep: false) }
                        .accessibilityIdentifier("fic-check-updates")
                    Button("Re-read edited chapters", systemImage: "arrow.triangle.2.circlepath") { askForUpdate(deep: true) }
                        .accessibilityIdentifier("fic-reread-chapters")
                } footer: {
                    if askingForUpdate { ProgressView() }
                    else { Text("The server fetches them from the site; they arrive with the next sync.") }
                }
                .disabled(askingForUpdate)
            }
            if !bookmarks.isEmpty {
                Section("Bookmarks") {
                    ForEach(bookmarks) { bookmark in
                        if let chapter = chapters.first(where: { $0.id == bookmark.data?["chapterId"]?.string }) {
                            NavigationLink {
                                TextChapterReader(owner: model, chapter: chapter,
                                    initialFraction: bookmark.data?["scrollPosition"]?.number, book: book)
                            } label: {
                                Label("\(bookmark.data?["type"]?.string == "continue" ? "Continue" : "Favorite"): \(chapter.title)",
                                      systemImage: bookmark.data?["type"]?.string == "continue" ? "play.fill" : "star.fill")
                            }
                            .swipeActions {
                                Button("Remove", role: .destructive) {
                                    do { try model.replica.deleteBookmark(bookmark); Task { await refreshChapters() }; model.requestSync() }
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
                                do { try model.replica.resolve(edit, keepLocal: false); Task { await refreshChapters() } }
                                catch { model.message = error.localizedDescription }
                            }
                        }
                    }
                }
            }
            chapterList
        }
        .navigationTitle(book.title)
        .ficSheets($sheet, model: model)
        .onChange(of: model.libraryRevision) { _, _ in Task { await refreshChapters() } }
        .navigationDestination(item: $resuming) { target in
            TextChapterReader(owner: model, chapter: target.chapter, initialFraction: target.fraction, book: book)
        }
        .task(id: model.downloadingLibrary) { await refreshChapters() }
        .onChange(of: model.ficDownloadRevision) { _, _ in Task { await refreshChapters() } }
        .onAppear {
            do { try model.replica.markBookOpened(book.id) }
            catch { model.message = error.localizedDescription }
            model.ensureFicOnDevice(book)
        }
        .onChange(of: model.syncChanges) { _, changes in
            if changes.touches(["fics", "fic_chapters", "fic_bookmarks", "fic_chapter_reads", "fic_chapter_reactions"]) {
                Task { await refreshChapters() }
            }
        }
    }

    private func askForUpdate(deep: Bool) {
        askingForUpdate = true
        Task {
            await model.checkFicForUpdates(book, deep: deep)
            askingForUpdate = false
        }
    }

    /// One tab per threadmark category (Threadmarks, Sidestory, Informational…)
    /// when the book has more than one; a plain list otherwise.
    @ViewBuilder
    private var chapterList: some View {
        let groups = ChapterCategories.groups(chapters)
        if groups.count > 1 {
            let picked = category ?? resume.map { ChapterCategories.category(of: $0.chapter) }
            let shown = groups.first { $0.category == picked } ?? groups[0]
            Section {
                ForEach(shown.chapters) { chapterLink($0) }
            } header: {
                categoryTabs(groups, shown: shown.category)
            }
        } else {
            ForEach(chapters) { chapterLink($0) }
        }
    }

    private func chapterLink(_ chapter: SyncChange) -> some View {
        let isRead = read.contains(chapter.id)
        return NavigationLink {
            TextChapterReader(owner: model, chapter: chapter, book: book)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: isRead ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(isRead ? Color.green : Color.secondary.opacity(0.5))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(chapter.title).foregroundStyle(isRead ? .secondary : .primary)
                    reactionLine(reactions[chapter.id])
                }
            }
        }
        .accessibilityValue(isRead ? "Read" : "Unread")
        .accessibilityIdentifier("chapter-row-\(chapter.id)")
        .swipeActions(edge: .leading) {
            Button(isRead ? "Unread" : "Read", systemImage: isRead ? "circle" : "checkmark.circle") {
                model.markChapters(bookID: book.id, chapterIDs: [chapter.id], read: !isRead)
            }
            .tint(isRead ? .gray : .green)
        }
        .contextMenu {
            Button(isRead ? "Mark unread" : "Mark read", systemImage: isRead ? "circle" : "checkmark.circle") {
                model.markChapters(bookID: book.id, chapterIDs: [chapter.id], read: !isRead)
            }
            Button("Mark all up to here read", systemImage: "checkmark.circle.badge.questionmark") {
                model.markChapters(bookID: book.id, chapterIDs: FicLibrary.chapters(through: chapter.id, in: chapters), read: true)
            }
            if FicLibrary.isLikeable(book: book, chapter: chapter) {
                let liked = reactions[chapter.id]?.isLiked ?? false
                Button(liked ? "Unlike" : "Like", systemImage: liked ? "heart.slash" : "heart") {
                    model.likeChapter(bookID: book.id, chapterID: chapter.id, liked: !liked)
                }
                Button("Re-get this chapter", systemImage: "arrow.down.doc") {
                    Task { await model.refetchChapter(chapter) }
                }
            }
            if let link = chapter.data?["sourceUrl"]?.string, let url = URL(string: link) {
                Button("Open on site", systemImage: "safari") {
                    // Going to the forum is going there to like it.
                    if FicLibrary.isLikeable(book: book, chapter: chapter) {
                        model.likeChapter(bookID: book.id, chapterID: chapter.id, liked: true, assumed: true)
                    }
                    openURL(url)
                }
            }
        }
    }

    /// The forum's evidence for one chapter: the user's reaction, or that it
    /// was checked and there was none. Nothing when it was never checked.
    @ViewBuilder
    private func reactionLine(_ reaction: ChapterReaction?) -> some View {
        if let reaction {
            Group {
                if let error = reaction.error, reaction.pending != nil {
                    Label("Not sent: \(error)", systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                } else if reaction.pending == "like" {
                    Label("Liked · waiting to send", systemImage: "clock.arrow.circlepath").foregroundStyle(.tint)
                } else if reaction.pending == "unlike" {
                    Label("Like removed · waiting to send", systemImage: "heart.slash").foregroundStyle(.secondary)
                } else if let name = reaction.reaction {
                    Label(reaction.source == "assumed" ? "\(name) · opened on site" : name,
                          systemImage: "hand.thumbsup.fill").foregroundStyle(.tint)
                } else {
                    Text("Not reacted · checked \(reaction.checkedAt.formatted(date: .abbreviated, time: .omitted))")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.caption)
        }
    }

    /// Shelf, rating, review and every tag, with the actions that change them.
    @ViewBuilder
    private var organizeSection: some View {
        let shelf = FicLibrary.Shelf(book: shown)
        Section {
            Menu {
                ShelfButtons(current: shelf) { model.editFics([book.id], ["shelf": .string($0.rawValue)]) }
            } label: {
                LabeledContent("Shelf") { Label(shelf.label, systemImage: shelf.systemImage) }
            }
            .accessibilityIdentifier("book-shelf-menu")
            Button { sheet = .review(shown, dropping: false) } label: {
                VStack(alignment: .leading, spacing: 4) {
                    if let rating = shown.data?["rating"]?.int, rating > 0 { RatingStars(rating: rating) }
                    else { Text("Rate and review").foregroundStyle(.tint) }
                    if let review = shown.data?["review"]?.string, !review.isEmpty {
                        Text(review).font(.callout).foregroundStyle(.primary)
                    }
                }
            }
            .accessibilityIdentifier("book-review")
            Button("Folders", systemImage: "folder") { sheet = .folders([shown]) }
            let tags = shown.data?["tags"]?.strings ?? []
            if !tags.isEmpty { TagCloud(tags: tags) }
        } footer: {
            let story = chapters.filter(FicLibrary.isMainStory)
            let total = story.count
            if total > 0 {
                let done = story.filter { read.contains($0.id) }.count
                let reacted = reactions.values.filter { $0.reaction != nil }.count
                Text("\(done) of \(total) chapters read" + (reactions.isEmpty ? "" : " · reacted on \(reacted)"))
            }
        }
    }

    private func categoryTabs(_ groups: [ChapterGroup], shown: String) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(groups) { group in
                    let selected = group.category == shown
                    Button { category = group.category } label: {
                        Text("\(group.title) \(group.chapters.count)")
                            .font(.subheadline.weight(selected ? .semibold : .regular))
                            .padding(.horizontal, 12).padding(.vertical, 6)
                            .foregroundStyle(selected ? Color.white : Color.primary)
                            .background(selected ? Color.accentColor : Color.secondary.opacity(0.15), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(group.title)
                    .accessibilityValue("\(group.chapters.count) chapters")
                    .accessibilityAddTraits(selected ? .isSelected : [])
                    .accessibilityIdentifier("chapter-tab-\(group.category)")
                }
            }
            .padding(.vertical, 4)
        }
        .textCase(nil)
    }

    /// The chapter list, resume point and bookmarks, read off the main thread.
    private func refreshChapters() async {
        let id = book.id
        do {
            let loaded = try await model.reader.read { store in
                (try store.chapterOutline(bookID: id), try store.resumePoint(bookID: id),
                 try store.bookmarks(bookID: id), try store.bookmarkEdits(bookID: id))
            }
            let tracking = try await model.reader.read { store in
                (try store.record(collection: "fics", id: id).map(store.overlaid),
                 try store.readChapterIDs(bookID: id), try store.reactions(bookID: id))
            }
            (chapters, resume, bookmarks, bookmarkEdits) = loaded
            (latest, read, reactions) = tracking
            onDevice = model.isFicOnDevice(book)
        } catch { model.message = error.localizedDescription }
        // Only the first read decides: coming back to this list must not send
        // the reader straight back in, and nor should a download finishing
        // while the list is being looked at. With no chapter text on the
        // device there is nothing to resume, and the list says why.
        if opensResume, !triedResume {
            triedResume = true
            if let resume {
                var instant = Transaction()
                instant.disablesAnimations = true
                withTransaction(instant) { resuming = ResumeTarget(chapter: resume.chapter, fraction: resume.fraction) }
            }
        }
    }
}

/// The chapter a book reopens at, as a navigation destination.
private struct ResumeTarget: Hashable {
    let chapter: SyncChange
    let fraction: Double?
    static func == (a: Self, b: Self) -> Bool { a.chapter.id == b.chapter.id && a.fraction == b.fraction }
    func hash(into hasher: inout Hasher) { hasher.combine(chapter.id) }
}

struct JournalRecordView: View {
    @ObservedObject var model: CaptureModel
    let record: SyncChange
    @State private var content = ""
    @State private var title = ""
    @State private var editing = false
    @State private var confirmingDelete = false
    @State private var attachments: [SyncChange] = []
    @State private var links: [String] = []
    @State private var typedLink = ""
    @FocusState private var typing: Bool
    @Environment(\.dismiss) private var dismiss

    private var staged: CaptureDraft { model.entryDrafts[record.id] ?? CaptureDraft() }
    private var changes: [String: JSONValue] {
        var out: [String: JSONValue] = [:]
        if content != record.data?["content"]?.string ?? "" { out["content"] = .string(content) }
        if title != record.data?["title"]?.string ?? "" { out["title"] = .string(title) }
        return out
    }
    private var canSave: Bool {
        !changes.isEmpty || !staged.isEmpty || !links.isEmpty
            || !typedLink.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        Form {
            if editing {
                Section {
                    TextField("Title", text: $title).focused($typing)
                    TextEditor(text: $content).frame(minHeight: 240).focused($typing)
                        .accessibilityLabel("Entry text")
                    // The Capture tab's buttons: everything they make waits in
                    // this entry's draft until Save.
                    AttachmentButtons(model: model, recorder: model.recorder, entryID: record.id) { typing = false }
                }
                if !staged.isEmpty {
                    Section("Adding") { StagedAttachmentRows(model: model, recorder: model.recorder, draft: staged) }
                }
                YouTubeLinksSection(model: model, links: $links, typed: $typedLink, typing: $typing)
                Section {
                    Button("Save edit on this device") { save() }.disabled(!canSave)
                    Button("Cancel", role: .cancel) { cancel() }
                }
                Section {
                    Button("Delete entry", role: .destructive) { confirmingDelete = true }
                }
            } else {
                Text(record.data?["content"]?.string ?? "").textSelection(.enabled)
                if let original = record.data?["rawContent"]?.string, !original.isEmpty {
                    DisclosureGroup("Original text and dictation") { Text(original).textSelection(.enabled) }
                }
                PendingAdditionsRow(additions: model.pendingAdditions(to: record.id))
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
        .toolbar {
            if !editing { ToolbarItem(placement: .primaryAction) { Button("Edit") { startEditing() } } }
        }
        // An edit left with something staged (the app was closed mid-edit)
        // opens where it was rather than hiding the staged clips.
        .onAppear { if !editing && !staged.isEmpty { startEditing() } }
        .task(id: model.syncing) {
            attachments = (try? model.replica.relatedRecords(collection: "journal_attachments", field: "entryId", value: record.id)) ?? []
        }
        .alert("Delete this entry?", isPresented: $confirmingDelete) {
            Button("Delete", role: .destructive) {
                // Whatever was staged for it goes too: it has nowhere left to upload.
                model.discardEdit(record.id)
                model.delete(record); dismiss()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It is deleted on the server the next time this device syncs. This cannot be undone.")
        }
    }

    private func startEditing() {
        content = record.data?["content"]?.string ?? ""
        title = record.data?["title"]?.string ?? ""
        editing = true
    }

    private func save() {
        // A URL typed but not yet added still belongs to this edit.
        guard YouTubeLinksSection.add(typed: $typedLink, to: $links, model: model) else { return }
        if model.saveEdit(record, changes: changes, youtubeURLs: links) { dismiss() }
    }

    private func cancel() {
        model.discardEdit(record.id)
        links = []; typedLink = ""; editing = false
    }
}

/// How many saved additions to an entry are still waiting to upload.
struct PendingAdditionsRow: View {
    let additions: [Capture]

    var body: some View {
        let count = additions.reduce(0) { $0 + $1.files.count + $1.clips.count + $1.links.count }
        if count > 0 {
            let failed = additions.contains { $0.state == .failed }
            let text = "\(count) added \(count == 1 ? "item" : "items") "
                + (failed ? "could not upload" : "waiting to upload")
            Label(text, systemImage: failed ? "exclamationmark.triangle" : "arrow.up.circle")
                .font(.footnote).foregroundStyle(failed ? .orange : .secondary)
                // Read as the sentence, not the icon's name.
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(text)
                .accessibilityIdentifier("pending-additions")
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
                Text(edit.operation.action == "delete" ? "Delete entry" : Self.summary(edit.operation.data))
                    .textSelection(.enabled)
            }
            if let error = edit.error { Text(error).foregroundStyle(.orange) }
            if let current = edit.conflict {
                Section("Server version") {
                    Text(current.deleted ? "Deleted on server" : current.data.map(Self.summary) ?? "").textSelection(.enabled)
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

    /// An entry's words, or a meal's dish, place and notes.
    static func summary(_ data: [String: JSONValue]) -> String {
        if let content = data["content"]?.string { return content }
        let meal = ["dish", "place", "notes"].compactMap { data[$0]?.string }.filter { !$0.isEmpty }
        return meal.isEmpty ? "Title or tags changed" : meal.joined(separator: "\n")
    }
}
