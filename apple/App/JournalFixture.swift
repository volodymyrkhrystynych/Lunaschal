#if DEBUG
import Foundation
import LunaschalCore
import UIKit

/// What the Journal feed's UI test reads, since UI tests run with no server:
/// launched with `-journalFeedFixture`, the replica gets an entry carrying a
/// photo, a clip and a watched video, written during a categorised event, plus
/// a meal with a photo, and the photos, poster and clip are placed where a
/// fetch would have put them.
/// Debug builds only.
enum JournalFixture {
    static let argument = "-journalFeedFixture"
    // Fixed, so a rerun replaces the last run's rows rather than wiping the replica.
    private static let epoch = "01K6ZZZZZZZZZZZZZZZZZZZZZZ"
    static let entry = "01K6ZZZZZZZZZZZZZZZZZZZZ01"
    static let quiet = "01K6ZZZZZZZZZZZZZZZZZZZZ02"
    static let photo = "01K6ZZZZZZZZZZZZZZZZZZZZ03"
    static let clip = "01K6ZZZZZZZZZZZZZZZZZZZZ04"
    static let video = "01K6ZZZZZZZZZZZZZZZZZZZZ05"
    static let event = "01K6ZZZZZZZZZZZZZZZZZZZZ06"
    static let meal = "01K6ZZZZZZZZZZZZZZZZZZZZ07"
    static let mealPhoto = "01K6ZZZZZZZZZZZZZZZZZZZZ08"
    /// With `-journalFeedArrival` too: thirty older entries to scroll through,
    /// and a button that brings a new entry in above them all, as a sync would.
    static let arrivalArgument = "-journalFeedArrival"
    static let arrival = "01K6ZZZZZZZZZZZZZZZZZZZZ09"
    static var arrivalEnabled: Bool { ProcessInfo.processInfo.arguments.contains(arrivalArgument) }

    /// The newest entry, as one delta page.
    static func arrivalPage() throws -> SyncPage {
        let page: [String: Any] = [
            "protocolVersion": 1, "epoch": epoch, "mode": "delta", "hasMore": false, "cursor": "arrival",
            "collections": ["journal_entries"],
            "changes": [["revision": 2, "collection": "journal_entries", "id": arrival, "deleted": false,
                         "data": ["id": arrival, "title": "Fixture arrival", "content": "Just synced.",
                                  "createdAt": ISO8601DateFormatter().string(from: Date())]]],
        ]
        return try JSONDecoder().decode(SyncPage.self, from: JSONSerialization.data(withJSONObject: page))
    }

    static func seedIfAsked(root: URL) throws {
        guard ProcessInfo.processInfo.arguments.contains(argument) else { return }
        let now = Date()
        let iso = ISO8601DateFormatter()
        let start = now.addingTimeInterval(-2 * 3600)
        let parts = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: start)
        let date = String(format: "%04d-%02d-%02d", parts.year!, parts.month!, parts.day!)
        let time = String(format: "%02d:%02d", parts.hour!, parts.minute!)
        let endParts = Calendar.current.dateComponents([.hour, .minute], from: start.addingTimeInterval(80 * 60))
        let endTime = String(format: "%02d:%02d", endParts.hour!, endParts.minute!)

        func change(_ collection: String, _ id: String, _ data: [String: Any]) -> [String: Any] {
            var data = data
            data["id"] = id
            return ["revision": 1, "collection": collection, "id": id, "deleted": false, "data": data]
        }
        let changes: [[String: Any]] = [
            change("journal_entries", entry, ["title": "Fixture walk", "content": "Down to the river and back.",
                                              "createdAt": iso.string(from: now.addingTimeInterval(-3600))]),
            change("journal_entries", quiet, ["title": "Fixture later", "content": "Home again.",
                                              "createdAt": iso.string(from: now.addingTimeInterval(-10 * 60))]),
            change("journal_attachments", photo, ["entryId": entry, "kind": "image", "name": "River",
                                                  "mime": "image/jpeg", "position": 0]),
            change("journal_attachments", clip, ["entryId": entry, "kind": "audio", "name": "Birdsong",
                                                 "mime": "audio/wav", "position": 1, "transcript": "Listen to that."]),
            change("journal_attachments", video, ["entryId": entry, "kind": "youtube", "name": "River documentary",
                                                  "position": 2, "importStatus": "done",
                                                  "sourceUrl": "https://www.youtube.com/watch?v=fixture",
                                                  "description": "A film about rivers."]),
            change("food_entries", meal, ["dish": "Fixture ramen", "place": "Kitchen", "notes": "Rich broth.",
                                          "createdAt": iso.string(from: now.addingTimeInterval(-30 * 60))]),
            change("food_media", mealPhoto, ["entryId": meal, "kind": "image", "mime": "image/jpeg", "position": 0]),
            change("calendar_events", event, ["title": "Walk by the river", "date": date, "time": time,
                                              "endTime": endTime, "allDay": 0, "repeatInterval": 1,
                                              "categoryTags": "[\"outside\",\"exercise\"]"]),
        ] + (arrivalEnabled ? (10..<40).map { n in
            change("journal_entries", "01K6ZZZZZZZZZZZZZZZZZZZY\(n)",
                   ["title": "Filler \(n - 9)", "content": "An older entry to scroll past.",
                    "createdAt": iso.string(from: now.addingTimeInterval(-Double(n) * 6 * 3600))])
        } : [])
        let page: [String: Any] = ["protocolVersion": 1, "epoch": epoch, "mode": "bootstrap", "changes": changes,
                                   "hasMore": false, "cursor": "fixture",
                                   "collections": ["journal_entries", "journal_attachments", "calendar_events",
                                                   "food_entries", "food_media"]]
        let decoded = try JSONDecoder().decode(SyncPage.self, from: JSONSerialization.data(withJSONObject: page))
        try ReplicaStore(url: root.appendingPathComponent("replica.sqlite")).apply(decoded, startingBootstrap: true)

        let media = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("journal-media", isDirectory: true)
        try FileManager.default.createDirectory(at: media, withIntermediateDirectories: true)
        try picture(.systemTeal).write(to: media.appendingPathComponent(photo))
        try picture(.systemOrange).write(to: media.appendingPathComponent(mealPhoto))
        try picture(.systemIndigo).write(to: media.appendingPathComponent(video + ".poster"))
        try silence(seconds: 2).write(to: media.appendingPathComponent(clip))
    }

    private static func picture(_ color: UIColor) -> Data {
        UIGraphicsImageRenderer(size: CGSize(width: 320, height: 200)).jpegData(withCompressionQuality: 0.8) {
            color.setFill()
            $0.fill(CGRect(x: 0, y: 0, width: 320, height: 200))
        }
    }

    /// A mono 8 kHz 16-bit WAV of nothing.
    private static func silence(seconds: Int) -> Data {
        let rate: UInt32 = 8000, bytes = UInt32(seconds) * rate * 2
        var data = Data()
        func put<T: FixedWidthInteger>(_ value: T) { withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) } }
        data.append(contentsOf: Array("RIFF".utf8)); put(36 + bytes)
        data.append(contentsOf: Array("WAVEfmt ".utf8)); put(UInt32(16)); put(UInt16(1)); put(UInt16(1))
        put(rate); put(rate * 2); put(UInt16(2)); put(UInt16(16))
        data.append(contentsOf: Array("data".utf8)); put(bytes)
        data.append(Data(count: Int(bytes)))
        return data
    }
}
#endif
