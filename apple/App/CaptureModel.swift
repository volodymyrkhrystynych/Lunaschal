import Foundation
import SwiftUI
import LunaschalCore
import AVFoundation
import UIKit

@MainActor
final class CaptureModel: ObservableObject {
    @Published private(set) var captures: [Capture] = []
    @Published private(set) var server: URL?
    @Published private(set) var signedIn = false
    @Published private(set) var syncing = false
    @Published private(set) var signingIn = false
    @Published var message: String?
    @Published private(set) var syncMessage: String?
    let store: CaptureStore
    let recorder: Recorder
    private let syncer: CaptureSync
    private var activeAPI: JournalAPI?
    private var token: String?
    private var syncingTask: Task<Void, Never>?

    init(store: CaptureStore) throws {
        self.store = store
        recorder = Recorder(store: store)
        syncer = CaptureSync(store: store)
        try store.recoverInterruptedRecordings()
        server = try store.server
        if let server { token = try SessionToken.read(server: server) }
        signedIn = token != nil
        captures = try store.list()
        recorder.onChange = { [weak self] in
            self?.reload()
            self?.requestSync()
        }
        recorder.onError = { [weak self] in self?.message = $0.localizedDescription }
    }

    var allowCellular: Bool {
        // Defaults to true, including before Settings has ever been opened.
        UserDefaults.standard.object(forKey: "allowCellularSync") as? Bool ?? true
    }

    func reload() {
        do { captures = try store.list() } catch { message = error.localizedDescription }
    }

    func saveText(_ text: String) -> Bool {
        do {
            try store.save(Capture(text: text.trimmingCharacters(in: .whitespacesAndNewlines)))
            reload()
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func login(address: String, password: String, code: String) async -> Bool {
        guard !signingIn else { return false }
        signingIn = true
        defer { signingIn = false }
        do {
            let url = try ServerAddress.parse(address)
            if let server, server != url { throw CaptureError.differentServer }
            let api = try JournalAPI(server: url, token: nil, allowCellular: allowCellular)
            let value = try await api.login(password: password, code: code)
            try store.bind(to: url)
            server = url
            try SessionToken.save(value, server: url)
            token = value
            signedIn = true
            message = nil
            requestSync()
            return true
        } catch { message = error.localizedDescription; return false }
    }

    func signOut() {
        cancelSync()
        do {
            if let server { try SessionToken.remove(server: server) }
            token = nil
            signedIn = false
        } catch { message = error.localizedDescription }
    }

    func requestSync() {
        guard UIApplication.shared.applicationState == .active,
              !syncing, signedIn, let server, let token else { return }
        syncing = true
        syncingTask = Task {
            defer { syncing = false; activeAPI = nil; reload() }
            do {
                let api = try JournalAPI(server: server, token: token, allowCellular: allowCellular)
                activeAPI = api
                try await syncer.run(using: api)
                syncMessage = nil
            } catch {
                if Task.isCancelled { return }
                // Being offline is normal; don't show an alert every retry.
                syncMessage = error.localizedDescription
                if let failure = error as? HTTPFailure, [401, 403].contains(failure.status) {
                    signedIn = false
                }
            }
        }
    }

    func cancelSync() {
        syncingTask?.cancel()
        activeAPI?.cancel()
    }

    func retry(_ capture: Capture) {
        do {
            if capture.state == .interrupted {
                // Reject a broken/unfinalized AAC container without erasing it.
                let audio = try AVAudioPlayer(contentsOf: store.audioURL(capture))
                guard audio.duration > 0 else { throw CaptureError.missingAudio }
                try store.finishRecording(capture.id)
            } else {
                var item = try store.load(capture.id)
                guard item.state == .failed else { return }
                item.state = .pending
                item.lastError = nil
                try store.save(item)
            }
            reload()
            requestSync()
        } catch { message = error.localizedDescription }
    }
}
