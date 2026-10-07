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

    func testLockedLayersTravelWithTheirPages() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let notebook = try store.create()
        let first = try store.checkpoint(notebook.id, pages: [Data([1]), Data([2]), Data([3])],
                                         locked: [nil, Data([7])], marked: [1], preview: Data([9]))
        XCTAssertEqual(try NotebookStore(root: root).lockedLayers(first), [nil, Data([7]), nil])
        // Unlocking writes the next checkpoint without the layer; the old one keeps it.
        let second = try store.checkpoint(notebook.id, pages: [Data([1]), Data([8]), Data([3])],
                                          marked: [1], preview: Data([9]))
        XCTAssertEqual(try store.lockedLayers(second), [nil, nil, nil])
        XCTAssertEqual(try store.lockedLayers(store.restorePrevious(notebook.id)), [nil, Data([7]), nil])
        XCTAssertThrowsError(try store.checkpoint(notebook.id, pages: [Data([1])], locked: [Data()],
                                                  marked: [], preview: Data([9])))
        XCTAssertThrowsError(try store.checkpoint(notebook.id, pages: [Data([1])], locked: [nil, Data([1])],
                                                  marked: [], preview: Data([9])))
        XCTAssertEqual(try store.lockedLayers(NotebookStore(root: root).notebook(notebook.id)), [nil, Data([7]), nil])
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
        // A paged issue, as every one was before columns.
        var notebook = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 3)
        notebook.layout = nil
        try JSONEncoder().encode(notebook).write(to: root.appendingPathComponent(notebook.id + ".json"))
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

final class NotebookColumnStoreTests: XCTestCase {
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

    func testANewIssueIsAColumnAndABlankNotebookIsNot() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let paper = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 40)
        XCTAssertTrue(paper.isColumn)
        XCTAssertEqual(paper.markupCount, 1)
        XCTAssertFalse(try store.create().isColumn)
    }

    func testANotebookFromBeforeColumnsReadsAsPaged() throws {
        // A manifest written by the previous build: no `layout` key at all.
        let root = try directory(), store = try NotebookStore(root: root)
        var paper = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 2)
        paper.layout = nil
        var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(paper)) as! [String: Any]
        json.removeValue(forKey: "layout")
        try JSONSerialization.data(withJSONObject: json).write(to: root.appendingPathComponent(paper.id + ".json"))
        let reopened = try store.notebook(paper.id)
        XCTAssertFalse(reopened.isColumn)
        XCTAssertEqual(reopened.markupCount, 2)
    }

    func testAColumnSavesOneMarkupForEveryPage() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let paper = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 40)
        let saved = try store.checkpoint(paper.id, pages: [Data([7])], locked: [Data([8])], marked: [3, 39, 40],
                                         preview: Data([9]), column: 41)
        XCTAssertEqual(saved.pageCount, 41, "an added page at the foot of the column")
        XCTAssertEqual(saved.markedPages, [3, 39, 40])
        XCTAssertEqual(try store.pages(saved), [Data([7])])
        XCTAssertEqual(try store.lockedLayers(saved), [Data([8])])
    }

    func testAColumnCannotDropIssuePagesOrBeSavedAsPages() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let paper = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 3)
        XCTAssertThrowsError(try store.checkpoint(paper.id, pages: [Data([1])], marked: [], preview: Data([9]), column: 2))
        XCTAssertThrowsError(try store.checkpoint(paper.id, pages: [Data([1]), Data([2])], marked: [], preview: Data([9]), column: 3))
        XCTAssertThrowsError(try store.checkpoint(paper.id, pages: [Data([1]), Data([2]), Data([3])], marked: [], preview: Data([9])),
                             "pages saved over a column would be read back as one stacked markup")
    }

    func testConvertingAPagedIssueKeepsThePagedOriginalOneRestoreAway() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        var paper = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 3)
        // As the previous build left it: paged, three markups.
        paper.layout = nil
        try JSONEncoder().encode(paper).write(to: root.appendingPathComponent(paper.id + ".json"))
        try store.checkpoint(paper.id, pages: [Data([1]), Data([2]), Data([3])], marked: [1], preview: Data([9]))
        let column = try store.checkpoint(paper.id, pages: [Data([4])], marked: [1], preview: Data([9]), column: 3)
        XCTAssertTrue(column.isColumn)
        let restored = try store.restorePrevious(paper.id)
        XCTAssertFalse(restored.isColumn, "back to the paged pages, to be converted again on open")
        XCTAssertEqual(restored.pageCount, 3)
        XCTAssertEqual(try store.pages(restored), [Data([1]), Data([2]), Data([3])])
    }

    func testRestoringAColumnKeepsItsPageCount() throws {
        let root = try directory(), store = try NotebookStore(root: root)
        let paper = try store.createNewspaper(date: "2026-10-06", pdf: try pdf(in: root), pageCount: 3)
        try store.checkpoint(paper.id, pages: [Data([1])], marked: [], preview: Data([9]), column: 4)
        try store.checkpoint(paper.id, pages: [Data([2])], marked: [], preview: Data([9]), column: 4)
        let restored = try store.restorePrevious(paper.id)
        XCTAssertTrue(restored.isColumn)
        XCTAssertEqual(restored.pageCount, 4)
        XCTAssertEqual(try store.pages(restored), [Data([1])])
    }
}

