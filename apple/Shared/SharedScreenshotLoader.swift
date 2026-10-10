import Foundation
import UniformTypeIdentifiers
import LunaschalCore

enum SharedScreenshotLoader {
    static func save(_ provider: NSItemProvider, to outbox: ScreenshotOutbox) async throws {
        guard let identifier = provider.registeredTypeIdentifiers.first(where: {
            UTType($0)?.conforms(to: .image) == true
        }), let type = UTType(identifier) else { throw CocoaError(.fileReadUnknown) }
        let capturedAt = Date()
        let timeZone = TimeZone.current
        let filename = "screenshot.\(type.preferredFilenameExtension ?? "png")"
        let contentType = type.preferredMIMEType ?? "image/png"
        // Item-provider URLs disappear when the callback returns. Persist the
        // original bytes inside it, before resuming the async caller.
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            provider.loadFileRepresentation(forTypeIdentifier: identifier) { url, error in
                do {
                    if let error { throw error }
                    guard let url else { throw CocoaError(.fileReadUnknown) }
                    try outbox.append(file: url, filename: filename, contentType: contentType,
                                      now: capturedAt, timeZone: timeZone)
                    continuation.resume()
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
}
