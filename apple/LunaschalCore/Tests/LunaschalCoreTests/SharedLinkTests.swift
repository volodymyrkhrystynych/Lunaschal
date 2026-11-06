import Foundation
import XCTest
@testable import LunaschalCore

final class SharedLinkTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    func testFindsAYouTubeLinkInWhateverWasShared() {
        let canonical = "https://www.youtube.com/watch?v=aircAruvnKk"
        XCTAssertEqual(YouTubeLink.find(in: "https://youtu.be/aircAruvnKk"), canonical)
        XCTAssertEqual(YouTubeLink.find(in: "But what is a neural network?\nhttps://www.youtube.com/watch?v=aircAruvnKk&t=30"), canonical)
        XCTAssertEqual(YouTubeLink.find(in: "<https://m.youtube.com/shorts/aircAruvnKk>"), canonical)
        XCTAssertNil(YouTubeLink.find(in: "https://archiveofourown.org/works/123"))
        XCTAssertNil(YouTubeLink.find(in: "no link here"))
    }

    func testInboxKeepsEachVideoOnceInCanonicalForm() throws {
        let inbox = try SharedLinkInbox(root: root)
        try inbox.append("https://youtu.be/aircAruvnKk")
        try inbox.append("https://www.youtube.com/watch?v=aircAruvnKk")
        try inbox.append("https://youtu.be/dQw4w9WgXcQ")
        XCTAssertEqual(try inbox.list().map(\.url), ["https://www.youtube.com/watch?v=aircAruvnKk",
                                                     "https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
        XCTAssertThrowsError(try inbox.append("ftp://example.com/video"))
    }

    func testFindsAnyWebLinkForTheJournal() {
        // A video is still canonicalised; any other page is kept as shared.
        XCTAssertEqual(JournalLink.find(in: "https://youtu.be/aircAruvnKk"),
                       "https://www.youtube.com/watch?v=aircAruvnKk")
        XCTAssertEqual(JournalLink.find(in: "How bridges stand\nhttps://example.com/articles/bridges?ref=share"),
                       "https://example.com/articles/bridges?ref=share")
        XCTAssertEqual(JournalLink.find(in: "<https://example.com/a>"), "https://example.com/a")
        XCTAssertNil(JournalLink.find(in: "no link here"))
        XCTAssertNil(JournalLink.find(in: "ftp://example.com/file"))
    }

    func testJournalLinkRefusesWhatIsNotAPlainWebLink() {
        for bad in ["ftp://example.com/file", "javascript:alert(1)", "https://user:pw@example.com/",
                    "https://", "example.com/no-scheme"] {
            XCTAssertThrowsError(try JournalLink.canonical(bad), bad)
        }
        XCTAssertTrue(JournalLink.isYouTube("https://youtu.be/aircAruvnKk"))
        XCTAssertFalse(JournalLink.isYouTube("https://example.com/watch?v=aircAruvnKk"))
    }

    func testInboxTakesAnyWebPageToo() throws {
        let inbox = try SharedLinkInbox(root: root)
        try inbox.append("https://example.com/articles/bridges")
        try inbox.append("https://example.com/articles/bridges")
        XCTAssertEqual(try inbox.list().map(\.url), ["https://example.com/articles/bridges"])
    }

    func testTakingLinksLeavesOnesSharedMeanwhile() throws {
        let inbox = try SharedLinkInbox(root: root)
        try inbox.append("https://youtu.be/aircAruvnKk")
        let taken = try inbox.list()
        // The extension, in its own process, shares another before the app finishes.
        try SharedLinkInbox(root: root).append("https://youtu.be/dQw4w9WgXcQ")
        try inbox.remove(taken)
        XCTAssertEqual(try inbox.list().map(\.url), ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
    }

    func testDraftLinksMergeKeepsOrderAndDropsRepeats() {
        let a = "https://www.youtube.com/watch?v=aircAruvnKk"
        let b = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        XCTAssertEqual(DraftLinks.merge("", [a]), a)
        XCTAssertEqual(DraftLinks.merge(a, [b, a]), a + "\n" + b)
        XCTAssertEqual(DraftLinks.merge(a + "\n" + b, []), a + "\n" + b)
    }

    func testDiscardingTheComposerDraftRemovesItsFiles() throws {
        let store = try CaptureStore(root: root)
        let file = try store.stageFile(data: Data("photo".utf8), name: "a.jpg", contentType: "image/jpeg")
        let url = try store.fileURL(file)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        try store.discardDraft(for: nil)
        XCTAssertTrue(try store.draft().isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }
}
