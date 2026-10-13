import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// A picture the server downloaded into a fic (`backend/fanfic/storage.py`'s
/// `images/` folder). The stored chapter HTML points at it as
/// `/api/fanfic/<ficID>/images/<name>`, which means nothing without a server,
/// so the phone keeps its own copy beside the replica.
public struct FicImageReference: Hashable, Sendable {
    public let ficID: String
    public let name: String
}

public enum FicImages {
    /// The server's `safe_image_path` accepts the same names.
    private static let pattern = try! NSRegularExpression(
        pattern: "/api/fanfic/([0-9A-Za-z]{26})/images/([A-Za-z0-9._-]+)")

    /// The server copy a chapter's `src` points at; nil for anything else.
    public static func reference(_ src: String) -> FicImageReference? {
        let path = src.hasPrefix("api/") ? "/" + src : src
        guard path.hasPrefix("/api/fanfic/"),
              let match = pattern.firstMatch(in: path, range: NSRange(path.startIndex..., in: path)),
              match.range.location == 0, match.range.length == (path as NSString).length else { return nil }
        return validated(path, match)
    }

    /// Every server copy one chapter's HTML points at, once each, in order.
    public static func references(html: String) -> [FicImageReference] {
        var seen = Set<FicImageReference>()
        return pattern.matches(in: html, range: NSRange(html.startIndex..., in: html)).compactMap {
            guard let found = validated(html, $0), seen.insert(found).inserted else { return nil }
            return found
        }
    }

    private static func validated(_ text: String, _ match: NSTextCheckingResult) -> FicImageReference? {
        guard let id = Range(match.range(at: 1), in: text), let name = Range(match.range(at: 2), in: text) else { return nil }
        let ficID = String(text[id]).uppercased(), file = String(text[name])
        guard ULID.isValid(ficID), file != ".", file != ".." else { return nil }
        return FicImageReference(ficID: ficID, name: file)
    }
}

public protocol FicImageTransport {
    func ficImage(_ image: FicImageReference) async throws -> Data
}

extension JournalAPI: FicImageTransport {
    public func ficImage(_ image: FicImageReference) async throws -> Data {
        var req = request("api/fanfic/\(image.ficID)/images/\(image.name)")
        req.setValue("image/*", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: req)
        try check(data, response)
        return data
    }
}

/// The phone's copies of fic pictures: `<root>/<ficID>/<name>`. Kept apart
/// from `MediaStore`, whose files the server describes and verifies by hash;
/// these come with nothing but their name.
public final class FicImageStore: @unchecked Sendable {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    private func path(_ image: FicImageReference) -> URL {
        root.appendingPathComponent(image.ficID, isDirectory: true).appendingPathComponent(image.name)
    }

    /// The copy on this device, if there is one.
    public func file(_ image: FicImageReference) -> URL? {
        let url = path(image)
        return fm.fileExists(atPath: url.path) ? url : nil
    }

    @discardableResult
    public func save(_ data: Data, for image: FicImageReference) throws -> URL {
        guard !data.isEmpty else { throw MediaError.invalidRange }
        let url = path(image)
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        return url
    }

    /// Fetches what these chapters point at and this device lacks. A picture
    /// that fails is left out (it is fetched again when its chapter is read),
    /// never the reason a fic's download fails; cancelling still stops it.
    @discardableResult
    public func fetchMissing(in html: [String], using transport: FicImageTransport) async throws -> Int {
        var saved = 0
        for image in html.flatMap(FicImages.references(html:)) where file(image) == nil {
            try Task.checkCancellation()
            do {
                try save(try await transport.ficImage(image), for: image)
                saved += 1
            } catch {
                if error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
            }
        }
        return saved
    }

    public func usedBytes() -> Int64 {
        guard let files = fm.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in files {
            total += Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
        return total
    }

    public func removeAll() throws {
        for item in try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) {
            try fm.removeItem(at: item)
        }
    }
}
