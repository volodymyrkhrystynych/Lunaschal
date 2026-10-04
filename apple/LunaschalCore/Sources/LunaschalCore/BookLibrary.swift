import Foundation

public struct BookFilter: Equatable {
    public var query = ""
    public var source = ""
    public var folder = ""
    public var tag = ""
    public var sort = "activity"
    public var bookmark = ""
    public init() {}
}

public extension JSONValue {
    var strings: [String] {
        guard case .array(let values) = self else { return [] }
        return values.compactMap(\.string)
    }
}
