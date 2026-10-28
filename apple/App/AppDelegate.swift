import UIKit
import BackgroundTasks
import Combine
import LunaschalCore

@MainActor
final class AppDelegate: NSObject, UIApplicationDelegate, ObservableObject {
    @Published private(set) var startup: Result<CaptureModel, Error>?
    private var background: BackgroundSync?
    private var screenshotUploads: ScreenshotBackgroundReceiver?
    static let syncIdentifier = "com.lunaschal.mobile.sync"

    /// The share extension's background screenshot uploads finished: collect
    /// the session's events, then sync so the outbox reconciles and the
    /// journal shows the new run.
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        // Journal videos and Live Photos: the model owns that session, since
        // there may be only one per identifier.
        if identifier == JournalBackgroundUploader.identifier {
            guard case .success(let model) = startup else { return completionHandler() }
            model.journalUploader.finished = { [weak model] in model?.requestSync() }
            model.journalUploader.attach(completion: completionHandler)
            return
        }
        guard identifier == ScreenshotBackgroundUpload.identifier else { return completionHandler() }
        if screenshotUploads == nil {
            screenshotUploads = ScreenshotBackgroundReceiver { [weak self] in
                if case .success(let model) = self?.startup { model.requestSync() }
            }
        }
        screenshotUploads?.attach(completion: completionHandler)
    }

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let result = Result {
            let directory = try FileManager.default.url(for: .applicationSupportDirectory,
                in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent("Captures", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
            #if DEBUG
            try JournalFixture.seedIfAsked(root: directory)
            try JobsFixture.seedIfAsked(root: directory)
            try LibraryFixture.seedIfAsked(root: directory)
            #endif
            return try CaptureModel(store: CaptureStore(root: directory))
        }
        startup = result
        if case .success(let model) = result {
            let coordinator = BackgroundSync(scheduler: AppleSyncScheduler(),
                next: { [weak model] in try model?.nextBackgroundSync() },
                run: { [weak model] in await model?.syncInBackground() ?? false },
                cancelRun: { [weak model] in model?.cancelSync() },
                status: { [weak model] in model?.backgroundStatus = $0 })
            background = coordinator
            model.onBackgroundSyncNeeded = { [weak coordinator] in coordinator?.schedule() }
        }
        // Register during launch even if local storage could not be opened, so
        // an OS-delivered task always has a handler and completion path.
        let registered = BGTaskScheduler.shared.register(forTaskWithIdentifier: Self.syncIdentifier,
            using: .main) { [weak self] task in
                Task { @MainActor in
                    guard let processing = task as? BGProcessingTask, let background = self?.background else {
                        task.setTaskCompleted(success: false)
                        return
                    }
                    background.handle(AppleSyncLease(processing))
                }
            }
        if registered { background?.schedule() }
        else if case .success(let model) = result {
            background = nil
            model.onBackgroundSyncNeeded = nil
            model.backgroundStatus = "Background sync could not register. Open the app to sync."
        }
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        if case .some(.success(let model)) = startup { model.leaveForeground() }
    }
}

@MainActor
private struct AppleSyncScheduler: BackgroundSyncScheduling {
    func submit(earliest: Date) throws {
        let request = BGProcessingTaskRequest(identifier: AppDelegate.syncIdentifier)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = false
        request.earliestBeginDate = earliest
        try BGTaskScheduler.shared.submit(request)
    }
    func cancel() { BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: AppDelegate.syncIdentifier) }
}

@MainActor
private final class AppleSyncLease: BackgroundSyncLease {
    private let task: BGProcessingTask
    init(_ task: BGProcessingTask) { self.task = task }
    func onExpiration(_ handler: @escaping @MainActor () -> Void) {
        task.expirationHandler = { Task { @MainActor in handler() } }
    }
    func complete(success: Bool) {
        task.expirationHandler = nil
        task.setTaskCompleted(success: success)
    }
}
