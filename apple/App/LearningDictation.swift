import AVFoundation
import Foundation
import LunaschalCore

/// The review's mic button, as the desktop's: record, and on Stop send the
/// clip to the server's speech-to-text and hand back the words. Nothing is
/// kept. The clip is a scratch file and is deleted once transcribed (or not),
/// since the answer it becomes is what the server saves.
@MainActor
final class LearningDictation: NSObject, ObservableObject, AVAudioRecorderDelegate {
    enum Status: Equatable { case idle, starting, recording, transcribing }

    @Published private(set) var status = Status.idle
    @Published var error: String?

    private let capture: CaptureModel
    private let transport: () -> LearningTransport?
    private var recorder: AVAudioRecorder?
    private var clip: URL?
    private var deliver: ((String) -> Void)?

    init(capture: CaptureModel, transport: @escaping () -> LearningTransport?) {
        self.capture = capture
        self.transport = transport
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted),
            name: AVAudioSession.interruptionNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    /// Starts recording, or stops and transcribes; `onText` gets the words.
    func toggle(onText: @escaping (String) -> Void) async {
        switch status {
        case .recording: await stop()
        case .idle: await start(onText)
        case .starting, .transcribing: return
        }
    }

    private func start(_ onText: @escaping (String) -> Void) async {
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
            deliver = onText
            status = .recording
        } catch {
            try? FileManager.default.removeItem(at: url)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            self.error = error.localizedDescription
            status = .idle
        }
    }

    func stop() async {
        guard status == .recording, let url = clip else { return }
        recorder?.stop()
        recorder = nil
        clip = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        status = .transcribing
        defer {
            try? FileManager.default.removeItem(at: url)
            deliver = nil
            status = .idle
        }
        guard let api = transport() else { error = "Signed out before the clip could be sent."; return }
        do {
            let text = try await api.transcribe(audio: url)
            if text.isEmpty { error = "No speech was heard." } else { deliver?(text) }
        } catch {
            self.error = error.localizedDescription
        }
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
        // What was said before the call is still worth transcribing.
        Task { @MainActor in await self.stop() }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.cancel()
            self.error = error?.localizedDescription ?? "Audio encoding failed."
        }
    }
}