final class NotebookColumnTests: XCTestCase {
    // A broadsheet, a wide spread and a tabloid, at the column's width.
    private let slots = NotebookColumn.slots(heights: [2232, 868, 1612])

    func testPagesStackAtFullWidthWithNothingBetweenThem() {
        XCTAssertEqual(slots, [
            CGRect(x: 0, y: 0, width: 1240, height: 2232),
            CGRect(x: 0, y: 2232, width: 1240, height: 868),
            CGRect(x: 0, y: 3100, width: 1240, height: 1612),
        ])
        XCTAssertEqual(NotebookColumn.bounds(of: slots), CGRect(x: 0, y: 0, width: 1240, height: 4712))
    }

    func testThePageAtAHeight() {
        XCTAssertEqual(NotebookColumn.slot(atY: 0, in: slots), 0)
        XCTAssertEqual(NotebookColumn.slot(atY: 2231, in: slots), 0)
        XCTAssertEqual(NotebookColumn.slot(atY: 2232, in: slots), 1)
        XCTAssertEqual(NotebookColumn.slot(atY: 4000, in: slots), 2)
        XCTAssertEqual(NotebookColumn.slot(atY: 99_999, in: slots), 2)
        XCTAssertEqual(NotebookColumn.slot(atY: -10, in: slots), 0)
    }

    func testEitherWayUpAPageFillsTheWidthFromItsTop() {
        for view in [CGSize(width: 1032, height: 1270), CGSize(width: 1376, height: 950)] {
            let shown = NotebookColumn.visibleFrame(slot: slots[1], view: view)
            XCTAssertEqual(shown.minX, 0)
            XCTAssertEqual(shown.width, 1240, "nothing off to either side")
            XCTAssertEqual(shown.minY, 2232, "from the top of the page")
        }
    }

    func testWhichPagesCarryInk() {
        // Ten units a row: rows 0-223 are page 1, 224-309 page 2, the rest page 3.
        var rows = Array(repeating: false, count: 472)
        rows[5] = true
        rows[400] = true
        XCTAssertEqual(NotebookColumn.slotsWithInk(rows: rows, unitsPerRow: 10, slots: slots), [0, 2])
        rows[250] = true
        XCTAssertEqual(NotebookColumn.slotsWithInk(rows: rows, unitsPerRow: 10, slots: slots), [0, 1, 2])
        XCTAssertEqual(NotebookColumn.slotsWithInk(rows: [], unitsPerRow: 10, slots: slots), [])
    }
}

final class NotebookFitTests: XCTestCase {
    func testAnA4PageFitsWholeInEitherOrientation() {
        let a4 = CGRect(x: 0, y: 0, width: 1240, height: 1754)
        for view in [CGSize(width: 1024, height: 1290), CGSize(width: 1366, height: 950)] {
            let shown = NotebookFit.whole(a4, in: view)
            XCTAssertTrue(shown.insetBy(dx: -0.5, dy: -0.5).contains(a4), "nothing of the page is off screen")
            XCTAssertEqual(shown.height / shown.width, view.height / view.width, accuracy: 0.001)
        }
    }
}

final class PageSwipeTests: XCTestCase {
    func testAPageTurnIsADeliberateDrag() {
        XCTAssertEqual(PageSwipe.threshold(pageWidth: 1000), 350)
        XCTAssertEqual(PageSwipe.threshold(pageWidth: 200), 120)
    }

    func testThePageFollowsTheFinger() {
        let preview = PageSwipe.preview(dx: -100, index: 1, count: 3, threshold: 200)
        XCTAssertEqual(preview, .init(offset: -100, creating: false, progress: 0.5, armed: false))
        XCTAssertEqual(PageSwipe.outcome(dx: -100, index: 1, count: 3, threshold: 200), .stay, "springs back")
        XCTAssertEqual(PageSwipe.outcome(dx: -250, index: 1, count: 3, threshold: 200), .next)
        XCTAssertEqual(PageSwipe.outcome(dx: 250, index: 1, count: 3, threshold: 200), .previous)
    }

    func testFinishingTheSwipeOnTheLastPageAddsOne() {
        let near = PageSwipe.preview(dx: -80, index: 2, count: 3, threshold: 200)
        XCTAssertTrue(near.creating)
        XCTAssertFalse(near.armed)
        XCTAssertTrue(PageSwipe.preview(dx: -200, index: 2, count: 3, threshold: 200).armed)
        XCTAssertEqual(PageSwipe.outcome(dx: -200, index: 2, count: 3, threshold: 200), .newPage)
    }

    func testBackwardsFromTheFirstPageNothingMoves() {
        XCTAssertEqual(PageSwipe.preview(dx: 300, index: 0, count: 3, threshold: 200), .idle)
        XCTAssertEqual(PageSwipe.outcome(dx: 300, index: 0, count: 3, threshold: 200), .stay)
    }
}
