import Foundation
import XCTest
@testable import LunaschalCore

final class WatchReceiptTests: XCTestCase {
    private var root: URL!
    private var watch: CaptureStore!
    private var phone: CaptureStore!
    private var capture: Capture!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        watch = try CaptureStore(root: root.appendingPathComponent("watch"))
        phone = try CaptureStore(root: root.appendingPathComponent("phone"))
        capture = Capture(mode: .record)
        capture.state = .pending
        for store in [watch!, phone!] {
            try store.save(capture)
            try Data("original audio".utf8).write(to: store.audioURL(capture))
        }
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    func testServerReceiptSurvivesReopenAndConfirmedRemovalKeepsPhoneOriginal() throws {
        let sender = WatchReceipts(store: phone)
        try sender.markOrigin(capture)
        XCTAssertTrue(try sender.pendingServerReceipts().isEmpty)
        var uploaded = capture!
        uploaded.state = .synced
        try phone.save(uploaded)
        let receipt = try XCTUnwrap(sender.pendingServerReceipts().first)
        try WatchReceipts(store: watch).acceptServerReceipt(receipt)
        let reopened = try CaptureStore(root: watch.root)
        let receiver = WatchReceipts(store: reopened)
        XCTAssertTrue(try receiver.serverReceived(capture))
        XCTAssertEqual(try reopened.load(capture.id).state, .pending)
        try sender.confirmDelivery(receipt)
        try sender.markOrigin(capture) // replayed file handoff must not clear delivery
        XCTAssertTrue(try WatchReceipts(store: CaptureStore(root: phone.root)).pendingServerReceipts().isEmpty)
        try receiver.removeWatchCopy(capture.id)
        XCTAssertTrue(try reopened.list().isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: try reopened.audioURL(capture).path))
        XCTAssertEqual(try Data(contentsOf: phone.audioURL(capture)), Data("original audio".utf8))
        XCTAssertEqual(try phone.load(capture.id).state, .synced)
        try receiver.acceptServerReceipt(receipt) // lost delivery acknowledgement
        XCTAssertTrue(try reopened.list().isEmpty)
    }

    func testNoReceiptOrMismatchedReceiptCannotDeleteRecording() throws {
        let receiver = WatchReceipts(store: watch)
        XCTAssertThrowsError(try receiver.removeWatchCopy(capture.id))
        let bytes = try JSONSerialization.data(withJSONObject: ["captureID": capture.id, "attachmentID": ULID.make()])
        let forged = try JSONDecoder().decode(WatchServerReceipt.self, from: bytes)
        XCTAssertThrowsError(try receiver.acceptServerReceipt(forged))
        try bytes.write(to: watch.root.appendingPathComponent(capture.id + ".server-receipt"))
        XCTAssertThrowsError(try receiver.removeWatchCopy(capture.id))
        XCTAssertEqual(try Data(contentsOf: watch.audioURL(capture)), Data("original audio".utf8))
        XCTAssertEqual(try watch.load(capture.id), capture)
    }

    func testOnlyWatchOriginAndDurablyUploadedCapturesProduceReceipts() throws {
        let sender = WatchReceipts(store: phone)
        var uploaded = capture!
        uploaded.state = .synced
        try phone.save(uploaded)
        XCTAssertTrue(try sender.pendingServerReceipts().isEmpty)
        XCTAssertThrowsError(try sender.confirmDelivery(WatchServerReceipt(capture: capture)))
        try sender.markOrigin(capture)
        XCTAssertEqual(try sender.pendingServerReceipts().count, 1)
        try phone.save(capture)
        XCTAssertTrue(try sender.pendingServerReceipts().isEmpty)
        XCTAssertThrowsError(try sender.confirmDelivery(WatchServerReceipt(capture: capture)))
    }

    func testInterruptedRemovalCanFinishAndActiveRecordingIsProtected() throws {
        let receiver = WatchReceipts(store: watch)
        let receipt = try WatchServerReceipt(capture: capture)
        var active = capture!
        active.state = .recording
        try watch.save(active)
        XCTAssertThrowsError(try receiver.acceptServerReceipt(receipt))
        try watch.save(capture)
        try receiver.acceptServerReceipt(receipt)
        try FileManager.default.removeItem(at: watch.audioURL(capture))
        try receiver.removeWatchCopy(capture.id)
        XCTAssertTrue(try watch.list().isEmpty)
        try receiver.acceptServerReceipt(receipt)
    }
}
