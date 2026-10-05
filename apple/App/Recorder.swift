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
    /// The phone's composer records into its draft; the Watch records whole entries.
    private var intoDraft = false

    init(store: CaptureStore) {
        self.store = store
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted),
            name: AVAudioSession.interruptionNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    /// Records a clip into the Capture tab's draft. Stopping keeps it there;
    /// only Save entry sends it, as part of that entry.
    func startClip(transcribe: Bool) async {
        await begin(intoDraft: true, mode: transcribe ? .transcribe : .record)
    }

    /// Records a standalone entry: stopping saves it and queues it for sync.
    func start(mode: CaptureMode) async {
        await begin(intoDraft: false, mode: mode)
    }

    private func begin(intoDraft draft: Bool, mode: CaptureMode) async {
        guard activeID == nil, !isStarting else { return }
        isStarting = true
        defer { isStarting = false }
        var capture: Capture?
        var clip: CaptureClip?
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
            // Commit the identity before opening the audio file. After a crash,
            // there is still a manifest (or draft row) explaining whose recording this is.
            let url: URL, id: String
            if draft {
                let item = try store.beginClip(transcribe: mode == .transcribe)
                clip = item
                url = try store.clipURL(item); id = item.attachmentID
            } else {
                let item = Capture(mode: mode)
                try store.save(item)
                capture = item
                url = try store.audioURL(item); id = item.id
            }
            let audio = try AVAudioRecorder(url: url, settings: [
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
            intoDraft = draft
            activeID = id
            onChange?()
        } catch {
            if let clip { do { try store.discard(clip) } catch { onError?(error) } }
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
        do {
            if intoDraft { try store.finishClip(id) } else { try store.finishRecording(id) }
        } catch { onError?(error) }
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
            if intoDraft {
                try store.interruptClip(id)
            } else {
                var item = try store.load(id)
                item.state = .interrupted
                item.lastError = message
                try store.save(item)
            }
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
