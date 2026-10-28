import AVFoundation
import CoreTransferable
import ImageIO
import Photos
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Getting a photo, a Live Photo or a video out of the system pickers without
/// losing what it is: its original bytes, its paired clip, and when it was
/// taken. Each of these used to be lost somewhere — the camera's photo was
/// re-encoded without its EXIF, a Live Photo arrived as its still, and a video
/// could not be picked at all.
enum MediaImport {
    /// A picked video, as the file the picker wrote. The picker's copy is
    /// temporary, so it is moved somewhere of our own before the closure returns.
    struct Movie: Transferable {
        let url: URL

        static var transferRepresentation: some TransferRepresentation {
            FileRepresentation(contentType: .movie) { SentTransferredFile($0.url) } importing: { received in
                let copy = FileManager.default.temporaryDirectory
                    .appendingPathComponent(UUID().uuidString)
                    .appendingPathExtension(received.file.pathExtension.isEmpty ? "mov" : received.file.pathExtension)
                try FileManager.default.copyItem(at: received.file, to: copy)
                return Movie(url: copy)
            }
        }
    }

    /// A Live Photo's two halves written to temporary files: its still, as the
    /// camera stored it (HEIC, EXIF and all), and its clip.
    struct LivePhotoFiles {
        let still: Data
        let stillType: String
        let motion: URL
        let motionType: String
    }

    /// The resources behind a Live Photo the picker handed over. This reads the
    /// picked object itself, so it needs no photo-library permission.
    static func export(_ live: PHLivePhoto) async throws -> LivePhotoFiles? {
        let resources = PHAssetResource.assetResources(for: live)
        guard let photo = resources.first(where: { $0.type == .photo || $0.type == .fullSizePhoto }),
              let video = resources.first(where: { $0.type == .pairedVideo || $0.type == .fullSizePairedVideo })
        else { return nil }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let stillURL = folder.appendingPathComponent("still")
        let motionURL = folder.appendingPathComponent("motion.mov")
        let manager = PHAssetResourceManager.default()
        let options = PHAssetResourceRequestOptions()
        options.isNetworkAccessAllowed = true
        try await manager.writeData(for: photo, toFile: stillURL, options: options)
        try await manager.writeData(for: video, toFile: motionURL, options: options)
        let still = try Data(contentsOf: stillURL)
        try? FileManager.default.removeItem(at: stillURL)
        return LivePhotoFiles(
            still: still,
            stillType: UTType(photo.uniformTypeIdentifier)?.preferredMIMEType ?? "image/heic",
            motion: motionURL,
            motionType: UTType(video.uniformTypeIdentifier)?.preferredMIMEType ?? "video/quicktime")
    }

    /// When a movie was shot, from its own metadata. Apple's key carries the
    /// local offset; the common key is the fallback.
    static func creationDate(of url: URL, mime: String?) async -> Date? {
        var options: [String: Any] = [:]
        if let mime { options[AVURLAssetOverrideMIMETypeKey] = mime }
        let asset = AVURLAsset(url: url, options: options)
        if let item = try? await asset.load(.creationDate), let date = try? await item.load(.dateValue) {
            return date
        }
        guard let metadata = try? await asset.load(.metadata) else { return nil }
        for item in AVMetadataItem.metadataItems(from: metadata, filteredByIdentifier: .quickTimeMetadataCreationDate) {
            if let date = try? await item.load(.dateValue) { return date }
        }
        return nil
    }

    /// A frame of a staged video, for its row in the draft.
    static func poster(of url: URL, mime: String?, size: CGFloat) async -> UIImage? {
        var options: [String: Any] = [:]
        if let mime { options[AVURLAssetOverrideMIMETypeKey] = mime }
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url, options: options))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: size, height: size)
        guard let image = try? await generator.image(at: CMTime(seconds: 0.5, preferredTimescale: 600)).image
        else { return nil }
        return UIImage(cgImage: image)
    }

    /// The camera's photo as a JPEG that keeps the metadata the camera wrote
    /// (its capture time above all). `jpegData` writes none of it.
    static func cameraJPEG(_ image: UIImage, metadata: [String: Any]?) -> Data? {
        guard let metadata, let cgImage = image.cgImage else { return image.jpegData(compressionQuality: 0.9) }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil)
        else { return image.jpegData(compressionQuality: 0.9) }
        var properties = metadata
        properties[kCGImageDestinationLossyCompressionQuality as String] = 0.9
        // The pixels are the sensor's; the camera's own orientation tag says
        // which way up they go, so it is kept rather than baked in.
        CGImageDestinationAddImage(destination, cgImage, properties as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return image.jpegData(compressionQuality: 0.9) }
        return data as Data
    }
}
