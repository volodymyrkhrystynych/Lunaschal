import AVFoundation
import Combine
import LunaschalCore

@MainActor
final class Recorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    @Published private(set) var activeID: String?
    @Published private(set) var isStarting = false
    var onChange: (() -> Void)?
    var onError: ((Error) -> Void)?
    private let store: CaptureStore
    private var recorder: AVAudioRecorder?

    init(store: CaptureStore) {
        self.store = store
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted),
            name: AVAudioSession.interruptionNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    func start(mode: CaptureMode) async {
        guard activeID == nil, !isStarting else { return }
        isStarting = true
        defer { isStarting = false }
        var capture: Capture?
        do {
            let available = (try FileManager.default.attributesOfFileSystem(forPath: store.root.path)[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
            guard available > 32 * 1024 * 1024 else {
                throw RecorderError.message("Free some device storage before starting a recording. Saved audio has been kept.")
            }
            let permission = await AVAudioApplication.requestRecordPermission()
            guard permission else {
                throw RecorderError.message("Allow microphone access in Settings to record.")
            }
            let session = AVAudioSession.sharedInstance()
            #if os(watchOS)
            try session.setCategory(.record, mode: .default)
            #else
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            #endif
            try session.setActive(true)
            let item = Capture(mode: mode)
            // Commit the identity before opening the audio file. After a crash,
            // there is still a manifest explaining whose recording this is.
            try store.save(item)
            capture = item
            let audio = try AVAudioRecorder(url: store.audioURL(item), settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 44100,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 64000
            ])
            audio.delegate = self
            guard audio.prepareToRecord(), audio.record() else {
                throw RecorderError.message("Could not start the recorder.")
            }
            recorder = audio
            activeID = item.id
            onChange?()
        } catch {
            if var item = capture {
                item.state = .interrupted
                item.lastError = error.localizedDescription
                do { try store.save(item) } catch { onError?(error) }
            }
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            onChange?()
            onError?(error)
        }
    }

    func stop() {
        guard let id = activeID else { return }
        activeID = nil
        recorder?.stop()
        recorder = nil
        do { try store.finishRecording(id) } catch { onError?(error) }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onChange?()
    }

    @objc nonisolated private func interrupted(_ notification: Notification) {
        guard let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              type == AVAudioSession.InterruptionType.began.rawValue else { return }
        Task { @MainActor in self.stop() }
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            // Ignore the delegate callback from a recording we already stopped.
            guard self.recorder === recorder else { return }
            if flag { self.stop() }
            else { self.fail("Recording stopped unexpectedly. Its original file has been kept.") }
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.fail(error?.localizedDescription ?? "Audio encoding failed.")
        }
    }

    private func fail(_ message: String) {
        guard let id = activeID else { return }
        activeID = nil
        recorder?.stop()
        recorder = nil
        do {
            var item = try store.load(id)
            item.state = .interrupted
            item.lastError = message
            try store.save(item)
        } catch { onError?(error) }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        onChange?()
        onError?(RecorderError.message(message))
    }
}

enum RecorderError: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case let .message(value): return value }
    }
}
