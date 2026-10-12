import Foundation
import UIKit
import UniformTypeIdentifiers
import LunaschalCore

enum SharedScreenshotLoader {
    static func save(_ provider: NSItemProvider, to outbox: ScreenshotOutbox) async throws {
        let identifiers = provider.registeredTypeIdentifiers.filter {
            UTType($0)?.conforms(to: .image) == true
        }
        let capturedAt = Date()
        let timeZone = TimeZone.current
        var failure: Error = CocoaError(.fileReadUnknown)
        // An advertised representation is not necessarily loadable. Keep
        // trying the other image formats before giving up on the screenshot.
        for identifier in identifiers {
            guard let type = UTType(identifier) else { continue }
            try Task.checkCancellation()
            do {
                try await saveFile(provider, identifier: identifier, type: type,
                                   to: outbox, capturedAt: capturedAt, timeZone: timeZone)
                return
            } catch { failure = error }
            do {
                let data: Data = try await withCheckedThrowingContinuation { continuation in
                    provider.loadDataRepresentation(forTypeIdentifier: identifier) { data, error in
                        if let data { continuation.resume(returning: data) }
                        else { continuation.resume(throwing: error ?? CocoaError(.fileReadUnknown)) }
                    }
                }
                try saveData(data, type: type, to: outbox, capturedAt: capturedAt, timeZone: timeZone)
                return
            } catch { failure = error }
        }
        // Some share sources provide only a UIKit image object, not a file.
        try Task.checkCancellation()
        if provider.canLoadObject(ofClass: UIImage.self) {
            let image: UIImage = try await withCheckedThrowingContinuation { continuation in
                provider.loadObject(ofClass: UIImage.self) { object, error in
                    if let image = object as? UIImage { continuation.resume(returning: image) }
                    else { continuation.resume(throwing: error ?? CocoaError(.fileReadUnknown)) }
                }
            }
            guard let data = image.pngData() else { throw CocoaError(.fileReadUnknown) }
            try saveData(data, type: .png, to: outbox, capturedAt: capturedAt, timeZone: timeZone)
            return
        }
        throw failure
    }

    private static func saveData(_ data: Data, type: UTType, to outbox: ScreenshotOutbox,
                                 capturedAt: Date, timeZone: TimeZone) throws {
        guard !data.isEmpty else { throw CocoaError(.fileReadCorruptFile) }
        let temporary = outbox.root.appendingPathComponent(".image-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: temporary) }
        try data.write(to: temporary, options: .atomic)
        try outbox.append(file: temporary, filename: "screenshot.\(type.preferredFilenameExtension ?? "png")",
                          contentType: type.preferredMIMEType ?? "image/png", now: capturedAt, timeZone: timeZone)
    }

    private static func saveFile(_ provider: NSItemProvider, identifier: String, type: UTType,
                                 to outbox: ScreenshotOutbox, capturedAt: Date, timeZone: TimeZone) async throws {
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
