import Foundation
import XCTest
@testable import LunaschalCore

/// The composer's notes travel with its draft, and a newspaper filed again
/// replaces the entry it was filed as before.
final class NotebookDraftTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    // MARK: Notes in the draft

    func testTheDraftKeepsItsNotebookAcrossRestart() throws {
        let notebook = ULID.make()
        try store.setDraftNotebook(notebook)
        XCTAssertEqual(try CaptureStore(root: root).draft().notebookID, notebook)
        // Notes alone are not staged files or clips; the notebook says whether it holds anything.
        XCTAssertTrue(try store.draft().isEmpty)
        try store.setDraftNotebook(nil)
        XCTAssertNil(try store.draft().notebookID)
    }

    func testADraftSavedBeforeNotesExistedStillLoads() throws {
        try Data(#"{"files":[],"clips":[]}"#.utf8).write(to: root.appendingPathComponent("draft.json"))
        XCTAssertNil(try store.draft().notebookID)
    }

    func testOnlyANotebookIDCanBeKept() {
        XCTAssertThrowsError(try store.setDraftNotebook("../elsewhere")) {
            XCTAssertEqual($0 as? CaptureError, .invalidID)
        }
    }

    func testSaveEntryFilesWordsStagedFilesAndNotesPagesAsOneEntry() throws {
        let photo = try store.stageFile(data: Data("photo".utf8), name: "photo.jpg", contentType: "image/jpeg")
        try store.setDraftNotebook(ULID.make())
        let capture = try store.commitDraft(text: "Evening", youtubeURLs: [],
                                            images: [(Data("p1".utf8), "Notes p1.jpg"), (Data("p2".utf8), "Notes p2.jpg")])
        XCTAssertEqual(capture.text, "Evening")
        XCTAssertEqual(capture.files.map(\.name), ["photo.jpg", "Notes p1.jpg", "Notes p2.jpg"])
        XCTAssertEqual(capture.files.first, photo)
        XCTAssertEqual(try Data(contentsOf: store.fileURL(capture.files[2])), Data("p2".utf8))
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id), capture)
        // The notes went with the entry; the next one starts without them.
        XCTAssertNil(try store.draft().notebookID)
        XCTAssertTrue(try store.draft().isEmpty)
    }

    func testNotesAloneAreAnEntry() throws {
        try store.setDraftNotebook(ULID.make())
        let capture = try store.commitDraft(text: "", youtubeURLs: [], images: [(Data("p1".utf8), "Notes p1.jpg")])
        XCTAssertEqual(capture.files.map(\.name), ["Notes p1.jpg"])
    }

    func testAMealLeavesTheNotesForTheNextEntry() throws {
        let notebook = ULID.make()
        try store.setDraftNotebook(notebook)
        let meal = try store.commitDraft(text: "Soup", youtubeURLs: [], kind: .food,
                                         images: [(Data("p1".utf8), "Notes p1.jpg")])
        XCTAssertEqual(meal.files, [])
        XCTAssertEqual(try store.draft().notebookID, notebook)
    }

    func testARefusedSaveLeavesNoPagesBehind() throws {
        let files = root.appendingPathComponent("files")
        XCTAssertThrowsError(try store.commitDraft(text: "x", youtubeURLs: ["https://example.com/not-youtube"],
                                                   images: [(Data("p1".utf8), "Notes p1.jpg")]))
        XCTAssertEqual((try? FileManager.default.contentsOfDirectory(atPath: files.path)) ?? [], [])
    }

    // MARK: Replacing a filed newspaper

    func testAReplacementCarriesWhatItReplaces() throws {
        let morning = try store.commitImages(text: "Toronto Star, 2026-10-08", youtubeURLs: [],
                                             images: [(Data("p1".utf8), "p1.jpg")])
        let evening = try store.commitImages(text: "Toronto Star, 2026-10-08", youtubeURLs: [],
                                             images: [(Data("p1".utf8), "p1.jpg"), (Data("p5".utf8), "p5.jpg")],
                                             replaces: [morning.id])
        XCTAssertEqual(evening.replaces, [morning.id])
        XCTAssertEqual(try CaptureStore(root: root).load(evening.id).replaces, [morning.id])
    }

    func testAManifestFromBeforeReplacementsStillLoads() throws {
        let capture = try store.commitDraft(text: "Old", youtubeURLs: [])
        let url = root.appendingPathComponent(capture.id + ".json")
        var json = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        json.removeValue(forKey: "replaces")
        try JSONSerialization.data(withJSONObject: json).write(to: url)
        XCTAssertEqual(try CaptureStore(root: root).load(capture.id).replaces, [])
    }

    func testAReplacementCannotNameItselfOrAPath() throws {
        var capture = Capture(text: "x")
        capture.replaces = [capture.id]
        XCTAssertThrowsError(try store.save(capture))
        capture.replaces = ["../draft"]
        XCTAssertThrowsError(try store.save(capture))
    }

    func testAReplacementWaitsUntilWhatItReplacesHasGoneUp() throws {
        let morning = try store.commitImages(text: "Morning", youtubeURLs: [], images: [(Data("a".utf8), "a.jpg")])
        let evening = try store.commitImages(text: "Evening", youtubeURLs: [], images: [(Data("b".utf8), "b.jpg")],
                                             replaces: [morning.id, ULID.make()])
        XCTAssertTrue(try store.waitsForReplaced(evening))
        var sent = try store.load(morning.id)
        sent.state = .synced
        try store.save(sent)
        XCTAssertFalse(try store.waitsForReplaced(evening))
    }

    func testSyncSendsTheOldEntryFirstThenRetiresItsCopy() async throws {
        let morning = try store.commitImages(text: "Morning", youtubeURLs: [], images: [(Data("a".utf8), "a.jpg")])
        let evening = try store.commitImages(text: "Evening", youtubeURLs: [], images: [(Data("b".utf8), "b.jpg")],
                                             replaces: [morning.id])
        let server = OrderedServer()
        try await CaptureSync(store: store).run(using: server)
        XCTAssertEqual(server.sent, [morning.id, evening.id])
        XCTAssertEqual(server.replaces[evening.id], [morning.id])
        // The server deleted the morning entry; the device's copy goes too.
        XCTAssertEqual(try store.list().map(\.id), [evening.id])
        XCTAssertEqual(try store.load(evening.id).state, .synced)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try store.fileURL(morning.files[0]).path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: try store.fileURL(evening.files[0]).path))
    }

    func testAReplacementIsHeldWhileTheOldOneCannotGoUp() async throws {
        let morning = try store.commitImages(text: "Morning", youtubeURLs: [], images: [(Data("a".utf8), "a.jpg")])
        let evening = try store.commitImages(text: "Evening", youtubeURLs: [], images: [(Data("b".utf8), "b.jpg")],
                                             replaces: [morning.id])
        let server = OrderedServer()
        server.offline = [morning.id]
        do { try await CaptureSync(store: store).run(using: server) } catch {}
        XCTAssertEqual(server.sent, [])
        XCTAssertEqual(try store.load(evening.id).state, .pending)
    }
}

private final class OrderedServer: JournalTransport {
    var sent: [String] = []
    var replaces: [String: [String]] = [:]
    var offline: Set<String> = []

    func send(_ capture: Capture, audioURL: URL?) async throws { XCTFail("Pages must reach the transport") }
    func send(_ capture: Capture, audioURL: URL?, files: [URL], clips: [URL]) async throws {
        if offline.contains(capture.id) { throw URLError(.notConnectedToInternet) }
        sent.append(capture.id)
        replaces[capture.id] = capture.replaces
    }
    func fetch(_ id: String) async throws -> JournalSnapshot { throw HTTPFailure(status: 404) }
}
