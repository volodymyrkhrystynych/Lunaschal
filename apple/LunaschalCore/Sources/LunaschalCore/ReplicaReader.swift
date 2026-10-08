import Foundation

/// Reads the replica off the UI thread, on a connection of its own. Screens
/// used to query and decode on the main thread through the UI's connection,
/// so every refresh after a sync was a stutter; in WAL mode a reader never
/// waits for a writer, so this one never blocks on the sync workers either.
public actor ReplicaReader {
    private let url: URL
    private var opened: ReplicaStore?

    public init(url: URL) { self.url = url }

    public func read<T>(_ body: (ReplicaStore) throws -> T) throws -> T {
        if opened == nil { opened = try ReplicaStore(url: url) }
        return try body(opened!)
    }
}
