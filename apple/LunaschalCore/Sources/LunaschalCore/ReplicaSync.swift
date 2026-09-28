import Foundation

public protocol ReplicaTransport {
    func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage
    func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply
}

@MainActor
public final class ReplicaSync {
    private let store: ReplicaStore
    private var running = false

    public init(store: ReplicaStore) { self.store = store }

    public func run(using transport: ReplicaTransport, collections: [String], sendEdits: Bool = true) async throws {
        guard !running else { return }
        running = true
        defer { running = false }
        try await download(using: transport, collections: collections)
        if sendEdits {
            for edit in try store.edits() where edit.state == "pending" {
                try Task.checkCancellation()
                let response = try await transport.applyOperation(edit.operation)
                if response.resetRequired == true {
                    try store.resetCursors()
                    throw ReplicaError.needsBootstrap
                } else if response.conflict == true {
                    try store.hold(edit.operation, reply: response)
                } else if response.change != nil {
                    try store.acknowledge(edit.operation, reply: response)
                } else {
                    try store.hold(edit.operation, reply: response, state: "failed")
                }
            }
        }
    }

    private func download(using transport: ReplicaTransport, collections: [String]) async throws {
        var resetOnce = false
        while true {
            try Task.checkCancellation()
            let cursor = try store.cursor(collections: collections)
            do {
                let page = try await transport.syncPage(cursor: cursor, collections: collections)
                guard Set(page.collections) == Set(collections) else { throw ReplicaError.invalidPage }
                try store.apply(page, startingBootstrap: cursor == nil)
                if !page.hasMore { return }
            } catch let failure as HTTPFailure where failure.status == 410 && !resetOnce {
                resetOnce = true
                try store.resetCursors()
            }
        }
    }
}
