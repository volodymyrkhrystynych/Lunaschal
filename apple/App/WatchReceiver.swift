import Foundation
import WatchConnectivity
import LunaschalCore

final class WatchReceiver: NSObject, WCSessionDelegate {
    private let inbox: WatchInbox
    private let store: CaptureStore
    private let activity: ActivityStore
    var onChange: (@MainActor () -> Void)?
    var onError: (@MainActor (Error) -> Void)?
    /// A work / leisure / mix switch made on the Watch, already saved here.
    var onActivity: (@MainActor (ActivitySwitch) -> Void)?
    /// The session is up: the moment to hand the Watch its glance.
    var onActivated: (@MainActor () -> Void)?

    init(store: CaptureStore, activity: ActivityStore) throws {
        self.store = store
        self.activity = activity
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
            sendServerReceipts()
        } catch { onError?(error) }
    }

    @MainActor func sendServerReceipts() {
        let session = WCSession.default
        guard session.activationState == .activated else { return }
        do {
            let pending = Set(session.outstandingUserInfoTransfers.compactMap { $0.userInfo["serverReceiptID"] as? String })
            for receipt in try WatchReceipts(store: store).pendingServerReceipts() where !pending.contains(receipt.captureID) {
                session.transferUserInfo(["serverReceiptID": receipt.captureID,
                                          "serverStored": try JSONEncoder().encode(receipt)])
            }
        } catch { onError?(error) }
    }

    /// The state and habit counts for the Watch's complications. Application
    /// context keeps only the latest, which is all a glance is; the Watch
    /// gets it when it next wakes if it isn't reachable now.
    @MainActor func send(_ glance: WatchGlance) {
        let session = WCSession.default
        guard WCSession.isSupported(), session.activationState == .activated,
              session.isPaired, session.isWatchAppInstalled else { return }
        do { try session.updateApplicationContext(glance.context) } catch { onError?(error) }
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
        if let bytes = userInfo["activitySwitch"] as? Data {
            do {
                let change = try JSONDecoder().decode(ActivitySwitch.self, from: bytes)
                // Durable before the Watch is told it may forget the switch.
                try activity.save(change)
                session.transferUserInfo(["activityStored": change.id])
                Task { @MainActor in self.onActivity?(change); self.onChange?() }
            } catch { Task { @MainActor in self.onError?(error) } }
            return
        }
        if let bytes = userInfo["serverReceiptRequest"] as? Data {
            do {
                let requested = try JSONDecoder().decode(WatchServerReceipt.self, from: bytes)
                let capture = try store.load(requested.captureID)
                guard requested.matches(capture) else { throw CaptureError.invalidResponse }
                // Older versions imported audio without an origin marker. The
                // paired Watch can identify its existing capture without reupload.
                try WatchReceipts(store: store).markOrigin(capture)
                Task { @MainActor in self.sendServerReceipts() }
            } catch { Task { @MainActor in self.onError?(error) } }
            return
        }
        guard let bytes = userInfo["serverReceiptStored"] as? Data else { return }
        do {
            let receipt = try JSONDecoder().decode(WatchServerReceipt.self, from: bytes)
            try WatchReceipts(store: store).confirmDelivery(receipt)
        } catch { Task { @MainActor in self.onError?(error) } }
    }

    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {
        if state == .activated { Task { @MainActor in self.drain(); self.onActivated?() } }
    }
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}
