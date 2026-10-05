import Foundation
import XCTest
@testable import LunaschalCore

final class FoodCaptureTests: XCTestCase {
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

    func testSaveFoodTakesTheDraftButNotTheLinks() throws {
        let photo = try store.stageFile(data: Data("jpeg".utf8), name: "Meal.jpg", contentType: "image/jpeg")
        let spoken = try clip("aac")
        let capture = try store.commitDraft(text: " Ramen ", youtubeURLs: ["https://youtu.be/aircAruvnKk"], kind: .food)
        XCTAssertEqual(capture.kind, .food)
        XCTAssertEqual(capture.text, "Ramen")
        XCTAssertEqual(capture.files, [photo])
        XCTAssertEqual(capture.clips.map(\.attachmentID), [spoken.attachmentID])
        XCTAssertEqual(capture.links, [])
        XCTAssertTrue(try store.draft().isEmpty)
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id).kind, .food)
    }

    func testAPhotoOrClipAloneIsAMeal() throws {
        _ = try store.stageFile(data: Data("jpeg".utf8), name: "Meal.jpg", contentType: "image/jpeg")
        XCTAssertEqual(try store.commitDraft(text: "", youtubeURLs: [], kind: .food).text, "")
        XCTAssertThrowsError(try store.commitDraft(text: " ", youtubeURLs: [], kind: .food)) {
            XCTAssertEqual($0 as? CaptureError, .emptyText)
        }
    }

    func testAFileTheFoodLogCannotKeepStaysInTheDraft() throws {
        let pdf = try store.stageFile(data: Data("%PDF".utf8), name: "Menu.pdf", contentType: "application/pdf")
        XCTAssertThrowsError(try store.commitDraft(text: "Dinner", youtubeURLs: [], kind: .food)) {
            XCTAssertEqual($0 as? CaptureError, .notFoodMedia)
        }
        XCTAssertEqual(try store.draft().files, [pdf])
        XCTAssertEqual(try store.list(), [])
        // The same draft is still a perfectly good journal entry.
        XCTAssertEqual(try store.commitDraft(text: "Dinner", youtubeURLs: []).files, [pdf])
    }

    func testOlderManifestsAreJournalEntries() throws {
        let saved = try store.commitDraft(text: "Before food existed", youtubeURLs: [])
        var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(saved)) as! [String: Any]
        json.removeValue(forKey: "kind")
        let decoded = try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertEqual(decoded.kind, .journal)
    }

    func testFoodMultipartCarriesTheMealPhotosAndTheirIds() throws {
        let first = try store.stageFile(data: Data("JPEGDATA".utf8), name: "Bo\"wl\r\n.jpg", contentType: "image/jpeg")
        let second = try store.stageFile(data: Data("HEICDATA".utf8), name: "Side.heic", contentType: "image/heic")
        _ = try clip("aac")
        let capture = try store.commitDraft(text: "Ramen", youtubeURLs: [], kind: .food,
                                            now: Date(timeIntervalSince1970: 1_790_000_000))
        let body = try FoodMultipart(capture: capture, files: capture.files.map(store.fileURL))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = try XCTUnwrap(String(data: Data(contentsOf: body.url), encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"id\"\r\n\r\n\(capture.id)\r\n"))
        XCTAssertTrue(text.contains("name=\"text\"\r\n\r\nRamen\r\n"))
        XCTAssertTrue(text.contains("name=\"capturedAt\"\r\n\r\n\(ISO8601DateFormatter().string(from: capture.createdAt))\r\n"))
        XCTAssertTrue(text.contains("name=\"pendingClips\"\r\n\r\n1\r\n"))
        XCTAssertTrue(text.contains("name=\"mediaIds\"\r\n\r\n[\"\(first.attachmentID)\",\"\(second.attachmentID)\"]\r\n"))
        XCTAssertTrue(text.contains("name=\"media\"; filename=\"Bowl.jpg\"\r\nContent-Type: image/jpeg\r\n\r\nJPEGDATA\r\n"))
        XCTAssertTrue(text.contains("name=\"media\"; filename=\"Side.heic\"\r\nContent-Type: image/heic\r\n\r\nHEICDATA\r\n--\(body.boundary)--\r\n"))
    }

    func testFoodRecordingMultipartNamesTheMealAndClip() throws {
        let spoken = try clip("AACDATA")
        let capture = try store.commitDraft(text: "", youtubeURLs: [], kind: .food)
        let body = try FoodRecordingMultipart(capture: capture, clip: spoken, position: 2,
                                              audioURL: store.clipURL(spoken))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = try XCTUnwrap(String(data: Data(contentsOf: body.url), encoding: .utf8))
        XCTAssertTrue(text.contains("name=\"id\"\r\n\r\n\(capture.id)\r\n"))
        XCTAssertTrue(text.contains("name=\"mediaId\"\r\n\r\n\(spoken.attachmentID)\r\n"))
        XCTAssertTrue(text.contains("name=\"position\"\r\n\r\n2\r\n"))
        XCTAssertTrue(text.contains("name=\"audio\"; filename=\"recording.m4a\"\r\nContent-Type: audio/mp4\r\n\r\nAACDATA\r\n"))
    }

    func testAMealCountsAsSavedOnlyWhenEveryPhotoCameBack() throws {
        let photo = try store.stageFile(data: Data("jpeg".utf8), name: "Meal.jpg", contentType: "image/jpeg")
        let capture = try store.commitDraft(text: "Ramen", youtubeURLs: [], kind: .food)
        func ack(_ id: String, _ media: [String]) -> Data {
            Data("{\"id\":\"\(id)\",\"media\":[\(media.map { "{\"id\":\"\($0)\",\"kind\":\"image\"}" }.joined(separator: ","))]}".utf8)
        }
        XCTAssertNoThrow(try JournalAPI.validateFoodAcknowledgement(ack(capture.id, [photo.attachmentID]), for: capture))
        XCTAssertThrowsError(try JournalAPI.validateFoodAcknowledgement(ack(capture.id, []), for: capture))
        XCTAssertThrowsError(try JournalAPI.validateFoodAcknowledgement(ack(ULID.make(), [photo.attachmentID]), for: capture))
    }

    func testClipAcknowledgementMustNameTheMealAndClip() throws {
        let spoken = try clip("aac")
        let capture = try store.commitDraft(text: "", youtubeURLs: [], kind: .food)
        let good = Data("{\"id\":\"\(capture.id)\",\"media\":{\"id\":\"\(spoken.attachmentID)\",\"kind\":\"audio\"}}".utf8)
        XCTAssertNoThrow(try JournalAPI.validateFoodClipAcknowledgement(good, for: capture, clip: spoken))
        let wrong = Data("{\"id\":\"\(capture.id)\",\"media\":{\"id\":\"\(ULID.make())\"}}".utf8)
        XCTAssertThrowsError(try JournalAPI.validateFoodClipAcknowledgement(wrong, for: capture, clip: spoken))
    }

    @MainActor
    func testASyncedMealIsNotMistakenForADeletedJournalEntry() async throws {
        let meal = try store.commitDraft(text: "Ramen", youtubeURLs: [], kind: .food)
        let entry = try store.commitDraft(text: "A thought", youtubeURLs: [])
        let server = JournalOnlyServer()
        try await CaptureSync(store: store).run(using: server)
        XCTAssertEqual(Set(server.sent), [meal.id, entry.id])
        XCTAssertEqual(server.fetched, [entry.id])
        XCTAssertEqual(try store.load(meal.id).state, .synced)
        XCTAssertNil(try store.load(meal.id).lastError)
    }
}

/// Answers the journal read-back with 404 for anything it does not hold, as
/// `/api/journal/<id>` does for a meal.
private final class JournalOnlyServer: JournalTransport {
    var sent: [String] = []
    var fetched: [String] = []
    private var journal = Set<String>()
    func send(_ capture: Capture, audioURL: URL?) async throws {
        sent.append(capture.id)
        if capture.kind == .journal { journal.insert(capture.id) }
    }
    func fetch(_ id: String) async throws -> JournalSnapshot {
        fetched.append(id)
        guard journal.contains(id) else { throw HTTPFailure(status: 404) }
        return try JSONDecoder().decode(JournalSnapshot.self, from: Data("{\"id\":\"\(id)\",\"content\":\"\"}".utf8))
    }
}
