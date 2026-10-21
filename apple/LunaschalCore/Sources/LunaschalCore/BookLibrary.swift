import Foundation

public struct BookFilter: Equatable {
    public var query = ""
    public var source = ""
    public var folder = ""
    public var tag = ""
    public var sort = "activity"
    public var bookmark = ""
    /// `FicLibrary.activeShelves` (the default: not finished, not dropped),
    /// `archivedShelves`, `allShelves`, or one `Shelf` raw value.
    public var shelf = FicLibrary.activeShelves
    /// 0 for any rating, unrated included.
    public var minRating = 0
    public var unreadOnly = false
    public init() {}
}

public extension JSONValue {
    var strings: [String] {
        guard case .array(let values) = self else { return [] }
        return values.compactMap(\.string)
    }
}
