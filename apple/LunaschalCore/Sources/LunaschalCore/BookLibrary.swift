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
    /// The Backlog as a filter over the whole library, rather than its folder.
    public private(set) var backlogOnly = false
    public init() {}

    /// The filter a list opens with, and what Reset returns it to. The Backlog
    /// is triage of what was imported and never followed, so it reads
    /// newest-added first; everything else follows the sites' activity.
    public init(opening folder: String?) {
        self.init()
        if folder == FicLibrary.backlogFolder { sort = "added" }
    }

    /// Narrows to the Backlog, taking its newest-added order with it -- but
    /// only from the default sort, so one the user picked is left alone.
    public mutating func setBacklogOnly(_ on: Bool) {
        backlogOnly = on
        if on && sort == "activity" { sort = "added" }
        if !on && sort == "added" { sort = "activity" }
    }
}

public extension JSONValue {
    var strings: [String] {
        guard case .array(let values) = self else { return [] }
        return values.compactMap(\.string)
    }
}
