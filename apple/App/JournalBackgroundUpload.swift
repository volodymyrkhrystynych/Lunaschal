import Foundation
import LunaschalCore

/// The background `URLSession` that carries journal videos and Live Photos.
///
/// A phone video is hundreds of megabytes, and the foreground session gives a
/// whole upload fifteen minutes and stops the moment the app is closed. This
/// one is the system's: it keeps sending after Lunaschal is swiped away, and
/// relaunches it when the upload is done so the result can be written into
/// `BackgroundUploads`. The outbox still decides when a capture is delivered —
/// this only records what the server answered (see `BackgroundUploads`).
///
/// There must be exactly one of these per process: two sessions with the same
/// identifier is undefined behaviour, so `CaptureModel` owns it and the app
/// delegate reaches it through the model.
final class JournalBackgroundUploader: NSObject, BackgroundUploading, URLSessionDataDelegate {
    static let identifier = "com.lunaschal.mobile.journal.attachments"

    private let ledger: BackgroundUploads
    /// Called on the main queue whenever an upload finishes, to start a sync
    /// pass that confirms it.
    var finished: () -> Void = {}
    private var completion: (() -> Void)?
    private let queue: OperationQueue = {
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        return queue
    }()
    /// The server's answer to each task, which arrives in pieces before it completes.
    private var received: [Int: Data] = [:]
    private lazy var session = URLSession(configuration: Self.configuration(), delegate: self, delegateQueue: queue)

    init(ledger: BackgroundUploads) {
        self.ledger = ledger
        super.init()
    }

    static func configuration() -> URLSessionConfiguration {
        let config = URLSessionConfiguration.background(withIdentifier: identifier)
        config.sessionSendsLaunchEvents = true
        config.isDiscretionary = false
        config.httpShouldSetCookies = false
        config.httpCookieStorage = nil
        config.urlCache = nil
        return config
    }

    func handOff(_ request: URLRequest, body: URL, taskID: String) throws {
        let task = session.uploadTask(with: request, fromFile: body)
        task.taskDescription = taskID
        task.resume()
    }

    /// Clears records of uploads the system no longer has a task for — the app
    /// was force-quit, which cancels them. Run once at launch, before the
    /// first sync pass can hand anything new off.
    func reconcile() async {
        let live = Set(await session.allTasks.compactMap(\.taskDescription))
        try? ledger.reconcile(live: live)
    }

    /// The app was relaunched to hear how its uploads went.
    func attach(completion: @escaping () -> Void) {
        self.completion = completion
        _ = session
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        received[dataTask.taskIdentifier, default: Data()].append(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let data = received.removeValue(forKey: task.taskIdentifier) ?? Data()
        guard let id = task.taskDescription else { return }
        // A transport error has no status: the ledger then hands the file off again.
        let status = error == nil ? (task.response as? HTTPURLResponse)?.statusCode : nil
        try? ledger.finish(id, status: status, data: data)
        DispatchQueue.main.async { [weak self] in self?.finished() }
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        DispatchQueue.main.async { [weak self] in
            self?.completion?()
            self?.completion = nil
        }
    }
}
