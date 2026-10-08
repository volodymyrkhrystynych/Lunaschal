import AppIntents
import SwiftUI
import WidgetKit
import LunaschalCore

/// Controls, for Control Center, the Smart Stack and the Action button: the
/// one place on the Watch where a press acts without opening the app. (A
/// complication on the face always opens it.)

struct TimerValue: ControlValueProvider {
    let choice: TimerChoice
    var previewValue: PomodoroTimer.Run? { nil }

    /// The run this button owns, if one is going.
    func currentValue() async throws -> PomodoroTimer.Run? {
        let timer = WatchComplication.timerURL.flatMap(PomodoroTimer.load(from:)) ?? PomodoroTimer()
        guard let run = timer.run, run.endsAt > Date(), choice.owns(run) else { return nil }
        return run
    }
}

/// Focus or Timeout: the first press starts it with the app left asleep, a
/// press while it runs opens the timer.
struct TimerControl {
    let choice: TimerChoice

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: choice.controlKind, provider: TimerValue(choice: choice)) { run in
            ControlWidgetButton(action: StartTimerIntent(choice)) {
                if let run {
                    Label {
                        Text(run.kind.label)
                        Text("Until \(run.endsAt, style: .time)")
                    } icon: { Image(systemName: run.kind.symbol) }
                } else {
                    Label("\(choice.title) \(choice.kind.minutes)", systemImage: choice.symbol)
                }
            }
        }
        .displayName(choice == .focus ? "Focus 25" : "Timeout 10")
        .description(choice == .focus ? "Starts 25 minutes of focus." : "Starts a 10-minute timeout.")
    }
}

struct RecordingValue: ControlValueProvider {
    let choice: RecordingChoice
    var previewValue: Bool { false }

    func currentValue() async throws -> Bool {
        WatchComplication.recordingURL.flatMap(RecordingStatus.load(from:))?.mode == choice.mode
    }
}

/// Record or Transcribe: on starts a clip, off stops and saves it.
struct RecordingControl {
    let choice: RecordingChoice

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: choice.controlKind, provider: RecordingValue(choice: choice)) { isOn in
            ControlWidgetToggle(isOn: isOn, action: ToggleRecordingIntent(choice)) {
                Label(choice.title, systemImage: isOn ? "stop.fill" : choice.symbol)
            } valueLabel: { on in
                Text(on ? "Recording" : "Off")
            }
            .tint(.red)
        }
        .displayName(LocalizedStringResource(stringLiteral: choice.title))
        .description(choice == .record ? "Starts and stops a journal clip." : "Starts and stops a clip that becomes text.")
    }
}

// WidgetKit makes each control with `init()`, so every choice is its own type.
struct FocusControl: ControlWidget { var body: some ControlWidgetConfiguration { TimerControl(choice: .focus).body } }
struct TimeoutControl: ControlWidget { var body: some ControlWidgetConfiguration { TimerControl(choice: .timeout).body } }
struct RecordControl: ControlWidget { var body: some ControlWidgetConfiguration { RecordingControl(choice: .record).body } }
struct TranscribeControl: ControlWidget { var body: some ControlWidgetConfiguration { RecordingControl(choice: .transcribe).body } }
