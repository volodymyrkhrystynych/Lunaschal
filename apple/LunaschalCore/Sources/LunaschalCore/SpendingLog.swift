import Foundation

/// The replica collections behind spending: every purchase, typed on the Daily
/// page or captured as a receipt, and the receipts and clips themselves.
/// Synced only when the server lists them, as the food log is.
public enum SpendingSync {
    public static let collections = ["spending_logs", "spending_media"]

    public static func supported(by serverCollections: [String]) -> Bool {
        collections.allSatisfy(serverCollections.contains)
    }

    /// What the phone may change about a purchase. Matches
    /// `backend/mobile_sync/operations.py`; media is added through the routes.
    public static let editableFields: Set<String> = ["merchant", "category", "notes", "amountCents"]
}

/// A replicated `spending_logs` row, read for the Journal feed.
public struct SpendingEntryRecord: Identifiable, Equatable {
    public let id: String
    public let day: String
    public let merchant: String?
    public let category: String?
    /// CAD cents; nil until a receipt or a clip has been read.
    public let amountCents: Int?
    public let notes: String?
    public let rawContent: String?
    public let createdAt: Date?

    public init?(record: SyncChange) {
        guard record.collection == "spending_logs", !record.deleted, let data = record.data,
              let day = data["date"]?.string else { return nil }
        func text(_ key: String) -> String? {
            guard let value = data[key]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else { return nil }
            return value
        }
        id = record.id
        self.day = day
        merchant = text("merchant"); category = text("category")
        notes = text("notes"); rawContent = text("rawContent")
        amountCents = data["amountCents"]?.number.map { Int($0) }
        createdAt = JournalTimestamp.parse(data["createdAt"]?.string)
    }

    /// The card's heading: where, then what for; an unread receipt is a "Purchase".
    public var heading: String {
        let parts = [merchant, category].compactMap { $0 }
        return parts.isEmpty ? "Purchase" : parts.joined(separator: " · ")
    }

    /// The words under the heading: the cleaned notes, or what was said while
    /// the structuring pass has not run.
    public var body: String? { notes ?? rawContent }
}

extension JournalAttachmentItem {
    /// One `spending_media` row, shaped like a journal attachment so the feed's
    /// photo strip and audio row draw a receipt and its clip too.
    public init?(spendingMedia record: SyncChange) {
        guard record.collection == "spending_media", !record.deleted, let data = record.data,
              let entryID = data["entryId"]?.string, let kind = data["kind"]?.string else { return nil }
        func text(_ key: String) -> String? {
            guard let value = data[key]?.string?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else { return nil }
            return value
        }
        self.init(id: record.id, entryID: entryID, kind: kind, mime: data["mime"]?.string ?? "",
                  position: Int(data["position"]?.number ?? 0), transcript: text("transcript"),
                  description: text("description"), collection: "spending_media")
    }

    /// A purchase's media in the order the server shows it.
    public static func groupedSpending(_ records: [SyncChange]) -> [String: [JournalAttachmentItem]] {
        Dictionary(grouping: records.compactMap(JournalAttachmentItem.init(spendingMedia:)), by: \.entryID)
            .mapValues { $0.sorted { ($0.position, $0.id) < ($1.position, $1.id) } }
    }
}
