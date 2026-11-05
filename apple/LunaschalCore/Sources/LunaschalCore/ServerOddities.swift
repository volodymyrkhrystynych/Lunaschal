import Foundation

/// A snapshot from the saved briefing, also available from the offline chat cache.
public struct ServerOddities: Equatable {
    public struct Item: Equatable {
        public var title: String
        public var note: String
    }
    public let prose: String
    public let items: [Item]
    public let notices: String

    public static func parse(content: String, metadata: String?) -> ServerOddities? {
        guard JSONValue.parse(metadata)?["briefing"]?.bool == true else { return nil }
        let lines = content.components(separatedBy: "\n")
        guard let start = lines.firstIndex(of: "**Server oddities (past day)**") else { return nil }
        var items: [Item] = []
        var notices: [String] = []
        for line in lines.dropFirst(start + 1) {
            if line.hasPrefix("- **") {
                items.append(Item(title: String(line.dropFirst(2)), note: ""))
            } else if line.hasPrefix("  "), !items.isEmpty {
                let index = items.count - 1
                items[index].note += (items[index].note.isEmpty ? "" : "\n") + line.trimmingCharacters(in: .whitespaces)
            } else {
                notices.append(line)
            }
        }
        return ServerOddities(prose: lines.prefix(start).joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines),
                              items: items, notices: notices.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines))
    }
}
