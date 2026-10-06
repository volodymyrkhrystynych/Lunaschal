import XCTest
@testable import LunaschalCore

final class NotebookTests: XCTestCase {
    private func directory() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    private func pdf(in root: URL) throws -> URL {
        let url = root.appendingPathComponent(UUID().uuidString + ".pdf")
        try Data("%PDF-1.7\n...".utf8).write(to: url)
        return url
    }

    func testCheckpointReopensWithPagesInOrderAndKeepsOnlyTwoRevisions() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let notebook = try store.create()
        XCTAssertEqual(try store.pages(notebook), [])
        try store.checkpoint(notebook.id, pages: [Data([1])], marked: [0], preview: Data([9]))
        let second = try store.checkpoint(notebook.id, pages: [Data([2]), Data([3])], marked: [1], preview: Data([9]))
        let third = try store.checkpoint(notebook.id, pages: [Data([4]), Data([5]), Data([6])], marked: [0, 2, 7], preview: Data([9]))
        let reopened = try NotebookStore(root: root)
        let loaded = try reopened.notebook(notebook.id)
        XCTAssertEqual(loaded.pageCount, 3)
        XCTAssertEqual(loaded.markedPages, [0, 2], "a mark past the last page is dropped")
        XCTAssertEqual(try reopened.pages(loaded), [Data([4]), Data([5]), Data([6])])
        let revisions = try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent(notebook.id).path)
        XCTAssertEqual(Set(revisions), [third.checkpoint!, second.checkpoint!])
    }

    func testFailedCheckpointKeepsThePreviousOne() throws {
        let store = try NotebookStore(root: directory())
        let notebook = try store.create()
        let saved = try store.checkpoint(notebook.id, pages: [Data([1])], marked: [], preview: Data([2]))
        XCTAssertThrowsError(try store.checkpoint(notebook.id, pages: [Data([3])], marked: [], preview: Data()))
        XCTAssertThrowsError(try store.checkpoint(notebook.id, pages: [Data([3]), Data()], marked: [], preview: Data([2])))
        XCTAssertThrowsError(try store.checkpoint(notebook.id, pages: [], marked: [], preview: Data([2])))
        XCTAssertEqual(try store.notebook(notebook.id), saved)
        XCTAssertEqual(try store.pages(saved), [Data([1])])
    }

    func testRestorePreviousGoesBackOneCheckpoint() throws {
        let store = try NotebookStore(root: directory())
        let notebook = try store.create()
        try store.checkpoint(notebook.id, pages: [Data([1]), Data([2])], marked: [], preview: Data([9]))
        try store.checkpoint(notebook.id, pages: [Data([3])], marked: [], preview: Data([9]))
        let restored = try store.restorePrevious(notebook.id)
        XCTAssertEqual(try store.pages(restored), [Data([1]), Data([2])])
    }

    func testOneYouTubeLinkReplacedOrRemoved() throws {
        let store = try NotebookStore(root: directory())
        let notebook = try store.create()
        try store.setYouTube(notebook.id, url: "https://youtu.be/dQw4w9WgXcQ")
        let replaced = try store.setYouTube(notebook.id, url: "https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        XCTAssertEqual(replaced.youtubeURL, try YouTubeLink.canonical("https://youtu.be/aqz-KE-bpKQ"))
        XCTAssertThrowsError(try store.setYouTube(notebook.id, url: "https://example.com/video"))
        XCTAssertEqual(try store.notebook(notebook.id).youtubeURL, replaced.youtubeURL)
        XCTAssertNil(try store.setYouTube(notebook.id, url: "  ").youtubeURL)
    }

    func testInboxKeepsScreenshotsUntilCleared() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        try store.enqueueScreenshot(Data([1]), now: Date(timeIntervalSince1970: 1))
        try store.enqueueScreenshot(Data([2]), now: Date(timeIntervalSince1970: 2))
        XCTAssertThrowsError(try store.enqueueScreenshot(Data()))
        let items = try NotebookStore(root: root).inboxItems()
        XCTAssertEqual(items.map(\.data), [Data([1]), Data([2])])
        store.clearInbox([items[0].url, root.appendingPathComponent("elsewhere.png")])
        XCTAssertEqual(try store.inboxItems().map(\.data), [Data([2])])
        XCTAssertEqual(store.inboxCount(), 1)
        // The inbox isn't mistaken for a notebook.
        XCTAssertEqual(try store.notebooks(), [])
    }

    func testMarkSavedAndDelete() throws {
        let store = try NotebookStore(root: directory())
        let notebook = try store.create()
        let saved = try store.markSaved(notebook.id, captureID: ULID.make())
        XCTAssertEqual(saved.savedCaptureIDs.count, 1)
        XCTAssertNotNil(saved.savedAt)
        try store.delete(notebook.id)
        XCTAssertEqual(try store.notebooks(), [])
        XCTAssertThrowsError(try store.notebook("../escape"))
    }

    // MARK: Newspaper

    func testNewspaperNotebookOwnsItsPDF() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let source = try pdf(in: root)
        let notebook = try store.createNewspaper(date: "2026-10-06", pdf: source, pageCount: 48)
        XCTAssertFalse(FileManager.default.fileExists(atPath: source.path), "moved, not copied")
        let reopened = try NotebookStore(root: root).notebook(notebook.id)
        XCTAssertEqual(reopened.newspaperDate, "2026-10-06")
        XCTAssertEqual(reopened.pdfPageCount, 48)
        XCTAssertEqual(reopened.title, "Toronto Star · 2026-10-06")
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(store.pdfURL(reopened))).prefix(5), Data("%PDF-".utf8))
        XCTAssertNil(try store.pdfURL(store.create()))
    }

    func testFailedDownloadLeavesNoNotebook() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let html = root.appendingPathComponent("error.pdf")
        try Data("<html>Issue PDF is unavailable</html>".utf8).write(to: html)
        XCTAssertThrowsError(try store.createNewspaper(date: "2026-10-06", pdf: html, pageCount: 48))
        XCTAssertThrowsError(try store.createNewspaper(date: "2026-10-06", pdf: root.appendingPathComponent("missing.pdf"), pageCount: 48))
        XCTAssertThrowsError(try store.createNewspaper(date: "../x", pdf: try pdf(in: root), pageCount: 48))
        XCTAssertThrowsError(try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 0))
        XCTAssertEqual(try store.notebooks(), [])
        let folders = try FileManager.default.contentsOfDirectory(atPath: root.path).filter { ULID.isValid($0) }
        XCTAssertEqual(folders, [])
    }

    func testCheckpointCannotDropIssuePages() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let notebook = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 3)
        XCTAssertThrowsError(try store.checkpoint(notebook.id, pages: [Data([1])], marked: [], preview: Data([9])))
        let saved = try store.checkpoint(notebook.id, pages: [Data([1]), Data([2]), Data([3]), Data([4])],
                                         marked: [2, 3], preview: Data([9]))
        XCTAssertEqual(saved.pageCount, 4)
        XCTAssertEqual(saved.markedPages, [2, 3])
    }

    func testUnsavedNewspaperIsReusedUntilFiled() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let first = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 3)
        XCTAssertEqual(try store.unsavedNewspaper(date: "2026-10-06")?.id, first.id)
        XCTAssertNil(try store.unsavedNewspaper(date: "2026-10-05"))
        try store.markSaved(first.id, captureID: ULID.make())
        XCTAssertNil(try store.unsavedNewspaper(date: "2026-10-06"))
    }

    func testPagesToFile() {
        var blank = Notebook(title: "Notes", source: .blank, pageCount: 3, pdfPageCount: 0, now: Date())
        blank.markedPages = [1]
        XCTAssertEqual(blank.pagesToFile, [0, 1, 2], "a blank notebook files every page")
        var paper = Notebook(title: "", source: .newspaper(date: "2026-10-06"), pageCount: 60, pdfPageCount: 60, now: Date())
        XCTAssertEqual(paper.pagesToFile, [0], "the cover alone")
        paper.markedPages = [12, 3, 70]
        XCTAssertEqual(paper.pagesToFile, [0, 3, 12])
    }

    func testIssueChoiceUsesTheFourAMDay() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        func at(_ day: Int, _ hour: Int) -> Date {
            calendar.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour))!
        }
        let issues = [NewspaperIssue(date: "2026-10-04", pageCount: 40),
                      NewspaperIssue(date: "2026-10-05", pageCount: 44),
                      NewspaperIssue(date: "2026-10-07", pageCount: 50)]
        XCTAssertEqual(NewspaperIssue.choose(issues, now: at(5, 20), calendar: calendar), .today(issues[1]))
        // 01:30 on the 6th is still the 5th's day.
        XCTAssertEqual(NewspaperIssue.choose(issues, now: at(6, 1), calendar: calendar), .today(issues[1]))
        // The 6th's paper isn't in; the newest not in the future is offered.
        XCTAssertEqual(NewspaperIssue.choose(issues, now: at(6, 9), calendar: calendar), .newest(issues[1]))
        XCTAssertEqual(NewspaperIssue.choose([], now: at(6, 9), calendar: calendar), .none)
    }

    func testIssueFromReplicaRecord() {
        XCTAssertEqual(NewspaperIssue(record: ["date": .string("2026-10-06"), "pageCount": .number(48)]),
                       NewspaperIssue(date: "2026-10-06", pageCount: 48))
        XCTAssertEqual(NewspaperIssue(record: ["date": .string("2026-10-06"), "page_count": .number(48)])?.pageCount, 48)
        XCTAssertNil(NewspaperIssue(record: ["date": .string("yesterday"), "pageCount": .number(48)]))
        XCTAssertNil(NewspaperIssue(record: ["date": .string("2026-10-06"), "pageCount": .number(0)]))
        XCTAssertNil(NewspaperIssue(record: nil))
    }
}
