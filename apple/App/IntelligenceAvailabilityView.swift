import SwiftUI
import FoundationModels

struct IntelligenceAvailabilityView: View {
    @Environment(\.scenePhase) private var phase
    @State private var message = "Checking availability…"

    var body: some View {
        Section("Apple Intelligence") {
            Text(message)
            Button("Check availability") { refresh() }
            Text("This checks Apple's on-device text model. Recording transcription still runs on your server; offline recordings are always retained.")
                .font(.footnote).foregroundStyle(.secondary)
        }
        .task { refresh() }
        .onChange(of: phase) { _, phase in if phase == .active { refresh() } }
    }

    private func refresh() {
        let model = SystemLanguageModel.default
        switch model.availability {
        case .available:
            message = model.supportsLocale(.current)
                ? "The on-device text model is available for your current language."
                : "The model is available, but your current language is not supported."
        case .unavailable(.appleIntelligenceNotEnabled):
            message = "Apple Intelligence is turned off. You can enable it in device Settings."
        case .unavailable(.deviceNotEligible):
            message = "Apple Intelligence is not available on this device."
        case .unavailable(.modelNotReady):
            message = "The on-device model is not ready. Apple manages its download; check again later."
        case .unavailable:
            message = "The on-device model is currently unavailable."
        @unknown default:
            message = "The on-device model is currently unavailable."
        }
    }
}
