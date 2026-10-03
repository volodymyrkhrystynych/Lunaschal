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

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any]) {
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
        if state == .activated { Task { @MainActor in self.drain() } }
    }
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}
