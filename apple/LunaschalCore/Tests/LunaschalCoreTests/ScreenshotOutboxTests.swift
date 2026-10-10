import XCTest
@testable import LunaschalCore

final class ScreenshotOutboxTests: XCTestCase {
    func testAcknowledgementMustMatchBothAttachmentAndEntry() throws {
        let entry = ULID.make(), attachment = ULID.make()
        func receipt(_ id: String, _ parent: String) throws -> Data {
            try JSONSerialization.data(withJSONObject: ["id": entry, "attachment": ["id": id, "entryId": parent]])
        }
        XCTAssertNoThrow(try JournalAPI.validateScreenshotReceipt(receipt(attachment, entry), attachmentID: attachment))
        XCTAssertThrowsError(try JournalAPI.validateScreenshotReceipt(receipt(ULID.make(), entry), attachmentID: attachment))
        XCTAssertThrowsError(try JournalAPI.validateScreenshotReceipt(receipt(attachment, ULID.make()), attachmentID: attachment))
        XCTAssertThrowsError(try JournalAPI.validateScreenshotReceipt(Data("{}".utf8), attachmentID: attachment))
    }

    private func fixture() throws -> (URL, ScreenshotOutbox, URL) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let queue = try ScreenshotOutbox(root: root.appendingPathComponent("queue"))
        let file = root.appendingPathComponent("source.png")
        try Data([0, 1, 2, 3]).write(to: file)
        return (root, queue, file)
    }

    func testRestartPreservesBytesIDAndLocalTimestamp() throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let shot = try queue.append(file: file, filename: "game.png", contentType: "image/png",
                                    now: Date(timeIntervalSince1970: 0), timeZone: TimeZone(secondsFromGMT: -18000)!)
        try FileManager.default.removeItem(at: file)
        let reopened = try ScreenshotOutbox(root: queue.root)
        XCTAssertEqual(try reopened.list(), [shot])
        XCTAssertEqual(shot.capturedAt, "1969-12-31T19:00:00.000-05:00")
        XCTAssertEqual(try Data(contentsOf: reopened.file(for: shot)), Data([0, 1, 2, 3]))
    }

    func testFailureRetainsImageAndRetryUsesSameID() async throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let shot = try queue.append(file: file, filename: "game.png", contentType: "image/png")
        let transport = ScreenshotRecorder()
        transport.fail = true
        do { try await queue.run(using: transport); XCTFail("Expected failure") } catch {}
        XCTAssertEqual(try queue.list(), [shot])
        transport.fail = false
        try await queue.run(using: transport)
        XCTAssertEqual(transport.ids, [shot.id, shot.id])
        XCTAssertTrue(try queue.list().isEmpty)
    }

    func testConcurrentDrainerDoesNotDuplicateAndNewSharesSurvive() async throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let first = try queue.append(file: file, filename: "one.png", contentType: "image/png")
        let concurrent = ScreenshotRecorder()
        let transport = ScreenshotRecorder()
        transport.duringSend = {
            let other = try ScreenshotOutbox(root: queue.root)
            try await other.run(using: concurrent)
            try other.append(file: file, filename: "two.png", contentType: "image/png")
        }
        try await queue.run(using: transport)
        XCTAssertEqual(transport.ids, [first.id])
        XCTAssertTrue(concurrent.ids.isEmpty)
        XCTAssertEqual(try queue.list().map(\.filename), ["two.png"])
        try await queue.run(using: concurrent)
        XCTAssertEqual(concurrent.ids.count, 1)
        XCTAssertTrue(try queue.list().isEmpty)
    }

    func testFailedCopyNeverPublishesAnItem() throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.removeItem(at: file)
        XCTAssertThrowsError(try queue.append(file: file, filename: "game.png", contentType: "image/png"))
        XCTAssertTrue(try queue.list().isEmpty)
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: queue.root.path).isEmpty)
    }
}

private final class ScreenshotRecorder: ScreenshotTransport {
    var fail = false
    var ids: [String] = []
    var duringSend: (() async throws -> Void)?
    func sendScreenshot(_ shot: SharedScreenshot, file: URL) async throws {
        XCTAssertEqual(try Data(contentsOf: file), Data([0, 1, 2, 3]))
        ids.append(shot.id)
        if fail { throw URLError(.networkConnectionLost) }
        try await duringSend?()
    }
}
