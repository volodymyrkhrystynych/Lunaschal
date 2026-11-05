import SwiftUI
import LunaschalCore

/// What a book row's swipe or long press opens: a sheet over the library.
enum FicSheet: Identifiable {
    case review(SyncChange, dropping: Bool)
    case folders([SyncChange])

    var id: String {
        switch self {
        case .review(let book, let dropping): "review-\(book.id)-\(dropping)"
        case .folders(let books): "folders-" + books.map(\.id).joined(separator: ",")
        }
    }
}

extension View {
    /// The sheets a book list's swipe actions open.
    func ficSheets(_ sheet: Binding<FicSheet?>, model: CaptureModel) -> some View {
        self.sheet(item: sheet) { which in
            NavigationStack {
                switch which {
                case .review(let book, let dropping): FicReviewSheet(model: model, book: book, dropping: dropping)
                case .folders(let books): FicFolderSheet(model: model, books: books)
                }
            }
        }
    }
}

/// Five stars; tapping the lit one again clears the rating.
struct StarRating: View {
    @Binding var rating: Int?

    var body: some View {
        HStack(spacing: 10) {
            ForEach(1...5, id: \.self) { star in
                Button {
                    rating = rating == star ? nil : star
                } label: {
                    Image(systemName: star <= (rating ?? 0) ? "star.fill" : "star")
                        .font(.title2)
                        .foregroundStyle(star <= (rating ?? 0) ? Color.yellow : Color.secondary)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(star) star\(star == 1 ? "" : "s")")
                .accessibilityAddTraits(star == rating ? .isSelected : [])
                .accessibilityIdentifier("rating-star-\(star)")
            }
        }
    }
}

/// A book's rating and review. Dropping is the same sheet with a different
/// save: write down why, then the book moves to the Dropped shelf.
struct FicReviewSheet: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange
    let dropping: Bool
    @State private var rating: Int?
    @State private var review: String
    @Environment(\.dismiss) private var dismiss

    init(model: CaptureModel, book: SyncChange, dropping: Bool) {
        self.model = model
        self.book = book
        self.dropping = dropping
        _rating = State(initialValue: book.data?["rating"]?.int)
        _review = State(initialValue: book.data?["review"]?.string ?? "")
    }

    var body: some View {
        Form {
            Section { Text(book.title).font(.headline) }
            Section("Rating") { StarRating(rating: $rating) }
            Section(dropping ? "Why you're dropping it" : "Review") {
                TextField("Optional", text: $review, axis: .vertical)
                    .lineLimit(4...12)
                    .accessibilityIdentifier("fic-review-text")
            }
        }
        .navigationTitle(dropping ? "Drop this fic" : "Review")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button(dropping ? "Drop" : "Save") { save() }
                    .accessibilityIdentifier("fic-review-save")
            }
        }
    }

    private func save() {
        let text = review.trimmingCharacters(in: .whitespacesAndNewlines)
        var changes: [String: JSONValue] = [
            "rating": rating.map { .number(Double($0)) } ?? .null,
            "review": text.isEmpty ? .null : .string(text),
        ]
        if dropping { changes["shelf"] = .string(FicLibrary.Shelf.dropped.rawValue) }
        if model.editFics([book.id], changes) { dismiss() }
    }
}

/// Which folders a book is in, as a checklist. With several books selected,
/// a folder is ticked only when all of them are in it, and saving puts every
/// one of them in exactly the ticked folders.
struct FicFolderSheet: View {
    @ObservedObject var model: CaptureModel
    let books: [SyncChange]
    @State private var folders: [SyncChange] = []
    @State private var chosen: Set<String>
    @Environment(\.dismiss) private var dismiss

    init(model: CaptureModel, books: [SyncChange]) {
        self.model = model
        self.books = books
        let sets = books.map { Set($0.data?["folderIds"]?.strings ?? []) }
        _chosen = State(initialValue: sets.dropFirst().reduce(sets.first ?? []) { $0.intersection($1) })
    }

    var body: some View {
        List {
            if folders.isEmpty {
                Text("No folders yet. Folders made in the desktop library appear here after a sync.")
                    .foregroundStyle(.secondary)
            }
            ForEach(folders) { folder in
                Button {
                    if chosen.contains(folder.id) { chosen.remove(folder.id) } else { chosen.insert(folder.id) }
                } label: {
                    HStack {
                        Label(folder.title, systemImage: "folder")
                        Spacer()
                        if chosen.contains(folder.id) { Image(systemName: "checkmark").foregroundStyle(.tint) }
                    }
                }
                .foregroundStyle(.primary)
                .accessibilityAddTraits(chosen.contains(folder.id) ? .isSelected : [])
                .accessibilityIdentifier("fic-folder-\(folder.id)")
            }
        }
        .navigationTitle(books.count == 1 ? "Folders" : "Folders for \(books.count) books")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") {
                    let ids = folders.map(\.id).filter(chosen.contains)
                    if model.editFics(books.map(\.id), ["folderIds": .array(ids.map(JSONValue.string))]) { dismiss() }
                }
                .accessibilityIdentifier("fic-folders-save")
            }
        }
        .task {
            do {
                folders = try await model.reader.read { store in
                    try store.records(collection: "fic_folders", limit: 10_000)
                        .sorted { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) }
                }
            } catch { model.message = error.localizedDescription }
        }
    }
}

/// One button per shelf, for a swipe's or a selection's shelf menu.
struct ShelfButtons: View {
    let current: FicLibrary.Shelf?
    let choose: (FicLibrary.Shelf) -> Void

    var body: some View {
        ForEach(FicLibrary.Shelf.allCases) { shelf in
            Button(shelf.label, systemImage: shelf == current ? "checkmark" : shelf.systemImage) { choose(shelf) }
                .accessibilityIdentifier("shelf-\(shelf.rawValue)")
        }
    }
}

/// Small stars for a row: only as many as were given.
struct RatingStars: View {
    let rating: Int

    var body: some View {
        HStack(spacing: 1) {
            ForEach(0..<rating, id: \.self) { _ in Image(systemName: "star.fill") }
        }
        .font(.caption2)
        .foregroundStyle(.yellow)
        .accessibilityElement()
        .accessibilityLabel("Rated \(rating) of 5")
    }
}

/// Tags laid out left to right, wrapping onto as many lines as they need.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(0, rows.count - 1))
        let width = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var indices: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = [Row()]
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            if !rows[rows.count - 1].indices.isEmpty, rows[rows.count - 1].width + spacing + size.width > width {
                rows.append(Row())
            }
            var row = rows[rows.count - 1]
            row.width += (row.indices.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.indices.append(index)
            rows[rows.count - 1] = row
        }
        return rows.filter { !$0.indices.isEmpty }
    }
}

/// Every one of a book's site tags, as pills.
struct TagCloud: View {
    let tags: [String]

    var body: some View {
        FlowLayout {
            ForEach(tags, id: \.self) { tag in
                Text(tag)
                    .font(.caption)
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Color.secondary.opacity(0.15), in: Capsule())
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("book-tags")
    }
}
