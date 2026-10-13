import Foundation

/// One threadmark category of a book (Threadmarks, Sidestory, Informational…)
/// and its chapters in reading order.
public struct ChapterGroup: Equatable, Identifiable {
    public let category: String
    public let chapters: [SyncChange]
    public var id: String { category }

    /// The tab's label: the server's default category is stored lowercase.
    public var title: String { category.prefix(1).uppercased() + category.dropFirst() }
}

/// A book's chapters split by XenForo threadmark category. Positions run on
/// through every category, so a book's outline is Threadmarks, then Sidestory,
/// then Informational; reading order and Next/Previous stay inside one.
public enum ChapterCategories {
    /// `fic_chapters.category`'s default, and what a chapter with none is.
    public static let defaultCategory = "threadmarks"

    public static func category(of chapter: SyncChange) -> String {
        guard let name = chapter.data?["category"]?.string, !name.isEmpty else { return defaultCategory }
        return name
    }

    /// The categories in the order their first chapter appears in `outline`,
    /// which is already in reading order.
    public static func groups(_ outline: [SyncChange]) -> [ChapterGroup] {
        var order: [String] = []
        var members: [String: [SyncChange]] = [:]
        for chapter in outline {
            let name = category(of: chapter)
            if members[name] == nil { order.append(name) }
            members[name, default: []].append(chapter)
        }
        return order.map { ChapterGroup(category: $0, chapters: members[$0]!) }
    }

    /// The chapters either side of `id` within its own category; nil at either
    /// end, and both nil for a chapter not in `outline`.
    public static func neighbors(of id: String, in outline: [SyncChange]) -> (previous: SyncChange?, next: SyncChange?) {
        guard let chapter = outline.first(where: { $0.id == id }) else { return (nil, nil) }
        let name = category(of: chapter)
        let siblings = outline.filter { category(of: $0) == name }
        guard let index = siblings.firstIndex(where: { $0.id == id }) else { return (nil, nil) }
        return (index > 0 ? siblings[index - 1] : nil,
                index + 1 < siblings.count ? siblings[index + 1] : nil)
    }
}
