import AppIntents
import Combine

/// A request from the Action Button (or Shortcuts, or Siri) to open straight
/// to the camera. A flag rather than a call into the UI: on a cold launch the
/// intent runs before the capture store has opened and `CaptureRoot` exists,
/// so it is left here for `CaptureRoot` to pick up when it appears.
@MainActor
final class QuickPhoto: ObservableObject {
    static let shared = QuickPhoto()
    @Published var requested = false
}

/// *Take Photo*: brings the app forward with the camera open. The photo goes
/// into the Capture composer's draft, and closing the camera leaves you there.
struct TakePhotoIntent: AppIntent {
    static let title: LocalizedStringResource = "Take Photo"
    static let description = IntentDescription("Opens Lunaschal's camera; the photo goes into the Capture draft.")
    static let supportedModes: IntentModes = .foreground(.immediate)

    @MainActor
    func perform() async throws -> some IntentResult {
        QuickPhoto.shared.requested = true
        return .result()
    }
}

/// Listed under Settings → Action Button → Shortcut → Lunaschal, with no
/// Shortcut to build by hand.
struct LunaschalShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: TakePhotoIntent(),
                    phrases: ["Take a photo in \(.applicationName)"],
                    shortTitle: "Take Photo", systemImageName: "camera")
    }
}
