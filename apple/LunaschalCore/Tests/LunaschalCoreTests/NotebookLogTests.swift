import XCTest
@testable import LunaschalCore

final class NotebookLogTests: XCTestCase {
    private func url() -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".txt")
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testLinesAreTimedAndSurviveReopening() {
        let file = url()
        let log = NotebookLog(url: file, now: { Date(timeIntervalSince1970: 0) })
        log.record("turn p1→p2")
        log.record("save filed p1 p2")
        XCTAssertEqual(log.lines.count, 2)
        XCTAssertTrue(log.lines[0].hasSuffix(" turn p1→p2"))
        XCTAssertEqual(NotebookLog(url: file).lines, log.lines, "read back from the file")
    }

    func testOnlyTheNewestLinesAreKept() {
        let file = url()
        let log = NotebookLog(url: file)
        for index in 0..<(NotebookLog.limit + 25) { log.record("event \(index)") }
        XCTAssertEqual(log.lines.count, NotebookLog.limit)
        XCTAssertTrue(log.lines.last!.hasSuffix("event \(NotebookLog.limit + 24)"))
        XCTAssertTrue(log.lines.first!.hasSuffix("event 25"))
        XCTAssertEqual(NotebookLog(url: file).lines.count, NotebookLog.limit)
    }

    func testClearEmptiesTheFileToo() {
        let file = url()
        let log = NotebookLog(url: file)
        log.record("something")
        log.clear()
        XCTAssertEqual(log.text, "")
        XCTAssertEqual(NotebookLog(url: file).lines, [])
    }

    func testPageCountsReadFromOne() {
        XCTAssertEqual(NotebookLog.pages([3: 1, 0: 12]), "p1×12 p4×1")
        XCTAssertEqual(NotebookLog.pages([:]), "")
    }
}
