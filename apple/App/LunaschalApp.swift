import SwiftUI
import LunaschalCore

@main
@MainActor
struct LunaschalApp: App {
    private let startup: Result<CaptureModel, Error>

    init() {
        startup = Result {
            let directory = try FileManager.default.url(for: .applicationSupportDirectory,
                in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent("Captures", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
            return try CaptureModel(store: CaptureStore(root: directory))
        }
    }

    var body: some Scene {
        WindowGroup {
            switch startup {
            case .success(let model): CaptureRoot(model: model)
            case .failure(let error):
                ContentUnavailableView("Could not open saved captures", systemImage: "externaldrive.badge.exclamationmark",
                    description: Text("\(error.localizedDescription)\nExisting files have not been reset."))
            }
        }
    }
}
