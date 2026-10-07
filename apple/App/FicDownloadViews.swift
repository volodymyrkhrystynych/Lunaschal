import SwiftUI
import LunaschalCore

/// The library's download indicator: the fic downloading now, how far it has
/// got and how long it has left, and what is queued behind it.
struct FicDownloadBanner: View {
    @ObservedObject var model: CaptureModel

    var body: some View {
        if let status = model.ficDownload {
            VStack(alignment: .leading, spacing: 6) {
                Label {
                    Text("Downloading \(status.title)").lineLimit(1)
                } icon: {
                    Image(systemName: "arrow.down.circle")
                }
                .font(.subheadline.weight(.semibold))
                ProgressView(value: status.fraction)
                Text(status.detail).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                let waiting = model.ficQueue.entries.dropFirst()
                if !waiting.isEmpty {
                    Text("Next: " + waiting.map(\.title).joined(separator: ", "))
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            .padding(.vertical, 4)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("fic-download-banner")
        }
    }
}

/// One book's own download state, shown where its text would be.
struct FicDownloadState: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange

    var body: some View {
        if let status = model.ficDownload, status.id == book.id {
            VStack(alignment: .leading, spacing: 6) {
                Text("Downloading this fic").font(.headline)
                ProgressView(value: status.fraction)
                Text(status.detail).font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("fic-download-progress")
        } else if let place = model.ficQueue.entries.firstIndex(where: { $0.id == book.id }) {
            Label(place == 0 ? "Starting download…" : "Queued · \(place) ahead",
                  systemImage: "clock.arrow.circlepath")
                .foregroundStyle(.secondary)
        } else if let error = model.ficErrors[book.id] {
            VStack(alignment: .leading, spacing: 6) {
                Text(error).foregroundStyle(.secondary)
                Button("Try again") { model.ensureFicOnDevice(book) }
            }
        } else if !model.signedIn {
            Text("Sign in to download this fic.").foregroundStyle(.secondary)
        }
    }
}
