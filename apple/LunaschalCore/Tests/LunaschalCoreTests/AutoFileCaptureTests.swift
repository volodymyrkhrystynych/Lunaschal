import Foundation
import XCTest
@testable import LunaschalCore

/// The Capture tab's Save button may let the server file a capture as a meal
/// or a purchase; the phone must say so, and must not read the move as a
/// deletion.
final class AutoFileCaptureTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func testTheFlagTravelsWithTheCaptureAndReachesTheCreate() throws {
        let saved = try store.commitDraft(text: "had pho for lunch", youtubeURLs: [], autoFile: true)
        let reloaded = try CaptureStore(root: root).load(saved.id)
        XCTAssertTrue(reloaded.autoFile)
        XCTAssertEqual(JournalAPI.entryBody(reloaded)["autoFile"], "1")
    }

    func testWithoutItNothingIsSent() throws {
        let plain = try store.commitDraft(text: "a thought", youtubeURLs: [])
        XCTAssertFalse(plain.autoFile)
        XCTAssertNil(JournalAPI.entryBody(plain)["autoFile"], "Off sends nothing, as before")
    }

    func testAMealIsNeverAutoFiled() throws {
        // Food and Spent already chose; only a journal save is left to the server.
        let meal = try store.commitDraft(text: "pho", youtubeURLs: [], kind: .food, autoFile: true)
        XCTAssertFalse(meal.autoFile)
    }

    func testACaptureSavedBeforeTheFlagStillLoads() throws {
        var old = Capture(text: "Original")
        old.state = .synced
        try store.save(old)
        let manifest = root.appendingPathComponent(old.id + ".json")
        var json = try JSONSerialization.jsonObject(with: Data(contentsOf: manifest)) as! [String: Any]
        json.removeValue(forKey: "autoFile")
        try JSONSerialization.data(withJSONObject: json).write(to: manifest)
        XCTAssertFalse(try store.load(old.id).autoFile)
    }

    func testAnEntryFiledElsewhereIsPresentNotRemoved() {
        XCTAssertEqual(CaptureStore.EntryOnServer.of(journalDeleted: true, filedElsewhere: true), .present)
        XCTAssertEqual(CaptureStore.EntryOnServer.of(journalDeleted: nil, filedElsewhere: true), .present)
        XCTAssertEqual(CaptureStore.EntryOnServer.of(journalDeleted: true, filedElsewhere: false), .removed)
        XCTAssertEqual(CaptureStore.EntryOnServer.of(journalDeleted: false, filedElsewhere: false), .present)
        XCTAssertEqual(CaptureStore.EntryOnServer.of(journalDeleted: nil, filedElsewhere: false), .unknown)
    }

    func testARemovalReadTooEarlyIsTakenBack() throws {
        var capture = Capture(text: "pho")
        capture.state = .synced
        try store.save(capture)
        // The journal tombstone landed before the meal it was filed as.
        XCTAssertEqual(try store.tidySynced(entry: { _ in .removed }), 1)
        XCTAssertEqual(try store.load(capture.id).lastError, CaptureStore.removedOnServer)
        XCTAssertEqual(try store.tidySynced(entry: { _ in .present }), 1)
        XCTAssertNil(try store.load(capture.id).lastError)
    }
}
