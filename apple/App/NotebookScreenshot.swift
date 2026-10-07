import AppIntents
import UIKit
import LunaschalCore

/// What part of a full-screen screenshot belongs to the other app. An app
/// cannot capture another app's pixels, so the screenshot comes from the
/// Shortcuts "Take Screenshot" action and this cuts our own window out of it.
enum NotebookCrop {
    /// The largest strip of `screen` outside `window` (both in points, in the
    /// screen's coordinate space), or nil when our window covers the screen.
    /// In Split View that is simply the other half; with a Stage Manager
    /// window in the middle it is whichever side is bigger.
    static func otherRegion(screen: CGRect, window: CGRect) -> CGRect? {
        let ours = window.intersection(screen)
        guard !ours.isNull else { return screen }
        let strips = [
            CGRect(x: screen.minX, y: screen.minY, width: ours.minX - screen.minX, height: screen.height),
            CGRect(x: ours.maxX, y: screen.minY, width: screen.maxX - ours.maxX, height: screen.height),
            CGRect(x: screen.minX, y: screen.minY, width: screen.width, height: ours.minY - screen.minY),
            CGRect(x: screen.minX, y: ours.maxY, width: screen.width, height: screen.maxY - ours.maxY),
        ]
        // A sliver (a divider, a window inset by a few points) is not an app.
        let minimum = min(screen.width, screen.height) * 0.15
        let best = strips.filter { $0.width >= minimum && $0.height >= minimum }
            .max { $0.width * $0.height < $1.width * $1.height }
        return best
    }

    /// `region` in the screenshot's pixels. The screenshot is scaled to the
    /// screen as a whole, so it holds even when its size isn't bounds × scale.
    static func pixelRect(_ region: CGRect, screen: CGRect, imageSize: CGSize) -> CGRect {
        let sx = imageSize.width / screen.width, sy = imageSize.height / screen.height
        return CGRect(x: (region.minX - screen.minX) * sx, y: (region.minY - screen.minY) * sy,
                      width: region.width * sx, height: region.height * sy).integral
            .intersection(CGRect(origin: .zero, size: imageSize))
    }

    /// A system screenshot is exactly the screen in pixels; a copied photo
    /// almost never is. Paste crops only what passes this, so a picture copied
    /// from Photos is never cut in half. `native` is the panel's own pixel
    /// size, which is what a screenshot has under Display Zoom ("More Space"),
    /// where points × scale is not.
    static func isScreenshot(_ image: CGImage, screen: CGRect, scale: CGFloat, native: CGSize? = nil) -> Bool {
        let size = CGSize(width: image.width, height: image.height)
        func matches(_ other: CGSize) -> Bool {
            (abs(size.width - other.width) <= 2 && abs(size.height - other.height) <= 2)
                || (abs(size.width - other.height) <= 2 && abs(size.height - other.width) <= 2)
        }
        return matches(CGSize(width: screen.width * scale, height: screen.height * scale))
            || native.map(matches) == true
    }

    /// What Paste did with an image, so the status line can say why a
    /// screenshot went in whole instead of leaving that to be guessed at.
    enum PasteOutcome: Equatable {
        case cropped
        case notScreenshot(width: Int, height: Int)
        case fullScreen
        case sidesSwapped
    }

    /// For Paste: the other app's part of a screenshot, anything else whole.
    /// Cutting is the default for anything screen-sized. The snapshot of our
    /// window can only veto it, and only when the screenshot clearly shows us
    /// on the *other* side (an older one, taken with the apps the other way
    /// round). A snapshot that matches neither side well, which is what
    /// PaperKit's canvas and the floating tool picker tend to produce, never
    /// stops the cut.
    static func cropIfScreenshot(_ image: CGImage, screen: CGRect, window: CGRect, scale: CGFloat,
                                 native: CGSize? = nil, snapshot: CGImage? = nil) -> (image: CGImage, outcome: PasteOutcome) {
        guard isScreenshot(image, screen: screen, scale: scale, native: native) else {
            return (image, .notScreenshot(width: image.width, height: image.height))
        }
        if let snapshot, sidesSwapped(image, snapshot: snapshot, screen: screen, window: window) {
            return (image, .sidesSwapped)
        }
        let cropped = crop(image, screen: screen, window: window)
        return (cropped, cropped.width == image.width && cropped.height == image.height ? .fullScreen : .cropped)
    }

    /// Whether `screenshot` shows our window on the far side from where it is
    /// now. Our snapshot is compared with where we are and with the mirrored
    /// part; only a confident match on the mirrored side counts. Two blank
    /// halves, or a snapshot that resembles neither, is not a swap.
    static func sidesSwapped(_ screenshot: CGImage, snapshot: CGImage, screen: CGRect, window: CGRect) -> Bool {
        let ours = window.intersection(screen)
        guard !ours.isNull, ours.width > 0, ours.height > 0 else { return false }
        let imageSize = CGSize(width: screenshot.width, height: screenshot.height)
        // Side by side mirrors left/right; stacked mirrors top/bottom.
        let mirrored = ours.height >= screen.height - 1
            ? CGRect(x: screen.minX + screen.maxX - ours.maxX, y: ours.minY, width: ours.width, height: ours.height)
            : CGRect(x: ours.minX, y: screen.minY + screen.maxY - ours.maxY, width: ours.width, height: ours.height)
        guard mirrored.intersection(ours).width <= ours.width * 0.5 else { return false }
        let width = 48, height = max(8, Int((48 * ours.height / ours.width).rounded()))
        guard let reference = grayscale(snapshot, crop: nil, width: width, height: height),
              let here = grayscale(screenshot, crop: pixelRect(ours, screen: screen, imageSize: imageSize),
                                   width: width, height: height),
              let there = grayscale(screenshot, crop: pixelRect(mirrored, screen: screen, imageSize: imageSize),
                                    width: width, height: height) else { return false }
        let near = difference(reference, here, width: width), far = difference(reference, there, width: width)
        return far < 0.25 && far < 0.6 * near
    }

