import SwiftUI
import LunaschalCore

struct LibraryView: View {
    @ObservedObject var model: CaptureModel
    @State private var filter = BookFilter()
    @State private var books: [SyncChange] = []
    @State private var folders: [SyncChange] = []
    @State private var tags: [String] = []
    @State private var limit = 50
    @State private var count = 0

    private let sources = [
        ("", "All sources"), ("forums.spacebattles.com", "SpaceBattles"),
        ("forums.sufficientvelocity.com", "Sufficient Velocity"),
        ("forum.questionablequesting.com", "Questionable Questing"),
        ("fanfiction", "FanFiction.net"), ("ao3", "AO3"), ("patreon", "Patreon"),
        ("epub", "EPUB"), ("docx", "DOCX"), ("pdf", "PDF"),
    ]

    var body: some View {
        List {
            ForEach(books) { book in
                NavigationLink { BookView(model: model, book: book) } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(book.title).font(.headline).lineLimit(2)
                        if let author = book.data?["author"]?.string, !author.isEmpty {
                            Text(author).font(.subheadline).foregroundStyle(.secondary)
                        }
                        Text("\(Int(book.data?["chapterCount"]?.number ?? 0)) chapters · \(Int(book.data?["wordCount"]?.number ?? 0)) words")
                            .font(.caption).foregroundStyle(.secondary)
                        let tags = book.data?["tags"]?.strings ?? []
                        if !tags.isEmpty {
                            Text(tags.prefix(4).joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if books.count < count {
                Button("Load more (\(books.count) of \(count))") { limit += 50; refresh() }
            }
        }
        .overlay {
            if books.isEmpty {
                ContentUnavailableView("No matching books", systemImage: "books.vertical",
                    description: Text("Try another search or filter, or sync your library in Settings."))
            }
        }
        .navigationTitle("Library")
        .searchable(text: $filter.query, placement: .navigationBarDrawer(displayMode: .always),
                    prompt: "Search titles and tags")
        .toolbar {
            Menu {
                Picker("Source", selection: $filter.source) {
                    ForEach(sources, id: \.0) { value, label in Text(label).tag(value) }
                }
                Picker("Folder", selection: $filter.folder) {
                    Text("All folders").tag("")
                    Text("Unsorted").tag("unsorted")
                    ForEach(folders) { folder in Text(folder.title).tag(folder.id) }
                }
                Picker("Tag", selection: $filter.tag) {
                    Text("All tags").tag("")
                    ForEach(tags, id: \.self) { Text($0).tag($0) }
                }
                Picker("Bookmarks", selection: $filter.bookmark) {
                    Text("All books").tag("")
                    Text("Favorites").tag("favorite")
                    Text("Continue reading").tag("continue")
                }
                Picker("Sort", selection: $filter.sort) {
                    Text("Latest chapter").tag("activity")
                    Text("Recently read").tag("recent")
                    Text("Title").tag("title")
                }
                Button("Reset filters") { filter = BookFilter() }
            } label: { Label("Filter books", systemImage: filter == BookFilter() ? "line.3.horizontal.decrease" : "line.3.horizontal.decrease.circle.fill") }
        }
        .task(id: model.downloadingLibrary) { refresh() }
        .onAppear { refresh() }
        .onChange(of: model.syncing) { _, syncing in if !syncing { refresh() } }
        .onChange(of: filter) { _, _ in limit = 50; refresh() }
    }

    private func refresh() {
        do {
            let result = try model.replica.books(filter: filter, limit: limit)
            books = result.records; count = result.count
            folders = try model.replica.records(collection: "fic_folders", limit: 10_000)
                .sorted { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) }
            tags = try model.replica.bookTags()
        } catch { model.message = error.localizedDescription }
    }
}
