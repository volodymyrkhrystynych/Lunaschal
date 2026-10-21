import SwiftUI
import LunaschalCore

/// The Library tab: books by provider, or books by folder. Library mode is
/// about what the sites updated most recently (the chapter's own posting
/// date, never when it was downloaded); Folders mode is a Finder-style list
/// you go into and come back out of.
struct LibraryView: View {
    @ObservedObject var model: CaptureModel
    @AppStorage("libraryMode") private var mode = "library"

    var body: some View {
        Group {
            if mode == "folders" { FolderListView(model: model) }
            else { BookListView(model: model, showsProviders: true) }
        }
        .navigationTitle("Library")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Picker("Library view", selection: $mode) {
                    Text("Library").tag("library")
                    Text("Folders").tag("folders")
                }
                .pickerStyle(.segmented)
                .fixedSize()
                .accessibilityIdentifier("library-mode")
            }
        }
    }
}

/// One list of books. With a `folder` it is that folder's contents; without,
/// it is the whole library, narrowed by the provider pills.
struct BookListView: View {
    @ObservedObject var model: CaptureModel
    var folder: String? = nil
    var showsProviders = false
    @State private var filter = BookFilter()
    @State private var books: [SyncChange] = []
    @State private var limit = 50
    @State private var count = 0
    @State private var onDevice: Set<String> = []
    @State private var reactions: [String: ReactionSummary] = [:]
    /// Bumped per refresh; an older one finishing late is dropped.
    @State private var loads = 0
    @State private var sheet: FicSheet?
    /// The book a swipe asked to move to another shelf.
    @State private var shelving: SyncChange?
    @State private var editMode: EditMode = .inactive
    @State private var selection: Set<String> = []

    /// What a pass has to touch for this list to read the library again.
    static let shows: Set<String> = ["fics", "fic_folders", "fic_bookmarks", "fic_chapters",
                                     "fic_chapter_reads", "fic_chapter_reactions"]

    static let providers = [
        ("", "All"), ("forums.spacebattles.com", "SpaceBattles"),
        ("forums.sufficientvelocity.com", "Sufficient Velocity"),
        ("forum.questionablequesting.com", "Questionable Questing"),
        ("fanfiction", "FanFiction.net"), ("ao3", "AO3"), ("patreon", "Patreon"),
        ("epub", "EPUB"), ("docx", "DOCX"), ("pdf", "PDF"),
    ]

