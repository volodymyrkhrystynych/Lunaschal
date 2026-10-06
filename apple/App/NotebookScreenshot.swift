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
    /// from Photos is never cut in half.
    static func isScreenshot(_ image: CGImage, screen: CGRect, scale: CGFloat) -> Bool {
        let width = screen.width * scale, height = screen.height * scale
        return abs(CGFloat(image.width) - width) <= 1 && abs(CGFloat(image.height) - height) <= 1
    }

    /// For Paste: the other app's part of a screenshot, anything else whole.
    /// With a snapshot of our window, also check the part being thrown away
    /// really is Lunaschal: an older screenshot, taken with the apps the other
    /// way round, has the right size and the wrong half.
    static func cropIfScreenshot(_ image: CGImage, screen: CGRect, window: CGRect, scale: CGFloat,
                                 snapshot: CGImage? = nil) -> CGImage {
        guard isScreenshot(image, screen: screen, scale: scale) else { return image }
        if let snapshot, !showsWindow(image, snapshot: snapshot, screen: screen, window: window) { return image }
        return crop(image, screen: screen, window: window)
    }

    /// Whether `screenshot` shows our window where it is now. Our snapshot is
    /// compared with that part of the screenshot and with the same-sized part
    /// on the far side; ours has to be clearly the closer of the two. When the
    /// two can't be told apart (two blank pages) the answer is no, and the
    /// paste goes in whole: a whole screenshot is easier to fix than a wrong half.
    static func showsWindow(_ screenshot: CGImage, snapshot: CGImage, screen: CGRect, window: CGRect) -> Bool {
        let ours = window.intersection(screen)
        guard !ours.isNull, ours.width > 0, ours.height > 0 else { return false }
        let imageSize = CGSize(width: screenshot.width, height: screenshot.height)
        // Side by side mirrors left/right; stacked mirrors top/bottom.
        let mirrored = ours.height >= screen.height - 1
            ? CGRect(x: screen.minX + screen.maxX - ours.maxX, y: ours.minY, width: ours.width, height: ours.height)
            : CGRect(x: ours.minX, y: screen.minY + screen.maxY - ours.maxY, width: ours.width, height: ours.height)
        let width = 48, height = max(8, Int((48 * ours.height / ours.width).rounded()))
        guard let reference = grayscale(snapshot, crop: nil, width: width, height: height),
              let here = grayscale(screenshot, crop: pixelRect(ours, screen: screen, imageSize: imageSize),
                                   width: width, height: height) else { return false }
        let near = difference(reference, here, width: width)
        guard near < 0.25 else { return false }
        if mirrored.intersection(ours).width > ours.width * 0.5 { return near < 0.06 }
        guard let there = grayscale(screenshot, crop: pixelRect(mirrored, screen: screen, imageSize: imageSize),
                                    width: width, height: height) else { return false }
        return near < 0.6 * difference(reference, there, width: width)
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
    func geometry() -> (screen: CGRect, window: CGRect, scale: CGFloat)? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first,
              let window = scene.keyWindow ?? scene.windows.first else { return nil }
        let space = scene.screen.coordinateSpace
        return (scene.screen.bounds, window.convert(window.bounds, to: space), scene.screen.scale)
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
