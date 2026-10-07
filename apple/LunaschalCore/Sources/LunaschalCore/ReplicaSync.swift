import Foundation

public protocol ReplicaTransport {
    func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage
    /// A page of at most `limit` records. Each page is applied in one write
    /// transaction, so the library download asks for small ones: anything
    /// the UI writes meanwhile waits for the transaction in progress.
    func syncPage(cursor: String?, collections: [String], limit: Int) async throws -> SyncPage
    func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply
}

extension ReplicaTransport {
    public func syncPage(cursor: String?, collections: [String], limit: Int) async throws -> SyncPage {
        try await syncPage(cursor: cursor, collections: collections)
    }
}

/// Applies the server's pages and sends the outbox, off the UI actor and on a
/// database connection of its own, like `LibraryDownload`. It used to run on
/// the main actor over the UI's connection, so every page's inserts and search
/// indexing ran on the UI thread, and any write the UI made meanwhile waited
/// for the busy timeout behind the library download's transactions: the app
/// froze while it synced. The UI keeps its own connection for reads (which in
/// WAL mode never wait) and the small writes a tap makes.
public actor ReplicaSync {
    private let url: URL
    private var opened: ReplicaStore?
    private var running = false

    public init(url: URL) { self.url = url }

    private var store: ReplicaStore {
        get throws {
            if let opened { return opened }
            let store = try ReplicaStore(url: url)
            opened = store
            return store
        }
    }

    public func run(using transport: ReplicaTransport, collections: [String], sendEdits: Bool = true) async throws {
        guard !running else { return }
        running = true
        defer { running = false }
        let store = try store
        try await download(using: transport, collections: collections, into: store)
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

    private func download(using transport: ReplicaTransport, collections: [String], into store: ReplicaStore) async throws {
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