    var body: some View {
        List(selection: $selection) {
            if model.ficDownload != nil {
                Section { FicDownloadBanner(model: model) }
            }
            ForEach(books) { book in
                NavigationLink { BookReaderEntry(model: model, book: book) } label: {
                    BookRow(book: book, downloaded: onDevice.contains(book.id), reactions: reactions[book.id])
                }
                    // Left swipe: the things you do to a book you're judging.
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button("Drop", systemImage: "archivebox") { sheet = .review(book, dropping: true) }
                            .tint(.red)
                            .accessibilityIdentifier("swipe-drop")
                        Button("Review", systemImage: "star") { sheet = .review(book, dropping: false) }
                            .tint(.orange)
                            .accessibilityIdentifier("swipe-review")
                        Button("Folders", systemImage: "folder") { sheet = .folders([book]) }
                            .tint(.blue)
                            .accessibilityIdentifier("swipe-folders")
                    }
                    .swipeActions(edge: .leading) {
                        Button("Shelf", systemImage: "books.vertical") { shelving = book }
                            .tint(.indigo)
                            .accessibilityIdentifier("swipe-shelf")
                    }
                    .contextMenu {
                        Button("Review", systemImage: "star") { sheet = .review(book, dropping: false) }
                        Button("Folders", systemImage: "folder") { sheet = .folders([book]) }
                        Menu("Shelf", systemImage: "books.vertical") {
                            ShelfButtons(current: FicLibrary.Shelf(book: book)) { model.editFics([book.id], ["shelf": .string($0.rawValue)]) }
                        }
                        Button("Drop…", systemImage: "archivebox", role: .destructive) { sheet = .review(book, dropping: true) }
                        if FicSources.isUpdatable(book.data?["sourceType"]?.string) {
                            Button("Check for updates", systemImage: "arrow.clockwise") {
                                Task { await model.checkFicForUpdates(book, deep: false) }
                            }
                            Button("Re-read edited chapters", systemImage: "arrow.triangle.2.circlepath") {
                                Task { await model.checkFicForUpdates(book, deep: true) }
                            }
                        }
                    }
            }
            if books.count < count {
                LoadMoreRow(shown: books.count, total: count) { limit += 50; await refresh() }
            }
        }
        .overlay {
            if books.isEmpty {
                ContentUnavailableView(folder == nil ? "No matching books" : "No books in this folder",
                    systemImage: "books.vertical",
                    description: Text("Try another search or filter, or sync your library in Settings."))
            }
        }
        .environment(\.editMode, $editMode)
        .ficSheets($sheet, model: model)
        .confirmationDialog("Move to shelf", isPresented: Binding(get: { shelving != nil }, set: { if !$0 { shelving = nil } }),
                            titleVisibility: .visible, presenting: shelving) { book in
            ShelfButtons(current: FicLibrary.Shelf(book: book)) { model.editFics([book.id], ["shelf": .string($0.rawValue)]) }
        }
        .searchable(text: $filter.query, placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Search titles and tags")
        .safeAreaInset(edge: .top, spacing: 0) {
            if showsProviders {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Self.providers, id: \.0) { value, label in
                            Button(label) { filter.source = value }
                                .buttonStyle(.bordered)
                                .buttonBorderShape(.capsule)
                                .controlSize(.small)
                                .tint(filter.source == value ? .accentColor : .secondary)
                                .accessibilityAddTraits(filter.source == value ? .isSelected : [])
                        }
                    }
                    .padding(.horizontal).padding(.vertical, 6)
                }
                .background(.bar)
                .accessibilityIdentifier("library-providers")
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button(editMode.isEditing ? "Done" : "Select") {
                    editMode = editMode.isEditing ? .inactive : .active
                    selection = []
                }
                .accessibilityIdentifier("library-select")
            }
            if editMode.isEditing {
                ToolbarItemGroup(placement: .bottomBar) {
                    Button("Folders…", systemImage: "folder") {
                        sheet = .folders(books.filter { selection.contains($0.id) })
                    }
                    .accessibilityIdentifier("selection-folders")
                    Spacer()
                    Text("\(selection.count) selected").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Menu {
                        ShelfButtons(current: nil) { shelf in
                            if model.editFics(Array(selection), ["shelf": .string(shelf.rawValue)]) { selection = [] }
                        }
                    } label: { Label("Shelf", systemImage: "books.vertical") }
                    .accessibilityIdentifier("selection-shelf")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                if model.refreshingFics {
                    ProgressView()
                } else {
                    Button("Refresh library on server", systemImage: "arrow.clockwise") {
                        Task { await model.refreshFicsOnServer() }
                    }
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Picker("Bookmarks", selection: $filter.bookmark) {
                        Text("All books").tag("")
                        Text("Favorites").tag("favorite")
                        Text("Continue reading").tag("continue")
                    }
                    Picker("Shelf", selection: $filter.shelf) {
                        Text("Active").tag(FicLibrary.activeShelves)
                        ForEach(FicLibrary.Shelf.allCases) { Text($0.label).tag($0.rawValue) }
                        Text("Archived (finished & dropped)").tag(FicLibrary.archivedShelves)
                        Text("Everything").tag(FicLibrary.allShelves)
                    }
                    .pickerStyle(.menu)
                    Picker("Rating", selection: $filter.minRating) {
                        Text("Any rating").tag(0)
                        ForEach([5, 4, 3, 2, 1], id: \.self) { Text(String(repeating: "★", count: $0) + ($0 < 5 ? " & up" : "")).tag($0) }
                    }
                    .pickerStyle(.menu)
                    Toggle("Unread chapters only", isOn: $filter.unreadOnly)
                    Picker("Sort", selection: $filter.sort) {
                        Text("Latest update on the site").tag("activity")
                        Text("Recently read").tag("recent")
                        Text("Title").tag("title")
                        Text("Rating").tag("rating")
                    }
                    Button("Reset filters") { filter = fresh() }
                } label: {
                    Label("Filter books", systemImage: filter == fresh()
                          ? "line.3.horizontal.decrease" : "line.3.horizontal.decrease.circle.fill")
                }
            }
        }
        .task(id: model.downloadingLibrary) { await refresh() }
        // Only when a pass changed books, folders, bookmarks or chapters. The
        // list's rows keep their ids, so it stays where it was scrolled to.
        .onChange(of: model.syncChanges) { _, changes in
            if changes.touches(Self.shows) { Task { await refresh() } }
        }
        .onChange(of: model.libraryRevision) { _, _ in Task { await refresh() } }
        .onChange(of: filter) { _, _ in limit = 50 }
        // Typing searches once it pauses, not on every keystroke.
        .task(id: filter) {
            try? await Task.sleep(nanoseconds: Self.typingPause)
            guard !Task.isCancelled else { return }
            await refresh()
        }
        // When the fic downloading changes, the one before it has finished.
        .onChange(of: model.ficDownload?.id) { _, _ in onDevice = model.ficsOnDevice(books) }
    }

    private func fresh() -> BookFilter { BookFilter() }

    static let typingPause: UInt64 = 300_000_000

    /// Reads and decodes off the main thread.
    private func refresh() async {
        loads += 1
        let load = loads, limit = limit
        // The folder is where this list is, not a filter to clear.
        var query = filter
        query.folder = folder ?? ""
        do {
            let (result, counts, summaries) = try await model.reader.read { store in
                let result = try store.books(filter: query, limit: limit)
                let text = result.records.filter { $0.data?["sourceType"]?.string != "pdf" }
                return (result, try store.chapterCounts(bookIDs: text.map(\.id)),
                        try store.reactionSummaries(bookIDs: result.records.map(\.id)))
            }
            guard load == loads else { return }
            books = result.records; count = result.count; reactions = summaries
            selection.formIntersection(books.map(\.id))
            onDevice = model.ficsOnDevice(books, chapterCounts: counts)
        } catch { model.message = error.localizedDescription }
    }
}

