import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif

public struct MediaDescriptor: Codable, Identifiable, Sendable {
    public let collection: String
    public let id: String
    public let available: Bool
    public let size: Int64?
    public let sha256: String?
    public let mime: String?
    public let url: String?
    public let reason: String?

    public func validate() throws {
        guard Self.collections.contains(collection), ULID.isValid(id) else { throw MediaError.invalidManifest }
        if available {
            guard let size, size >= 0, let sha256, sha256.count == 64,
                  sha256.allSatisfy({ "0123456789abcdef".contains($0) }) else { throw MediaError.invalidManifest }
        }
    }

    public static let collections = ["journal_attachments", "study_sources", "paper_pages", "paper_page_images", "newspaper_frontpages"]
}

public struct MediaPage: Decodable {
    public let items: [MediaDescriptor]
    public let hasMore: Bool
    public let after: String
}

public enum MediaError: LocalizedError {
    case invalidManifest, integrity, invalidRange, budget, unavailableHasher
    public var errorDescription: String? {
        switch self {
        case .invalidManifest: return "The server sent an invalid media manifest."
        case .integrity: return "Downloaded media failed verification. Retry to download a fresh copy."
        case .invalidRange: return "The server could not resume this file. Refresh its manifest and retry."
        case .budget: return "The library download exceeds this device's storage budget or free space."
        case .unavailableHasher: return "Media verification requires CryptoKit on this platform."
        }
    }
}

/// Downloaded copies only. This directory never contains capture originals.
@MainActor
public final class MediaStore {
    public let root: URL
    private let hash: @Sendable (URL) throws -> String
    private let fm = FileManager.default

    public init(root: URL, hash: @escaping @Sendable (URL) throws -> String = { try MediaStore.sha256($0) }) throws {
        self.root = root
        self.hash = hash
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public nonisolated static func sha256(_ url: URL) throws -> String {
        #if canImport(CryptoKit)
        var digest = SHA256()
        let input = try FileHandle(forReadingFrom: url)
        defer { try? input.close() }
        while let chunk = try input.read(upToCount: 1024 * 1024), !chunk.isEmpty { digest.update(data: chunk) }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
        #else
        throw MediaError.unavailableHasher
        #endif
    }

    public func downloaded(collection: String, id: String) throws -> URL? {
        guard MediaDescriptor.collections.contains(collection), ULID.isValid(id) else { throw MediaError.invalidManifest }
        let manifest = root.appendingPathComponent("\(collection)-\(id).json")
        guard fm.fileExists(atPath: manifest.path) else { return nil }
        let item = try JSONDecoder().decode(MediaDescriptor.self, from: Data(contentsOf: manifest))
        try item.validate()
        guard item.available, let digest = item.sha256 else { return nil }
        let file = root.appendingPathComponent(digest)
        return fm.fileExists(atPath: file.path) && fileSize(file) == item.size ? file : nil
    }

    public func offset(for item: MediaDescriptor, budget: Int64) throws -> Int64 {
        try item.validate()
        guard item.available, let size = item.size else { throw MediaError.invalidManifest }
        let part = try path(item, partial: true)
        let present = fileSize(part)
        let remaining = max(0, size - present)
        let free = (try fm.attributesOfFileSystem(forPath: root.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
        guard remaining <= max(0, budget - (try usedBytes())), remaining <= max(0, free - 256 * 1024 * 1024) else {
            throw MediaError.budget
        }
        if present > size { try fm.removeItem(at: part); return 0 }
        return present
    }

    public func append(_ data: Data, to item: MediaDescriptor, offset: Int64) throws {
        try item.validate()
        let part = try path(item, partial: true)
        guard fileSize(part) == offset, let size = item.size, offset + Int64(data.count) <= size else {
            throw MediaError.invalidRange
        }
        if !fm.fileExists(atPath: part.path) { try Data().write(to: part) }
        let output = try FileHandle(forWritingTo: part)
        defer { try? output.close() }
        try output.seekToEnd()
        try output.write(contentsOf: data)
        try output.synchronize()
    }

    public func finish(_ item: MediaDescriptor) async throws {
        try item.validate()
        let part = try path(item, partial: true)
        let verifier = hash
        let digest = try await Task.detached(priority: .utility) { try verifier(part) }.value
        try Task.checkCancellation()
        guard fileSize(part) == item.size, digest == item.sha256 else {
            try? fm.removeItem(at: part)
            throw MediaError.integrity
        }
        let final = try path(item, partial: false)
        if fm.fileExists(atPath: final.path) { try fm.removeItem(at: final) }
        try fm.moveItem(at: part, to: final)
        try remember(item)
    }

    public func reuse(_ item: MediaDescriptor) throws -> Bool {
        try item.validate()
        guard item.available else { return false }
        let file = try path(item, partial: false)
        guard fm.fileExists(atPath: file.path), fileSize(file) == item.size else { return false }
        // Content was verified before the atomic rename. Sharing the same hash
        // between records reuses bytes without conflating their identities.
        try remember(item)
        return true
    }

    public func usedBytes() throws -> Int64 {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.fileSizeKey])
            .reduce(Int64(0)) { $0 + fileSize($1) }
    }

    public func removeDownloadedCopies() throws {
        for file in try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) {
            try fm.removeItem(at: file)
        }
    }

    private func remember(_ item: MediaDescriptor) throws {
        try JSONEncoder().encode(item).write(to: root.appendingPathComponent("\(item.collection)-\(item.id).json"), options: .atomic)
    }
    private func path(_ item: MediaDescriptor, partial: Bool) throws -> URL {
        guard item.available, let digest = item.sha256 else { throw MediaError.invalidManifest }
        return root.appendingPathComponent(digest + (partial ? ".part" : ""))
    }
    private func fileSize(_ url: URL) -> Int64 {
        (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init) ?? 0
    }
}
