import Foundation
import XCTest
@testable import LunaschalCore

/// A purchase captured like a meal: the same composer and upload, its own
/// route, and a narrower rule about what may go with it.
final class SpendingCaptureTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func clip(_ bytes: String) throws -> CaptureClip {
        let clip = try store.beginClip(transcribe: true)
        try Data(bytes.utf8).write(to: store.clipURL(clip))
        try store.finishClip(clip.attachmentID)
        return clip
    }

    private func change(_ collection: String, _ id: String, _ data: [String: JSONValue]) -> SyncChange {
        var payload = data
        payload["id"] = .string(id)
        return SyncChange(revision: 1, collection: collection, id: id, deleted: false, data: payload)
    }

    func testKindsKnowTheirRoutesAndWhatTheyKeep() {
        let photo = CaptureFile(name: "r.jpg", contentType: "image/jpeg")
        let voice = CaptureFile(name: "r.m4a", contentType: "audio/mp4")
        let video = CaptureFile(name: "till.mov", contentType: "video/quicktime")
        let pdf = CaptureFile(name: "bill.pdf", contentType: "application/pdf")
        XCTAssertEqual(CaptureKind.spending.route, "api/spending")
        XCTAssertEqual(CaptureKind.food.route, "api/food")
        XCTAssertTrue(CaptureKind.spending.isMediaLog && CaptureKind.food.isMediaLog)
        XCTAssertFalse(CaptureKind.journal.isMediaLog)
        XCTAssertEqual([photo, voice, video, pdf].map(CaptureKind.spending.accepts), [true, true, false, false])
        XCTAssertEqual([photo, voice, video, pdf].map(CaptureKind.food.accepts), [true, true, true, false])
        XCTAssertTrue([photo, voice, video, pdf].allSatisfy(CaptureKind.journal.accepts))
    }

    func testAReceiptPhotoAloneIsAPurchaseThatTakesNoLinks() throws {
        let receipt = try store.stageFile(data: Data("jpeg".utf8), name: "Receipt.jpg", contentType: "image/jpeg",
                                          capturedAt: Date(timeIntervalSince1970: 1_700_000_000))
        let spoken = try clip("aac")
        let capture = try store.commitDraft(text: "", youtubeURLs: ["https://youtu.be/aircAruvnKk"], kind: .spending)
        XCTAssertEqual(capture.kind, .spending)
        XCTAssertEqual(capture.files, [receipt])
        XCTAssertEqual(capture.clips.map(\.attachmentID), [spoken.attachmentID])
        XCTAssertEqual(capture.links, [])
        // The receipt says when the money was spent, as a meal's photo does.
        XCTAssertEqual(capture.createdAt, Date(timeIntervalSince1970: 1_700_000_000))
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id).kind, .spending)
        XCTAssertTrue(try store.draft().isEmpty)
    }

    func testAVideoStaysInTheDraftForSomewhereThatKeepsIt() throws {
        let video = try store.stageFile(data: Data("mov".utf8), name: "Till.mov", contentType: "video/quicktime")
        XCTAssertThrowsError(try store.commitDraft(text: "Lunch", youtubeURLs: [], kind: .spending)) {
            XCTAssertEqual($0 as? CaptureError, .notSpendingMedia)
        }
        XCTAssertEqual(try store.draft().files, [video])
        XCTAssertEqual(try store.list(), [])
        XCTAssertEqual(try store.commitDraft(text: "Lunch", youtubeURLs: [], kind: .food).files, [video])
    }

    func testTheMultipartIsTheMealsShapeUnderTheReceiptsIds() throws {
        let receipt = try store.stageFile(data: Data("JPEGDATA".utf8), name: "Receipt.jpg", contentType: "image/jpeg")
        _ = try clip("aac")
        let capture = try store.commitDraft(text: "Groceries", youtubeURLs: [], kind: .spending)
        let body = try FoodMultipart(capture: capture, files: capture.files.map(store.fileURL))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = try XCTUnwrap(String(data: Data(contentsOf: body.url), encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"id\"\r\n\r\n\(capture.id)\r\n"))
        XCTAssertTrue(text.contains("name=\"pendingClips\"\r\n\r\n1\r\n"))
        XCTAssertTrue(text.contains("name=\"mediaIds\"\r\n\r\n[\"\(receipt.attachmentID)\"]\r\n"))
        XCTAssertTrue(text.contains("filename=\"Receipt.jpg\"\r\nContent-Type: image/jpeg\r\n\r\nJPEGDATA\r\n"))
    }

    func testAPurchasesMediaHasItsOwnFileRoute() throws {
        let api = try JournalAPI(server: URL(string: "https://lunaschal.example")!, token: nil, allowCellular: false)
        let id = ULID.make()
        let item = JournalAttachmentItem(id: id, entryID: ULID.make(), kind: "image", collection: "spending_media")
        XCTAssertEqual(try api.attachmentURL(item).absoluteString, "https://lunaschal.example/api/spending/media/\(id)")
    }

    func testAPurchaseReadsAsAHeadingAndAnAmountThatMayBeMissing() throws {
        let read = try XCTUnwrap(SpendingEntryRecord(record: change("spending_logs", ULID.make(), [
            "date": .string("2026-10-11"), "merchant": .string(" Loblaws "), "category": .string("Groceries"),
            "amountCents": .number(4286), "rawContent": .string("the strawberries were on sale"),
            "createdAt": .string("2026-10-11T12:00:00+00:00"),
        ])))
        XCTAssertEqual(read.heading, "Loblaws · Groceries")
        XCTAssertEqual(read.amountCents, 4286)
        XCTAssertEqual(read.body, "the strawberries were on sale")
        let unread = try XCTUnwrap(SpendingEntryRecord(record: change("spending_logs", ULID.make(), [
            "date": .string("2026-10-11"), "amountCents": .null,
        ])))
        XCTAssertEqual(unread.heading, "Purchase")
        XCTAssertNil(unread.amountCents)
        XCTAssertNil(SpendingEntryRecord(record: change("food_entries", ULID.make(), ["date": .string("x")])))
    }

    func testReceiptsAndClipsGroupInOrder() {
        let entry = ULID.make()
        let voice = change("spending_media", ULID.make(), ["entryId": .string(entry), "kind": .string("audio"),
                                                          "position": .number(1)])
        let photo = change("spending_media", ULID.make(), ["entryId": .string(entry), "kind": .string("image"),
                                                          "mime": .string("image/jpeg"), "position": .number(0),
                                                          "description": .string("TOTAL 42.86")])
        let grouped = JournalAttachmentItem.groupedSpending([voice, photo])
        XCTAssertEqual(grouped[entry]?.map(\.id), [photo.id, voice.id])
        XCTAssertEqual(grouped[entry]?.first?.collection, "spending_media")
        XCTAssertEqual(grouped[entry]?.first?.description, "TOTAL 42.86")
    }

    func testOnlyAPurchasesWordsAndAmountCanBeQueuedAsAnEdit() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".sqlite")
        defer { try? FileManager.default.removeItem(at: url) }
        let replica = try ReplicaStore(url: url)
        let purchase = change("spending_logs", ULID.make(), ["date": .string("2026-10-11")])
        try replica.apply(SyncPage(protocolVersion: 1, epoch: ULID.make(), mode: "bootstrap", changes: [purchase],
                                   hasMore: false, cursor: "c", collections: SpendingSync.collections),
                          startingBootstrap: true)
        XCTAssertThrowsError(try replica.queue(record: purchase, data: ["date": .string("2026-01-01")]))
        XCTAssertThrowsError(try replica.queue(record: purchase, data: [:], delete: true))
        let operation = try replica.queue(record: purchase, data: ["amountCents": .number(4100), "merchant": .string("Loblaws")])
        XCTAssertEqual(operation.collection, "spending_logs")
        XCTAssertEqual(purchase.title, "Purchase")
    }

    func testDailyCountsUnreadReceiptsBesideTheTotal() {
        let server = DailyStatus(day: "2026-10-11", spending: [.init(id: ULID.make(), category: "Coffee", amountCents: 450)],
                                 pendingSpending: 2)
        let summary = DailySummary(day: "2026-10-11", server: server, local: [])
        XCTAssertEqual(summary.totalCents, 450)
        XCTAssertEqual(summary.pendingSpending, 2)
        // An older server sends no count, and a stale day's count is not today's.
        XCTAssertEqual(DailySummary(day: "2026-10-11", server: DailyStatus(day: "2026-10-11"), local: []).pendingSpending, 0)
        XCTAssertEqual(DailySummary(day: "2026-10-12", server: server, local: []).pendingSpending, 0)
    }
}