/// The bottom of a paged list: scrolling it into view loads the next page.
/// Keyed on `shown`, so a page too short to push it off screen loads the
/// next one too instead of stopping until the list is scrolled again.
struct LoadMoreRow: View {
    let shown: Int
    let total: Int
    let load: () async -> Void

    var body: some View {
        HStack(spacing: 8) {
            ProgressView()
            Text("Loading \(shown) of \(total)…").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .listRowSeparator(.hidden)
        .accessibilityIdentifier("load-more")
        .task(id: shown) { await load() }
    }
}

struct BookRow: View {
    let book: SyncChange
    var downloaded = false
    var reactions: ReactionSummary? = nil

    /// When the site last posted a chapter: unix seconds from the server's
    /// `latest_activity`, which is the chapters' own dates, not our download.
    private var updated: Date? {
        switch book.data?["latestActivity"] {
        case .number(let seconds)? where seconds > 0: return Date(timeIntervalSince1970: seconds)
        case .string(let text)?: return ISO8601DateFormatter().date(from: text)
        default: return nil
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if downloaded {
                    Image(systemName: "arrow.down.circle.fill")
                        .foregroundStyle(.green)
                        .accessibilityLabel("Downloaded")
                        .accessibilityIdentifier("book-downloaded")
                }
                Text(book.title).font(.headline).lineLimit(2)
            }
            if let author = book.data?["author"]?.string, !author.isEmpty {
                Text(author).font(.subheadline).foregroundStyle(.secondary)
            }
            if let updated {
                Text("Updated \(updated.formatted(.relative(presentation: .named)))")
                    .font(.caption).foregroundStyle(.tint)
            }
            Text("\(Int(book.data?["chapterCount"]?.number ?? 0)) chapters · \(Int(book.data?["wordCount"]?.number ?? 0)) words")
                .font(.caption).foregroundStyle(.secondary)
            organized
            let tags = book.data?["tags"]?.strings ?? []
            if !tags.isEmpty {
                Text(tags.prefix(4).joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

extension BookRow {
    /// Shelf, rating, unread chapters and reaction proof, on one line; the
    /// line is left out when there's nothing to say.
    @ViewBuilder
    var organized: some View {
        let shelf = FicLibrary.Shelf(book: book)
        let rating = book.data?["rating"]?.int ?? 0
        let unread = book.data?["unreadCount"]?.int ?? 0
        if shelf != .reading || rating > 0 || unread > 0 || (reactions?.checked ?? 0) > 0 {
            HStack(spacing: 8) {
                if shelf != .reading {
                    Label(shelf.label, systemImage: shelf.systemImage)
                        .accessibilityIdentifier("book-shelf")
                }
                if rating > 0 { RatingStars(rating: rating) }
                if unread > 0 {
                    Text("\(unread) unread")
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Color.accentColor.opacity(0.15), in: Capsule())
                        .accessibilityIdentifier("book-unread")
                }
                if let reactions, reactions.checked > 0 {
                    Label("\(reactions.reacted)/\(reactions.checked)", systemImage: "hand.thumbsup")
                        .accessibilityLabel("Reacted on \(reactions.reacted) of \(reactions.checked) checked chapters")
                        .accessibilityIdentifier("book-reactions")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }
}

/// Folders as folders: going into one pushes its books, and Back returns here.
struct FolderListView: View {
    @ObservedObject var model: CaptureModel
    @State private var folders: [SyncChange] = []
    @State private var counts: [String: Int] = [:]

    var body: some View {
        List {
            ForEach(folders) { folder in
                link(id: folder.id, title: folder.title, icon: "folder")
            }
            link(id: "unsorted", title: "Unsorted", icon: "tray")
        }
        .overlay {
            if folders.isEmpty && counts["unsorted", default: 0] == 0 {
                ContentUnavailableView("No folders", systemImage: "folder",
                    description: Text("Folders made in the desktop library appear here after a sync."))
            }
        }
        .task(id: model.downloadingLibrary) { await refresh() }
        .onChange(of: model.syncChanges) { _, changes in
            if changes.touches(["fics", "fic_folders"]) { Task { await refresh() } }
        }
        .onChange(of: model.libraryRevision) { _, _ in Task { await refresh() } }
    }

    private func link(id: String, title: String, icon: String) -> some View {
        NavigationLink {
            BookListView(model: model, folder: id)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
        } label: {
            LabeledContent {
                Text("\(counts[id, default: 0])")
            } label: {
                Label(title, systemImage: icon)
            }
        }
        .accessibilityIdentifier("folder-\(id)")
    }

    private func refresh() async {
        do {
            (folders, counts) = try await model.reader.read { store in
                (try store.records(collection: "fic_folders", limit: 10_000)
                    .sorted { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) },
                 try store.folderCounts())
            }
        } catch { model.message = error.localizedDescription }
    }
}

/// Opening a book, as the desktop does it: straight into the chapter it was
/// left at (see `ReplicaStore.resumePoint`). The chapter list is one tap away
/// in the reader's toolbar.
struct BookReaderEntry: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange
    @State private var resume: (chapter: SyncChange, fraction: Double?)?
    @State private var loaded = false

    var body: some View {
        Group {
            if book.data?["sourceType"]?.string == "pdf" {
                DownloadedMediaView(model: model, collection: "fics", id: book.id,
                                    mime: "application/pdf", title: book.title)
            } else if let resume {
                TextChapterReader(owner: model, chapter: resume.chapter, initialFraction: resume.fraction, book: book)
            } else if loaded {
                // No chapter text on this device: the book page says why.
                BookView(model: model, book: book)
            } else {
                // Never empty: a Group with no view in it runs no `.task`, so
                // the book never loaded, never started downloading, and the
                // screen stayed blank.
                ProgressView()
            }
        }
        .task {
            guard !loaded else { return }
            do {
                try model.replica.markBookOpened(book.id)
                resume = try model.replica.resumePoint(bookID: book.id)
            } catch { model.message = error.localizedDescription }
            loaded = true
            // Straight to the front of the queue: this is the book being looked at.
            model.ensureFicOnDevice(book)
        }
    }
}
