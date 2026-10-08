import AppIntents
import Foundation
import LunaschalCore

/// The two timers a button can start. A break only ever follows focus.
enum TimerChoice: String, AppEnum {
    case focus, timeout

    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Timer"
    static let caseDisplayRepresentations: [TimerChoice: DisplayRepresentation] = [
        .focus: "Focus 25", .timeout: "Timeout 10",
    ]

    var kind: PomodoroKind { self == .focus ? .work : .timeout }
}

/// Focus or Timeout from Control Center, the Smart Stack or the Action
/// button. Starts at once with the app left asleep; pressed again while a
/// run is going (or waiting for Continue / Break), it opens the timer.
struct StartTimerIntent: AppIntent {
    static let title: LocalizedStringResource = "Start timer"
    static let supportedModes: IntentModes = [.background, .foreground(.dynamic)]

    @Parameter(title: "Timer") var timer: TimerChoice

    init() {}
    init(_ timer: TimerChoice) { self.timer = timer }

    func perform() async throws -> some IntentResult {
        let (_, press, _) = try PomodoroEngine.standard().apply { $0.press(timer.kind) }
        if press == .alreadyGoing {
            PomodoroEngine.requestOpen(.timer)
            try await continueInForeground(alwaysConfirm: false)
        }
        return .result()
    }
}

/// What a recording button makes.
enum RecordingChoice: String, AppEnum {
    case record, transcribe

    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Recording"
    static let caseDisplayRepresentations: [RecordingChoice: DisplayRepresentation] = [
        .record: "Record", .transcribe: "Transcribe",
    ]

    var mode: CaptureMode { self == .record ? .record : .transcribe }
}

/// Record or Transcribe as a toggle: on starts a clip, off stops and saves
/// it, without bringing the app forward. As an `AudioRecordingIntent` it runs
/// in the app's process, which is where the recorder and the clips are; the
/// extension only has to know the type exists to put it on a Control.
struct ToggleRecordingIntent: SetValueIntent, AudioRecordingIntent {
    static let title: LocalizedStringResource = "Record a clip"

    @Parameter(title: "Recording") var recording: RecordingChoice
    @Parameter(title: "Recording") var value: Bool

    init() {}
    init(_ recording: RecordingChoice) { self.recording = recording }

    func perform() async throws -> IntentResultContainer<Never, Never, Never, Never> {
        #if WATCH_APP
        try await WatchModel.toggleRecording(recording.mode, on: value)
        return .result()
        #else
        // Reached only if the system ever runs this in the extension, which
        // has no microphone session or clip store of its own.
        throw RecordingIntentError.needsApp
        #endif
    }
}

enum RecordingIntentError: Error, CustomLocalizedStringResourceConvertible {
    case needsApp
    var localizedStringResource: LocalizedStringResource { "Open Lunaschal on the Watch to record." }
}
