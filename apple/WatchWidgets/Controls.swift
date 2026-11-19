import AppIntents
import SwiftUI
import WidgetKit
import LunaschalCore

/// Controls, for Control Center, the Smart Stack and the Action button: the
/// one place on the Watch where a press acts without opening the app. (A
/// complication on the face always opens it.)

struct RecordingValue: ControlValueProvider {
    let choice: RecordingChoice
    var previewValue: Bool { false }

    func currentValue() async throws -> Bool {
        choice.matches(WatchComplication.recordingURL.flatMap(RecordingStatus.load(from:)))
    }
}

/// Record, Transcribe or To-do: on starts a clip, off stops and saves it.
struct RecordingControl {
    let choice: RecordingChoice

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: choice.controlKind, provider: RecordingValue(choice: choice)) { isOn in
            ControlWidgetToggle(isOn: isOn, action: ToggleRecordingIntent(choice)) {
                Label(choice.title, systemImage: choice.symbol)
            } valueLabel: { on in
                Text(on ? "Recording" : "Off")
            }
            .tint(.red)
        }
        .displayName(LocalizedStringResource(stringLiteral: choice.title))
        .description(LocalizedStringResource(stringLiteral: choice.summary))
    }
}

// WidgetKit makes each control with `init()`, so every choice is its own type.
struct RecordControl: ControlWidget { var body: some ControlWidgetConfiguration { RecordingControl(choice: .record).body } }
struct TranscribeControl: ControlWidget { var body: some ControlWidgetConfiguration { RecordingControl(choice: .transcribe).body } }
struct TodoControl: ControlWidget { var body: some ControlWidgetConfiguration { RecordingControl(choice: .todo).body } }
