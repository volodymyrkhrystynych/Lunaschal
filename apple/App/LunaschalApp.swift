import SwiftUI
import LunaschalCore

@main
@MainActor
struct LunaschalApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            switch appDelegate.startup {
            case .some(.success(let model)):
                CaptureRoot(model: model)
                #if DEBUG
                    .onAppear(perform: Self.compactWidthIfAsked)
                #endif
            case .some(.failure(let error)):
                ContentUnavailableView("Could not open saved captures", systemImage: "externaldrive.badge.exclamationmark",
                    description: Text("\(error.localizedDescription)\nExisting files have not been reset."))
            case nil: ProgressView("Opening saved captures…")
            }
        }
    }

    #if DEBUG
    /// `-compactWidth`: what an iPad window in Split View or Slide Over is, for
    /// the UI tests, which cannot resize the simulator's window.
    private static func compactWidthIfAsked() {
        guard ProcessInfo.processInfo.arguments.contains("-compactWidth") else { return }
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            for window in scene.windows { window.traitOverrides.horizontalSizeClass = .compact }
        }
    }
    #endif
}
