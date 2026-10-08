import Foundation

public protocol ReplicaTransport {
    func syncPage(cursor: String?, collections: [String]) async throws -> SyncPage
    /// A page of at most `limit` records. Each page is applied in one write
    /// transaction, so the library download asks for small ones: anything
    /// the UI writes meanwhile waits for the transaction in progress.
    func syncPage(cursor: String?, collections: [String], limit: Int) async throws -> SyncPage
    func applyOperation(_ operation: ReplicaOperation) async throws -> OperationReply
    /// Whether each cursor's scope has anything new, in one request; nil when
    /// the server can't say, and every scope is then simply pulled.
    func syncStatus(cursors: [String]) async throws -> [ScopeStatus]?
}

extension ReplicaTransport {
    public func syncPage(cursor: String?, collections: [String], limit: Int) async throws -> SyncPage {
        try await syncPage(cursor: cursor, collections: collections)
    }

    public func syncStatus(cursors: [String]) async throws -> [ScopeStatus]? { nil }
}

/// The server's answer for one scope: something to fetch, or a cursor it no
/// longer accepts (the scope then bootstraps again).
public struct ScopeStatus: Codable, Equatable, Sendable {
    public let changed: Bool
    public let resetRequired: Bool

    public init(changed: Bool, resetRequired: Bool) {
        self.changed = changed
        self.resetRequired = resetRequired
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

    /// The replica's one-time upgrade work (`ReplicaStore.maintain`) on this
    /// worker's connection, off the UI thread and after launch. A no-op once done.
    public func maintain() throws {
        try store.maintain { !Task.isCancelled }
    }

    /// Which of these scopes have anything to pull, asked in one request. A
    /// scope with no cursor yet always does (it bootstraps); so does every
    /// scope when the server can't answer.
    public func scopesToPull(using transport: ReplicaTransport, scopes: [[String]]) async throws -> [Bool] {
        let store = try store
        let cursors = try scopes.map { try store.cursor(collections: $0) }
        let asked = cursors.compactMap { $0 }
        guard !asked.isEmpty, let answers = try await transport.syncStatus(cursors: asked),
              answers.count == asked.count else { return scopes.map { _ in true } }
        var next = answers.makeIterator()
        return cursors.map { cursor in
            guard cursor != nil, let answer = next.next() else { return true }
            return answer.changed || answer.resetRequired
        }
    }

    /// Pulls a scope and sends the outbox; returns the collections whose
    /// records changed here, so only the screens showing them reload.
    @discardableResult
    public func run(using transport: ReplicaTransport, collections: [String], sendEdits: Bool = true) async throws -> Set<String> {
        guard !running else { return [] }
        running = true
        defer { running = false }
        let store = try store
        var changed = try await download(using: transport, collections: collections, into: store)
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
                changed.insert(edit.operation.collection)
            }
        }
        return changed
    }

    private func download(using transport: ReplicaTransport, collections: [String], into store: ReplicaStore) async throws -> Set<String> {
        var resetOnce = false
        var changed: Set<String> = []
        while true {
            try Task.checkCancellation()
            let cursor = try store.cursor(collections: collections)
            do {
                let page = try await transport.syncPage(cursor: cursor, collections: collections)
                guard Set(page.collections) == Set(collections) else { throw ReplicaError.invalidPage }
                changed.formUnion(try store.apply(page, startingBootstrap: cursor == nil))
                if !page.hasMore { return changed }
            } catch let failure as HTTPFailure where failure.status == 410 && !resetOnce {
                resetOnce = true
                // This scope only: the others' cursors are still good, and the
                // library's can only be rebuilt over Wi-Fi.
                try store.resetCursor(collections: collections)
            }
        }
    }
}
