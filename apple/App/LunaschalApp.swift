import SwiftUI
import LunaschalCore

@main
@MainActor
struct LunaschalApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            switch appDelegate.startup {
            case .some(.success(let model)): CaptureRoot(model: model)
            case .some(.failure(let error)):
                ContentUnavailableView("Could not open saved captures", systemImage: "externaldrive.badge.exclamationmark",
                    description: Text("\(error.localizedDescription)\nExisting files have not been reset."))
            case nil: ProgressView("Opening saved captures…")
            }
        }
    }
}
