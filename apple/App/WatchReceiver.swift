import Foundation
import WatchConnectivity
import LunaschalCore

final class WatchReceiver: NSObject, WCSessionDelegate {
    private let inbox: WatchInbox
    private let store: CaptureStore
    var onChange: (@MainActor () -> Void)?
    var onError: (@MainActor (Error) -> Void)?

    init(store: CaptureStore) throws {
        self.store = store
        inbox = try WatchInbox(root: store.root.appendingPathComponent("watch-inbox", isDirectory: true))
        super.init()
    }

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    nonisolated func session(_ session: WCSession, didReceive file: WCSessionFile) {
        do {
            guard let metadata = file.metadata?["envelope"] as? Data else { throw CaptureError.invalidResponse }
            // Copy now: WatchConnectivity deletes file.fileURL when this returns.
            try inbox.stage(file: file.fileURL, metadata: metadata)
            Task { @MainActor in self.drain() }
        } catch { Task { @MainActor in self.onError?(error) } }
    }

    @MainActor func drain() {
        do {
            for id in try inbox.drain(into: store, hash: MediaStore.sha256,
                                     onFailure: { self.onError?($0) }) {
                WCSession.default.transferUserInfo(["phoneStored": id])
            }
            onChange?()
        } catch { onError?(error) }
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        if state == .activated { Task { @MainActor in self.drain() } }
    }
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}