    /// `image` (or a rect of it, in pixels) as a small grey thumbnail, 0...1.
    static func grayscale(_ image: CGImage, crop: CGRect?, width: Int, height: Int) -> [Float]? {
        let source = crop.flatMap { image.cropping(to: $0) } ?? image
        var pixels = [UInt8](repeating: 0, count: width * height)
        guard let context = CGContext(data: &pixels, width: width, height: height, bitsPerComponent: 8,
                                      bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
        context.interpolationQuality = .medium
        context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
        return pixels.map { Float($0) / 255 }
    }

    /// Mean difference in brightness and in edges. Edges are what tell a blank
    /// notebook page from a white web page full of text.
    static func difference(_ a: [Float], _ b: [Float], width: Int) -> Float {
        guard a.count == b.count, !a.isEmpty else { return 1 }
        var total: Float = 0
        for index in a.indices {
            total += abs(a[index] - b[index])
            if index % width + 1 < width {
                total += abs((a[index + 1] - a[index]) - (b[index + 1] - b[index]))
            }
            if index + width < a.count {
                total += abs((a[index + width] - a[index]) - (b[index + width] - b[index]))
            }
        }
        return total / Float(a.count)
    }

    /// The other app's part of `image`, or the whole image when that can't be
    /// told (our window full-screen, or the screenshot's shape doesn't match
    /// the screen, as after a rotation mid-shortcut).
    static func crop(_ image: CGImage, screen: CGRect, window: CGRect) -> CGImage {
        let size = CGSize(width: image.width, height: image.height)
        let screenPortrait = screen.height >= screen.width, imagePortrait = size.height >= size.width
        guard screenPortrait == imagePortrait, let region = otherRegion(screen: screen, window: window) else { return image }
        return image.cropping(to: pixelRect(region, screen: screen, imageSize: size)) ?? image
    }
}

/// Where a screenshot goes: into the notebook on screen, or into the store's
/// inbox for the next one opened.
@MainActor
final class NotebookSession {
    static let shared = NotebookSession()
    var store: NotebookStore?
    /// The editor on screen; it takes a delivered screenshot onto its current page.
    weak var editor: NotebookScreenshotReceiver?

    /// Our window and screen right now, in screen points. Read at delivery
    /// rather than tracked, since Split View can swap sides without resizing.
    func geometry() -> (screen: CGRect, window: CGRect, scale: CGFloat, native: CGSize)? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first,
              let window = scene.keyWindow ?? scene.windows.first else { return nil }
        let screen = scene.screen
        return (screen.bounds, window.convert(window.bounds, to: screen.coordinateSpace), screen.scale, screen.nativeBounds.size)
    }

    /// Our window as it looks now, small: enough to recognise it in a screenshot.
    func snapshot() -> CGImage? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first,
              let window = scene.keyWindow ?? scene.windows.first, window.bounds.width > 0 else { return nil }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 0.25
        return UIGraphicsImageRenderer(bounds: window.bounds, format: format).image { _ in
            window.drawHierarchy(in: window.bounds, afterScreenUpdates: false)
        }.cgImage
    }

    /// Returns where the image went, for the Shortcut's dialog.
    func deliver(_ data: Data) throws -> String {
        guard let image = UIImage(data: data)?.cgImage else { throw CaptureError.missingFile }
        let cropped = geometry().map { NotebookCrop.crop(image, screen: $0.screen, window: $0.window) } ?? image
        if let editor { return editor.receiveScreenshot(cropped) }
        guard let store, let png = UIImage(cgImage: cropped).pngData() else { throw CaptureError.missingFile }
        try store.enqueueScreenshot(png)
        return "Saved for the next notebook you open."
    }
}

@MainActor
protocol NotebookScreenshotReceiver: AnyObject {
    func receiveScreenshot(_ image: CGImage) -> String
}

/// The Shortcuts action: *Take Screenshot → Add Screenshot to Lunaschal Notes*.
/// It runs in the app's own process, so it can see where our window is.
struct AddScreenshotToNotesIntent: AppIntent {
    static let title: LocalizedStringResource = "Add Screenshot to Lunaschal Notes"
    static let description = IntentDescription(
        "Crops Lunaschal's half out of a screenshot and pastes the other app's half onto the open notebook page.")
    static let openAppWhenRun = false

    @Parameter(title: "Screenshot", supportedContentTypes: [.image])
    var screenshot: IntentFile

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let placed = try NotebookSession.shared.deliver(screenshot.data)
        return .result(dialog: IntentDialog(stringLiteral: placed))
    }
}
