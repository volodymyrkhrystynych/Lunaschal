import XCTest
@testable import LunaschalCore

final class DrawingPublicationTests: XCTestCase {
    private let server = URL(string: "https://example.test")!
    private func setup() throws -> (DrawingStore, DrawingPublicationStore) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return (try DrawingStore(root: root.appendingPathComponent("drawings")),
                try DrawingPublicationStore(root: root.appendingPathComponent("outbox"), hash: digest))
    }
    private func digest(_ url: URL) throws -> String { try Data(contentsOf: url).base64EncodedString() }
    private func save(_ drawings: DrawingStore, _ page: DrawingPage, _ value: UInt8) throws -> DrawingPage {
        try drawings.checkpoint(page.id, native: Data([value]), preview: Data([value + 1]))
    }
    private func reply(_ publication: DrawingPublication, revision: Int64 = 23) -> DrawingReply {
        DrawingReply(operationId: publication.operation.id, changes: [SyncChange(revision: revision,
            collection: "paper_native_ink", id: publication.operation.pageId, deleted: false,
            data: ["sha256": .string(publication.inkHash), "previewSha256": .string(publication.previewHash)])],
            error: nil, conflict: nil, resetRequired: nil)
    }

    func testExplicitSaveSurvivesCheckpointPruningAndRelaunch() throws {
        let (drawings, outbox) = try setup()
        var page = try save(drawings, drawings.create(), 1)
        XCTAssertTrue(try outbox.all().isEmpty)
        let queued = try outbox.queue(page, drawings: drawings, server: server, epoch: ULID.make())
        for value: UInt8 in 2...8 { page = try save(drawings, page, value) }
        let reopened = try DrawingPublicationStore(root: outbox.root, hash: digest)
        let retained = try XCTUnwrap(reopened.publication(page.id))
        XCTAssertEqual(retained.operation, queued.operation)
        XCTAssertEqual(try Data(contentsOf: reopened.payloads(retained).ink), Data([1]))
        XCTAssertThrowsError(try reopened.queue(page, drawings: drawings, server: server, epoch: queued.operation.epoch))
        try reopened.receive(reply(queued), for: retained)
        let next = try reopened.queue(page, drawings: drawings, server: server, epoch: queued.operation.epoch)
        XCTAssertEqual(next.operation.baseRevision, 23)
        XCTAssertEqual(next.operation.paperId, queued.operation.paperId)
        XCTAssertNotEqual(next.operation.id, queued.operation.id)
        XCTAssertEqual(try Data(contentsOf: reopened.payloads(next).ink), Data([8]))
        XCTAssertThrowsError(try reopened.receive(reply(queued), for: queued))
    }

    func testConflictAndEpochChangeNeverAdoptANewerRevision() throws {
        let (drawings, outbox) = try setup()
        let page = try save(drawings, drawings.create(), 1)
        let queued = try outbox.queue(page, drawings: drawings, server: server, epoch: ULID.make())
        try outbox.receive(DrawingReply(operationId: nil, changes: nil, error: "Other device changed it", conflict: true, resetRequired: nil), for: queued)
        XCTAssertEqual(try outbox.publication(page.id)?.state, "conflict")
        XCTAssertThrowsError(try outbox.queue(page, drawings: drawings, server: server, epoch: queued.operation.epoch))
        XCTAssertThrowsError(try outbox.queue(page, drawings: drawings, server: server, epoch: ULID.make()))
        XCTAssertEqual(try Data(contentsOf: outbox.payloads(queued).ink), Data([1]))
    }

    func testMismatchedReceiptCorruptPayloadAndDifferentServerAreRejected() throws {
        let (drawings, outbox) = try setup()
        let page = try save(drawings, drawings.create(), 1)
        let queued = try outbox.queue(page, drawings: drawings, server: server, epoch: ULID.make())
        let bad = DrawingReply(operationId: ULID.make(), changes: reply(queued).changes, error: nil, conflict: nil, resetRequired: nil)
        XCTAssertThrowsError(try outbox.receive(bad, for: queued))
        XCTAssertEqual(try outbox.publication(page.id)?.state, "pending")
        XCTAssertThrowsError(try outbox.queue(page, drawings: drawings, server: URL(string: "https://elsewhere.test")!, epoch: queued.operation.epoch))
        let file = try outbox.payloads(queued).ink
        try Data([42]).write(to: file)
        XCTAssertThrowsError(try outbox.payloads(queued))
        XCTAssertEqual(try Data(contentsOf: XCTUnwrap(drawings.nativeURL(page))), Data([1]))
    }

    func testImportedRemoteIdentityAndRevisionAreRetained() throws {
        let (drawings, outbox) = try setup()
        let page = try drawings.importServerDrawing(id: ULID.make(), title: "Remote", native: Data([1]), preview: Data([2]))
        let epoch = ULID.make(), paperID = ULID.make()
        let native = SyncChange(revision: 78, collection: "paper_native_ink", id: page.id, deleted: false,
            data: ["sha256": .string(Data([1]).base64EncodedString()), "previewSha256": .string(Data([2]).base64EncodedString())])
        try outbox.adopt(page, paperID: paperID, native: native, drawings: drawings, server: server, epoch: epoch)
        XCTAssertEqual(try outbox.publication(page.id)?.state, "synced")
        XCTAssertThrowsError(try drawings.importServerDrawing(id: page.id, title: "Overwrite", native: Data([8]), preview: Data([9])))
        let edited = try save(drawings, page, 3)
        let queued = try outbox.queue(edited, drawings: drawings, server: server, epoch: epoch)
        XCTAssertEqual(queued.operation.pageId, page.id)
        XCTAssertEqual(queued.operation.paperId, paperID)
        XCTAssertEqual(queued.operation.baseRevision, 78)
    }

    func testMultipartKeepsMetadataAndOriginalBytes() throws {
        let (drawings, outbox) = try setup()
        let page = try save(drawings, drawings.create(title: "A quoted \"title\""), 65)
        let queued = try outbox.queue(page, drawings: drawings, server: server, epoch: ULID.make())
        let files = try outbox.payloads(queued)
        let body = try DrawingMultipart(operation: queued.operation, ink: files.ink, preview: files.preview)
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = try String(contentsOf: body.url, encoding: .utf8)
        XCTAssertTrue(text.contains("name=\"metadata\""))
        XCTAssertTrue(text.contains(queued.operation.id))
        XCTAssertTrue(text.contains("\r\n\r\nA\r\n"))
        XCTAssertTrue(text.contains("\r\n\r\nB\r\n"))
    }
}
