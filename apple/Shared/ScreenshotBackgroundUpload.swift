import Foundation
import LunaschalCore

/// Shared screenshots the extension could not deliver itself, handed to a
/// background `URLSession` so the system sends them after the share sheet has
/// closed and once the server is reachable — rather than waiting for
/// Lunaschal to be opened. That is the point of a screenshot run: it should
/// already be in the journal when you go to look.
///
/// Delivery is still confirmed only by the app's own `ScreenshotOutbox.run`,
/// which validates a receipt before removing a shot. The server de-duplicates
/// by the client-minted attachment id, so that replay after a background
/// upload already landed returns the same entry and creates nothing.
enum ScreenshotBackgroundUpload {
    static let identifier = "com.lunaschal.mobile.share.screenshots"

    static func configuration() -> URLSessionConfiguration {
        let config = URLSessionConfiguration.background(withIdentifier: identifier)
        config.sharedContainerIdentifier = SharedSignIn.appGroup
        config.sessionSendsLaunchEvents = true
        config.isDiscretionary = false
        config.httpShouldSetCookies = false
        config.httpCookieStorage = nil
        return config
    }

    /// Hands every queued shot not already in flight to the background
    /// session. Returns how many are now on their way.
    @discardableResult
    static func handOff(_ outbox: ScreenshotOutbox, api: JournalAPI) throws -> Int {
        let session = URLSession(configuration: configuration())
        defer { session.finishTasksAndInvalidate() }
        try FileManager.default.createDirectory(at: outbox.bodies, withIntermediateDirectories: true)
        var handed = 0
        for shot in try outbox.list() {
            if outbox.isHandedOff(shot) { handed += 1; continue }
            let (request, body) = try api.screenshotUploadRequest(shot, file: outbox.file(for: shot))
            let kept = outbox.body(for: shot)
            try FileManager.default.moveItem(at: body, to: kept)
            let task = session.uploadTask(with: request, fromFile: kept)
            task.taskDescription = shot.id
            task.resume()
            handed += 1
        }
        return handed
    }
}

/// The app's end of the background session: the system relaunches Lunaschal
/// when the extension's uploads finish, and this reconnects to collect the
/// events, then asks for a sync so the outbox reconciles with the server.
final class ScreenshotBackgroundReceiver: NSObject, URLSessionDataDelegate {
    private var session: URLSession?
    private var completion: (() -> Void)?
    private let finished: () -> Void

    init(finished: @escaping () -> Void) {
        self.finished = finished
    }

    func attach(completion: @escaping () -> Void) {
        self.completion = completion
        if session == nil {
            session = URLSession(configuration: ScreenshotBackgroundUpload.configuration(), delegate: self, delegateQueue: nil)
        }
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        DispatchQueue.main.async { [weak self] in
            self?.finished()
            self?.completion?()
            self?.completion = nil
        }
    }
}
