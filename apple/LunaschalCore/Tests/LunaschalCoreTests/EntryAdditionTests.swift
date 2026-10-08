import Foundation
import XCTest
@testable import LunaschalCore

/// Editing a server entry stages clips, photos and files into a draft of its
/// own, and Save turns that into an addition uploaded under the entry.
final class EntryAdditionTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!
    private let entry = ULID.make()

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func clip(into entryID: String?, _ bytes: String = "aac") throws -> CaptureClip {
        let clip = try store.beginClip(transcribe: true, into: entryID)
        try Data(bytes.utf8).write(to: store.clipURL(clip))
        try store.finishClip(clip.attachmentID)
        return clip
    }

    func testAnEntrysDraftIsSeparateFromTheComposersAndSurvivesRestart() throws {
        let composed = try store.stageFile(data: Data("a".utf8), name: "Mine.jpg", contentType: "image/jpeg")
        let photo = try store.stageFile(data: Data("b".utf8), name: "Edit.jpg", contentType: "image/jpeg", into: entry)
        let spoken = try clip(into: entry)
        let reopened = try CaptureStore(root: root)
        XCTAssertEqual(try reopened.draft().files, [composed])
        XCTAssertTrue(try reopened.draft().clips.isEmpty)
        XCTAssertEqual(try reopened.draft(for: entry).files, [photo])
        XCTAssertEqual(try reopened.draft(for: entry).clips.map(\.attachmentID), [spoken.attachmentID])
        XCTAssertEqual(try reopened.draft(for: entry).clips.first?.state, .ready)
        XCTAssertEqual(Array(try reopened.entryDrafts().keys), [entry])
        // A draft file is never read back as a capture manifest.
        XCTAssertEqual(try reopened.list(), [])
    }

    func testSaveMakesOneAdditionUnderTheEntryAndClearsOnlyItsDraft() throws {
        let composed = try store.stageFile(data: Data("a".utf8), name: "Mine.jpg", contentType: "image/jpeg")
        let photo = try store.stageFile(data: Data("b".utf8), name: "Edit.jpg", contentType: "image/jpeg", into: entry)
        let spoken = try clip(into: entry)
        let addition = try XCTUnwrap(store.commitAdditions(to: entry, kind: .journal,
                                                           youtubeURLs: ["https://youtu.be/aircAruvnKk"]))
        XCTAssertEqual(addition.entryID, entry)
        XCTAssertEqual(addition.targetID, entry)
        XCTAssertNotEqual(addition.id, entry)
        XCTAssertEqual(addition.text, "")
        XCTAssertEqual(addition.files, [photo])
        XCTAssertEqual(addition.clips.map(\.attachmentID), [spoken.attachmentID])
        XCTAssertEqual(addition.links.map(\.url), ["https://www.youtube.com/watch?v=aircAruvnKk"])
        XCTAssertTrue(try store.draft(for: entry).isEmpty)
        XCTAssertEqual(try store.entryDrafts(), [:])
        XCTAssertEqual(try store.draft().files, [composed])
        XCTAssertEqual(try store.pendingAdditions(to: entry).map(\.id), [addition.id])
        XCTAssertEqual(try CaptureStore(root: root).load(addition.id).entryID, entry)
    }

    func testNothingStagedIsNoAddition() throws {
        XCTAssertNil(try store.commitAdditions(to: entry, kind: .journal))
        XCTAssertNil(try store.commitAdditions(to: entry, kind: .food, youtubeURLs: ["https://youtu.be/aircAruvnKk"]))
        XCTAssertEqual(try store.list(), [])
    }

    func testAMealTakesMediaOnlyAndNoLinks() throws {
        _ = try store.stageFile(data: Data("%PDF".utf8), name: "Menu.pdf", contentType: "application/pdf", into: entry)
        XCTAssertThrowsError(try store.commitAdditions(to: entry, kind: .food)) {
            XCTAssertEqual($0 as? CaptureError, .notFoodMedia)
        }
        XCTAssertEqual(try store.draft(for: entry).files.count, 1)
        let pdf = try XCTUnwrap(store.draft(for: entry).files.first)
        try store.discardStaged(pdf)
        XCTAssertEqual(try store.entryDrafts(), [:])
        _ = try clip(into: entry)
        let addition = try XCTUnwrap(store.commitAdditions(to: entry, kind: .food,
                                                           youtubeURLs: ["https://youtu.be/aircAruvnKk"]))
        XCTAssertEqual(addition.kind, .food)
        XCTAssertEqual(addition.links, [])
    }

    func testAnAdditionCannotCarryTextOrPointAtABadEntry() throws {
        let photo = try store.stageFile(data: Data("b".utf8), name: "Edit.jpg", contentType: "image/jpeg", into: entry)
        XCTAssertThrowsError(try store.save(Capture(text: "words", files: [photo], entryID: entry)))
        XCTAssertThrowsError(try store.save(Capture(files: [photo], entryID: "not-an-id")))
        XCTAssertThrowsError(try store.save(Capture(entryID: entry))) {
            XCTAssertEqual($0 as? CaptureError, .nothingToAdd)
        }
        XCTAssertThrowsError(try store.stageFile(data: Data("c".utf8), name: "x", contentType: nil, into: "../escape"))
    }

    func testDiscardAndInterruptionFindTheClipInItsEntrysDraft() throws {
        let kept = try clip(into: entry)
        let running = try store.beginClip(transcribe: false, into: entry)
        try store.recoverInterruptedRecordings()
        let draft = try store.draft(for: entry)
        XCTAssertEqual(draft.clips.first { $0.attachmentID == running.attachmentID }?.state, .interrupted)
        XCTAssertEqual(draft.clips.first { $0.attachmentID == kept.attachmentID }?.state, .ready)
        try store.discard(kept)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.clipURL(kept).path))
        XCTAssertEqual(try store.draft(for: entry).clips.map(\.attachmentID), [running.attachmentID])
    }

    func testCancelThrowsAwayOnlyThatEntrysStagedBytes() throws {
        let composed = try store.stageFile(data: Data("a".utf8), name: "Mine.jpg", contentType: "image/jpeg")
        let photo = try store.stageFile(data: Data("b".utf8), name: "Edit.jpg", contentType: "image/jpeg", into: entry)
        let spoken = try clip(into: entry)
        try store.discardDraft(for: entry)
        XCTAssertEqual(try store.entryDrafts(), [:])
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.fileURL(photo).path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.clipURL(spoken).path))
        XCTAssertEqual(try store.draft().files, [composed])
        XCTAssertTrue(FileManager.default.fileExists(atPath: try store.fileURL(composed).path))
    }

    func testAMealsMediaHasItsOwnFileRoute() throws {
        let api = try JournalAPI(server: URL(string: "https://lunaschal.example")!, token: nil, allowCellular: false)
        let id = ULID.make()
        let meal = JournalAttachmentItem(id: id, entryID: entry, kind: "image", collection: "food_media")
        XCTAssertEqual(try api.attachmentURL(meal).absoluteString, "https://lunaschal.example/api/food/media/\(id)")
        XCTAssertThrowsError(try api.attachmentURL(meal, thumbnail: true))
        let journal = JournalAttachmentItem(id: id, entryID: entry, kind: "image")
        XCTAssertEqual(try api.attachmentURL(journal).absoluteString,
                       "https://lunaschal.example/api/journal/attachments/\(id)/file")
    }

    func testAnEmptyClipIsDroppedFromItsEntrysDraft() throws {
        let silent = try store.beginClip(transcribe: true, into: entry)
        XCTAssertThrowsError(try store.finishClip(silent.attachmentID))
        XCTAssertEqual(try store.entryDrafts(), [:])
    }

    // MARK: Requests

    func testAJournalAdditionUploadsUnderTheEntry() throws {
        let photo = try store.stageFile(data: Data("b".utf8), name: "Edit.jpg", contentType: "image/jpeg", into: entry)
        let spoken = try clip(into: entry)
        let addition = try XCTUnwrap(store.commitAdditions(to: entry, kind: .journal, youtubeURLs: ["https://youtu.be/aircAruvnKk"]))
        let file = Data("{\"id\":\"\(photo.attachmentID)\",\"entryId\":\"\(entry)\"}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateFileAcknowledgement(file, for: addition, file: photo))
        let elsewhere = Data("{\"id\":\"\(photo.attachmentID)\",\"entryId\":\"\(addition.id)\"}".utf8)
        XCTAssertThrowsError(try JournalAPI.validateFileAcknowledgement(elsewhere, for: addition, file: photo))
        let clipAck = Data("{\"id\":\"\(entry)\",\"attachment\":{\"id\":\"\(spoken.attachmentID)\"}}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateClipAcknowledgement(clipAck, for: addition, clip: spoken))
        let link = try XCTUnwrap(addition.links.first)
        let linkAck = Data("{\"id\":\"\(link.attachmentID)\",\"entryId\":\"\(entry)\"}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateLinkAcknowledgement(linkAck, for: addition, link: link))
    }

    func testAMealAdditionSendsOnlyItsMediaIds() throws {
        let photo = try store.stageFile(data: Data("JPEG".utf8), name: "Side.jpg", contentType: "image/jpeg", into: entry)
        let spoken = try clip(into: entry, "AAC")
        let addition = try XCTUnwrap(store.commitAdditions(to: entry, kind: .food))
        let body = try FoodMultipart(capture: addition, files: addition.files.map(store.fileURL))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = String(decoding: try Data(contentsOf: body.url), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"mediaIds\"\r\n\r\n[\"\(photo.attachmentID)\"]\r\n"))
        XCTAssertFalse(text.contains("name=\"id\""))
        XCTAssertFalse(text.contains("name=\"text\""))
        XCTAssertTrue(text.contains("filename=\"Side.jpg\"\r\nContent-Type: image/jpeg\r\n\r\nJPEG\r\n"))
        let ack = Data("{\"id\":\"\(entry)\",\"media\":[{\"id\":\"\(photo.attachmentID)\"}]}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateFoodAcknowledgement(ack, for: addition))

        let recording = try FoodRecordingMultipart(capture: addition, clip: spoken, position: nil,
                                                   audioURL: store.clipURL(spoken))
        defer { try? FileManager.default.removeItem(at: recording.url) }
        let clipText = String(decoding: try Data(contentsOf: recording.url), as: UTF8.self)
        XCTAssertTrue(clipText.contains("name=\"id\"\r\n\r\n\(entry)\r\n"))
        XCTAssertFalse(clipText.contains("name=\"position\""))
        let clipAck = Data("{\"id\":\"\(entry)\",\"media\":{\"id\":\"\(spoken.attachmentID)\"}}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateFoodClipAcknowledgement(clipAck, for: addition, clip: spoken))
    }

    @MainActor
    func testSyncSendsAnAdditionButNeverReadsItBackAsAnEntry() async throws {
        _ = try store.stageFile(data: Data("b".utf8), name: "Edit.jpg", contentType: "image/jpeg", into: entry)
        let addition = try XCTUnwrap(store.commitAdditions(to: entry, kind: .journal))
        let server = AdditionServer()
        try await CaptureSync(store: store).run(using: server)
        XCTAssertEqual(server.sent, [addition.id])
        XCTAssertEqual(server.fetched, [])
        XCTAssertEqual(try store.load(addition.id).state, .synced)
        XCTAssertEqual(try store.pendingAdditions(to: entry), [])
    }
}

final class FoodLogTests: XCTestCase {
    private func change(_ collection: String, _ id: String, _ data: [String: JSONValue], revision: Int64 = 1) -> SyncChange {
        var payload = data
        payload["id"] = .string(id)
        return SyncChange(revision: revision, collection: collection, id: id, deleted: false, data: payload)
    }

    func testAMealReadsItsDishNotesAndTime() throws {
        let meal = try XCTUnwrap(FoodEntryRecord(record: change("food_entries", ULID.make(), [
            "dish": .string(" Ramen "), "notes": .string(""), "rawContent": .string("had ramen"),
            "rating": .number(4), "createdAt": .string("2026-10-07T12:00:00+00:00"),
        ])))
        XCTAssertEqual(meal.heading, "Ramen")
        XCTAssertNil(meal.notes)
        XCTAssertEqual(meal.body, "had ramen")
        XCTAssertEqual(meal.rating, 4)
        XCTAssertNotNil(meal.createdAt)
        XCTAssertEqual(FoodEntryRecord(record: change("food_entries", ULID.make(), [:]))?.heading, "Meal")
        XCTAssertNil(FoodEntryRecord(record: change("journal_entries", ULID.make(), [:])))
    }

    func testAMealsMediaGroupsInOrderAsAttachments() {
        let meal = ULID.make()
        let later = change("food_media", ULID.make(), ["entryId": .string(meal), "kind": .string("audio"),
                                                       "position": .number(1), "transcript": .string("crispy")])
        let first = change("food_media", ULID.make(), ["entryId": .string(meal), "kind": .string("image"),
                                                       "mime": .string("image/jpeg"), "position": .number(0)])
        let grouped = JournalAttachmentItem.groupedFood([later, first])
        XCTAssertEqual(grouped[meal]?.map(\.id), [first.id, later.id])
        XCTAssertEqual(grouped[meal]?.map(\.media), [.image, .audio])
        XCTAssertEqual(grouped[meal]?.first?.collection, "food_media")
        XCTAssertEqual(grouped[meal]?.last?.transcript, "crispy")
    }

    func testOnlyAMealsWordsCanBeQueuedAsAnEdit() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".sqlite")
        defer { try? FileManager.default.removeItem(at: url) }
        let store = try ReplicaStore(url: url)
        let meal = change("food_entries", ULID.make(), ["dish": .string("Ramen")])
        try store.apply(SyncPage(protocolVersion: 1, epoch: ULID.make(), mode: "bootstrap", changes: [meal],
                                 hasMore: false, cursor: "c", collections: FoodSync.collections),
                        startingBootstrap: true)
        XCTAssertThrowsError(try store.queue(record: meal, data: ["rawContent": .string("x")]))
        XCTAssertThrowsError(try store.queue(record: meal, data: [:], delete: true))
        let operation = try store.queue(record: meal, data: ["dish": .string("Shoyu ramen"), "notes": .string("rich")])
        XCTAssertEqual(operation.collection, "food_entries")
        XCTAssertEqual(try store.edits().map(\.operation.recordId), [meal.id])
    }
}

private final class AdditionServer: JournalTransport {
    var sent: [String] = []
    var fetched: [String] = []
    func send(_ capture: Capture, audioURL: URL?) async throws { sent.append(capture.id) }
    func fetch(_ id: String) async throws -> JournalSnapshot {
        fetched.append(id)
        throw HTTPFailure(status: 404)
    }
}
