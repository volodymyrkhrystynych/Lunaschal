import XCTest
@testable import LunaschalCore

final class WatchHandoffTests: XCTestCase {
    func testTemporaryFileCanDisappearBeforeImportAndDuplicateKeepsUploadState() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root.appendingPathComponent("captures"))
        let inbox = try WatchInbox(root: root.appendingPathComponent("inbox"))
        let source = root.appendingPathComponent("system-temp.m4a")
        try Data("audio".utf8).write(to: source)
        var capture = Capture(mode: .transcribe, now: Date(timeIntervalSince1970: 12345))
        capture.state = .pending
        let envelope = try WatchEnvelope(capture: capture, bytes: 5, sha256: String(repeating: "a", count: 64))
        let metadata = try JSONEncoder().encode(envelope)
        try inbox.stage(file: source, metadata: metadata)
        try inbox.stage(file: source, metadata: metadata)
        try FileManager.default.removeItem(at: source)
        let ids = try inbox.drain(into: store, hash: { _ in envelope.sha256 })
        XCTAssertEqual(ids, [capture.id, capture.id])
        XCTAssertEqual(try store.list().count, 1)
        XCTAssertEqual(try store.load(capture.id).createdAt, capture.createdAt)
        capture.state = .synced
        try store.save(capture)
        try inbox.stage(file: store.audioURL(capture), metadata: metadata)
        _ = try inbox.drain(into: store, hash: { _ in envelope.sha256 })
        XCTAssertEqual(try store.load(capture.id).state, .synced)
        XCTAssertEqual(try Data(contentsOf: store.audioURL(capture)), Data("audio".utf8))
    }

    func testCorruptionIsNotAcknowledgedOrPublished() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try CaptureStore(root: root.appendingPathComponent("captures"))
        let inbox = try WatchInbox(root: root.appendingPathComponent("inbox"))
        let source = root.appendingPathComponent("temp.m4a")
        try Data("audio".utf8).write(to: source)
        var capture = Capture(mode: .record)
        capture.state = .pending
        let envelope = try WatchEnvelope(capture: capture, bytes: 5, sha256: String(repeating: "b", count: 64))
        try inbox.stage(file: source, metadata: JSONEncoder().encode(envelope))
        XCTAssertThrowsError(try inbox.drain(into: store, hash: { _ in "wrong" }))
        XCTAssertTrue(try store.list().isEmpty)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: inbox.root.path).count, 1)
        var good = Capture(mode: .record)
        good.state = .pending
        let goodEnvelope = try WatchEnvelope(capture: good, bytes: 5, sha256: String(repeating: "a", count: 64))
        try inbox.stage(file: source, metadata: JSONEncoder().encode(goodEnvelope))
        var errors = 0
        let received = try inbox.drain(into: store, hash: { _ in goodEnvelope.sha256 }, onFailure: { _ in errors += 1 })
        XCTAssertEqual(received, [good.id])
        XCTAssertEqual(errors, 1)
        XCTAssertEqual(try store.list().count, 1)
    }
}
