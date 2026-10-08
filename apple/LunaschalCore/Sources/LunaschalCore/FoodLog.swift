import Foundation

/// The replica collections behind the food log. Synced as their own scope and
/// only when the server lists them, as the calendar is: asking an older server
/// for a collection it doesn't know is a 400, which would fail the journal too.
public enum FoodSync {
    public static let collections = ["food_entries", "food_media"]

    public static func supported(by serverCollections: [String]) -> Bool {
        collections.allSatisfy(serverCollections.contains)
    }

    /// What the phone may change about a meal's words; media is added through
    /// the food routes instead. Matches `backend/mobile_sync/operations.py`.
    public static let editableFields: Set<String> = ["dish", "place", "notes"]
}

/// A replicated `food_entries` row, read for the Journal feed.
public struct FoodEntryRecord: Identifiable, Equatable {
    public let id: String
    public let dish: String?
    public let place: String?
    public let notes: String?
    public let rawContent: String?
    public let rating: Int?
    public let weather: String?
    public let createdAt: Date?

    public init?(record: SyncChange) {
        guard record.collection == "food_entries", !record.deleted, let data = record.data else { return nil }
        func text(_ key: String) -> String? {
            guard let value = data[key]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else { return nil }
            return value
        }
        id = record.id
        dish = text("dish"); place = text("place"); notes = text("notes"); rawContent = text("rawContent")
        rating = data["rating"]?.number.map { Int($0) }
        weather = data["weather"]?.string
        createdAt = JournalTimestamp.parse(data["createdAt"]?.string)
    }

    /// The card's heading: the dish once it is known, else what was said.
    public var heading: String { dish ?? "Meal" }

    /// The words under the heading: the cleaned notes, or the raw note while
    /// the structuring pass has not run.
    public var body: String? { notes ?? rawContent }
}

extension JournalAttachmentItem {
    /// One `food_media` row, shaped like a journal attachment so the feed's
    /// photo strip, audio row and video card draw a meal's media too.
    public init?(foodMedia record: SyncChange) {
        guard record.collection == "food_media", !record.deleted, let data = record.data,
              let entryID = data["entryId"]?.string, let kind = data["kind"]?.string else { return nil }
        func text(_ key: String) -> String? {
            guard let value = data[key]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else { return nil }
            return value
        }
        self.init(id: record.id, entryID: entryID, kind: kind, mime: data["mime"]?.string ?? "",
                  position: Int(data["position"]?.number ?? 0), transcript: text("transcript"),
                  description: text("description"), collection: "food_media")
    }

    /// A meal's media in the order the server shows it.
    public static func groupedFood(_ records: [SyncChange]) -> [String: [JournalAttachmentItem]] {
        Dictionary(grouping: records.compactMap(JournalAttachmentItem.init(foodMedia:)), by: \.entryID)
            .mapValues { $0.sorted { ($0.position, $0.id) < ($1.position, $1.id) } }
    }
}
