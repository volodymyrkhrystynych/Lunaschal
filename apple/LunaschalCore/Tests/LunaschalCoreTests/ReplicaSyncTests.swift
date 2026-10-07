import XCTest
@testable import LunaschalCore

private actor JournalServer: ReplicaTransport {
    var pages: [SyncPage]
    var sent: [ReplicaOperation] = []

    init(_ pages: [SyncPage]) { self.pages = pages }

    func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage {
        guard !pages.isEmpty else { throw ReplicaError.invalidPage }
        return pages.removeFirst()
    }

    func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply {
        sent.append(operation)
        let data = (operation.data).merging(["id": .string(operation.recordId)]) { $1 }
        let change = SyncChange(revision: operation.baseRevision + 10, collection: operation.collection,
                                id: operation.recordId, deleted: false, data: data)
        return OperationReply(operationId: operation.id, change: change, conflict: nil, current: nil,
                              error: nil, resetRequired: nil)
    }
}

/// The sync worker runs off the UI actor on its own connection to the same
/// file; what it applies and sends is what the UI's connection then sees.
final class ReplicaSyncTests: XCTestCase {
    private let epoch = ULID.make()
    private let collections = ["journal_entries"]

    private func entry(_ id: String, _ content: String, revision: Int64) -> SyncChange {
        SyncChange(revision: revision, collection: "journal_entries", id: id, deleted: false,
                   data: ["id": .string(id), "content": .string(content)])
    }

    private func page(_ changes: [SyncChange], cursor: String, more: Bool) throws -> SyncPage {
        let json: [String: Any] = ["protocolVersion": 1, "epoch": epoch, "mode": "bootstrap", "changes": [],
                                   "hasMore": more, "cursor": cursor, "collections": collections]
        var page = try JSONSerialization.jsonObject(with: try JSONSerialization.data(withJSONObject: json)) as! [String: Any]
        page["changes"] = try changes.map { try JSONSerialization.jsonObject(with: JSONEncoder().encode($0)) }
        return try JSONDecoder().decode(SyncPage.self, from: JSONSerialization.data(withJSONObject: page))
    }

    func testPagesAreAppliedAndEditsSentOnTheWorkersOwnConnection() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let url = root.appendingPathComponent("replica.sqlite")
        let ui = try ReplicaStore(url: url)
        let first = ULID.make(), second = ULID.make()
        let server = JournalServer([
            try page([entry(first, "One", revision: 1)], cursor: "a", more: true),
            try page([entry(second, "Two", revision: 2)], cursor: "b", more: false),
        ])
        let worker = ReplicaSync(url: url)
        try await worker.run(using: server, collections: collections)
        XCTAssertEqual(try ui.record(collection: "journal_entries", id: first)?.data?["content"]?.string, "One")
        XCTAssertEqual(try ui.record(collection: "journal_entries", id: second)?.data?["content"]?.string, "Two")
        XCTAssertEqual(try ui.cursor(collections: collections), "b")

        // An edit made on the UI's connection is sent and acknowledged by the worker.
        let saved = try XCTUnwrap(try ui.record(collection: "journal_entries", id: first))
        _ = try ui.queue(record: saved, data: ["content": .string("Edited")])
        await server.setPages([try page([], cursor: "c", more: false)])
        try await worker.run(using: server, collections: collections)
        let sent = await server.sent
        XCTAssertEqual(sent.map(\.recordId), [first])
        XCTAssertTrue(try ui.edits().isEmpty)
        XCTAssertEqual(try ui.record(collection: "journal_entries", id: first)?.data?["content"]?.string, "Edited")
    }
}

private extension JournalServer {
    func setPages(_ pages: [SyncPage]) { self.pages = pages }
}
