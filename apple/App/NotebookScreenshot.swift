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
    static func cropIfScreenshot(_ image: CGImage, screen: CGRect, window: CGRect, scale: CGFloat) -> CGImage {
        isScreenshot(image, screen: screen, scale: scale) ? crop(image, screen: screen, window: window) : image
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
