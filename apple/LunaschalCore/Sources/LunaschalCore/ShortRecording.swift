import Foundation

/// The Watch's stray-press rule. Its big buttons, face and Controls are easy
/// to hit by accident, and the phone's review-before-Save step has no
/// equivalent there, so a clip stopped this soon is thrown away.
public enum ShortRecording {
    public static let minimum: TimeInterval = 5

    public static func shouldDiscard(duration: TimeInterval, minimum: TimeInterval? = ShortRecording.minimum) -> Bool {
        minimum.map { duration < $0 } ?? false
    }

    public static func warning(_ mode: CaptureMode) -> String {
        let kind = mode == .transcribe ? "Transcription" : "Recording"
        return "\(kind) under \(Int(minimum)) seconds discarded"
    }
}
