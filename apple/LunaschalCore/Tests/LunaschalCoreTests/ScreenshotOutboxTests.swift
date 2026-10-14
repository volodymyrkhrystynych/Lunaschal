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

    func testUnreachableServerIsRetriedWithinTheBoundThenGivenUp() async throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let shot = try queue.append(file: file, filename: "game.png", contentType: "image/png")
        let transport = ScreenshotRecorder()
        transport.failures = [URLError(.cannotFindHost), URLError(.cannotFindHost)]
        var slept: [TimeInterval] = []
        try await queue.runRetrying(using: transport, delays: [1, 2, 3]) { slept.append($0) }
        XCTAssertEqual(slept, [1, 2])
        XCTAssertEqual(transport.ids, [shot.id, shot.id, shot.id])
        XCTAssertTrue(try queue.list().isEmpty)

        let stuck = try queue.append(file: file, filename: "game.png", contentType: "image/png")
        transport.fail = true
        slept = []
        do {
            try await queue.runRetrying(using: transport, delays: [1, 2]) { slept.append($0) }
            XCTFail("Expected failure")
        } catch let error as URLError { XCTAssertTrue(error.isUnreachable) }
        XCTAssertEqual(slept, [1, 2])
        XCTAssertEqual(try queue.list(), [stuck])
    }

    func testAServerErrorIsNotRetried() async throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        _ = try queue.append(file: file, filename: "game.png", contentType: "image/png")
        let transport = ScreenshotRecorder()
        transport.failures = [HTTPFailure(status: 400)]
        var slept: [TimeInterval] = []
        do {
            try await queue.runRetrying(using: transport) { slept.append($0) }
            XCTFail("Expected failure")
        } catch is HTTPFailure {}
        XCTAssertTrue(slept.isEmpty)
        XCTAssertEqual(transport.ids.count, 1)
    }

    func testUploadRequestPostsTheShotToTheScreenshotRoute() throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let shot = try queue.append(file: file, filename: "game.png", contentType: "image/png")
        let api = try JournalAPI(server: URL(string: "https://server.tailnet.ts.net")!, token: "tok", allowCellular: true)
        let (request, body) = try api.screenshotUploadRequest(shot, file: queue.file(for: shot))
        defer { try? FileManager.default.removeItem(at: body) }
        XCTAssertEqual(request.url?.absoluteString, "https://server.tailnet.ts.net/api/journal/screenshots")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "lunaschal_token=tok")
        XCTAssertTrue(request.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
        let text = String(decoding: try Data(contentsOf: body), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"attachmentId\"\r\n\r\n\(shot.id)\r\n"))
        XCTAssertTrue(text.contains("name=\"capturedAt\"\r\n\r\n\(shot.capturedAt)\r\n"))
        XCTAssertTrue(text.contains("filename=\"game.png\""))
    }

    func testBodiesArePrunedOnlyOnceDeliveredAndOld() throws {
        let (root, queue, file) = try fixture()
        defer { try? FileManager.default.removeItem(at: root) }
        let waiting = try queue.append(file: file, filename: "one.png", contentType: "image/png")
        let delivered = try queue.append(file: file, filename: "two.png", contentType: "image/png")
        try FileManager.default.createDirectory(at: queue.bodies, withIntermediateDirectories: true)
        for shot in [waiting, delivered] { try Data([9]).write(to: queue.body(for: shot)) }
        XCTAssertTrue(queue.isHandedOff(waiting))
        XCTAssertEqual(try queue.list().count, 2, "the body folder is never read as a shot")
        try FileManager.default.removeItem(at: queue.root.appendingPathComponent(delivered.id))

        queue.pruneBodies(olderThan: 3600, now: Date())
        XCTAssertTrue(queue.isHandedOff(delivered), "a fresh body may still be uploading")
        queue.pruneBodies(olderThan: 3600, now: Date().addingTimeInterval(7200))
        XCTAssertFalse(queue.isHandedOff(delivered))
        XCTAssertTrue(queue.isHandedOff(waiting), "a shot still queued keeps its body")
    }
}

private final class ScreenshotRecorder: ScreenshotTransport {
    var fail = false
    var failures: [Error] = []
    var ids: [String] = []
    var duringSend: (() async throws -> Void)?
    func sendScreenshot(_ shot: SharedScreenshot, file: URL) async throws {
        XCTAssertEqual(try Data(contentsOf: file), Data([0, 1, 2, 3]))
        ids.append(shot.id)
        if !failures.isEmpty { throw failures.removeFirst() }
        if fail { throw URLError(.networkConnectionLost) }
        try await duringSend?()
    }
}
