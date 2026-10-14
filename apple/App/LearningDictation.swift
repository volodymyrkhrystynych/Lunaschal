import AVFoundation
import Foundation
import LunaschalCore

/// The review's mic button: record, and on Stop hand the clip over as the
/// answer. Nothing waits on the words; the server transcribes the clip and
/// grades it while the next card is already up (`LearningModel.submitRecording`,
/// which owns the file from then on and deletes it once sent).
@MainActor
final class LearningDictation: NSObject, ObservableObject, AVAudioRecorderDelegate {
    enum Status: Equatable { case idle, starting, recording }

    @Published private(set) var status = Status.idle
    @Published var error: String?

    private let capture: CaptureModel
    private let transport: () -> LearningTransport?
    private var recorder: AVAudioRecorder?
    private var clip: URL?
    private var deliver: ((URL) -> Void)?

    init(capture: CaptureModel, transport: @escaping () -> LearningTransport?) {
        self.capture = capture
        self.transport = transport
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted),
            name: AVAudioSession.interruptionNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    /// Starts recording, or stops; `onClip` gets the recorded file.
    func toggle(onClip: @escaping (URL) -> Void) async {
        switch status {
        case .recording: stop()
        case .idle: await start(onClip)
        case .starting: return
        }
    }

    private func start(_ onClip: @escaping (URL) -> Void) async {
        guard transport() != nil else {
            error = "Dictation needs your server."
            return
        }
        status = .starting
        error = nil
        // One microphone: a Capture recording in progress gives way, as it does for Chat.
        if capture.recorder.activeID != nil { capture.recorder.stop() }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("learning-\(UUID().uuidString)").appendingPathExtension("m4a")
        do {
            guard await AVAudioApplication.requestRecordPermission() else {
                throw RecorderError.message("Allow microphone access in Settings to dictate.")
            }
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
            let audio = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100,
                AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000,
            ])
            audio.delegate = self
            guard audio.prepareToRecord(), audio.record() else { throw RecorderError.message("Could not start the recorder.") }
            recorder = audio
            clip = url
            deliver = onClip
            status = .recording
        } catch {
            try? FileManager.default.removeItem(at: url)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            self.error = error.localizedDescription
            status = .idle
        }
    }

    func stop() {
        guard status == .recording, let url = clip else { return }
        recorder?.stop()
        recorder = nil
        clip = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        let handOver = deliver
        deliver = nil
        status = .idle
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        guard size > 0, let handOver else {
            try? FileManager.default.removeItem(at: url)
            error = "Nothing was recorded."
            return
        }
        handOver(url)
    }

    /// Leaving the card drops a recording rather than typing it into the next one.
    func cancel() {
        guard status == .recording else { return }
        recorder?.stop()
        recorder = nil
        if let clip { try? FileManager.default.removeItem(at: clip) }
        clip = nil
        deliver = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        status = .idle
    }

    @objc nonisolated private func interrupted(_ notification: Notification) {
        guard let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              type == AVAudioSession.InterruptionType.began.rawValue else { return }
        // What was said before the call is still worth answering with.
        Task { @MainActor in self.stop() }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.cancel()
            self.error = error?.localizedDescription ?? "Audio encoding failed."
        }
    }
}
