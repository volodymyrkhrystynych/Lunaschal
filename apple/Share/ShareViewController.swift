import SwiftUI
import UniformTypeIdentifiers
import LunaschalCore

/// "Lunaschal" in the share sheet: a link to a fic on one of the sites the
/// server imports from is sent to `POST /api/fanfic/import`. Out of reach,
/// it waits in the App Group's outbox and the app sends it on its next sync.
/// A YouTube link goes into the Capture composer's draft instead, through the
/// App Group's shared-links inbox, and needs no server at all.
final class ShareViewController: UIViewController {
    private let model = ShareModel()

    override func viewDidLoad() {
        super.viewDidLoad()
        model.close = { [weak self] in self?.extensionContext?.completeRequest(returningItems: nil) }
        let host = UIHostingController(rootView: ShareView(model: model))
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)
        let items = extensionContext?.inputItems.compactMap { $0 as? NSExtensionItem } ?? []
        Task { await model.run(items) }
    }
}

@MainActor
final class ShareModel: ObservableObject {
    enum State: Equatable {
        case working
        case finished(String, imported: Bool)
    }

    @Published var state = State.working
    var close: () -> Void = {}

    func run(_ items: [NSExtensionItem]) async {
        let text = await Self.sharedText(items)
        guard let link = FicImportLink.find(in: text) else {
            if let video = YouTubeLink.find(in: text) { return addToDraft(video) }
            return finish("That isn’t a link to a fic or a YouTube video. Lunaschal imports fics from \(FicImportLink.siteNames.joined(separator: ", ")).",
                          imported: false)
        }
        guard let session = SharedSignIn.read() else {
            return finish("Open Lunaschal and sign in to your server first.", imported: false)
        }
        do {
            // Sharing is asking for it now, so cellular is fine.
            let api = try JournalAPI(server: session.server, token: session.token, allowCellular: true)
            finish(try await api.importFic(link.url).summary(site: link.site), imported: true)
        } catch let error as URLError where error.isUnreachable {
            guard let root = SharedSignIn.importOutboxRoot(),
                  (try? FicImportOutbox(root: root).append(link)) != nil else {
                return finish(error.localizedDescription, imported: false)
            }
            finish("The server can’t be reached, so the \(link.site) link is saved. Lunaschal imports it on its next sync.",
                   imported: true)
        } catch {
            finish(error.localizedDescription, imported: false)
        }
    }

    /// The link waits in the App Group until Lunaschal next opens, then joins
    /// the draft's YouTube links; whatever the draft already holds is kept.
    private func addToDraft(_ video: String) {
        guard let root = SharedSignIn.sharedLinksRoot() else {
            return finish("Lunaschal can’t receive shared links in this build.", imported: false)
        }
        do {
            try SharedLinkInbox(root: root).append(video)
            finish("Added to your journal draft. It’s in Capture next time you open Lunaschal.", imported: true)
        } catch {
            finish(error.localizedDescription, imported: false)
        }
    }

    private func finish(_ text: String, imported: Bool) {
        state = .finished(text, imported: imported)
        guard imported else { return }
        Task {
            try? await Task.sleep(nanoseconds: 1_800_000_000)
            close()
        }
    }

    /// Everything shared, as text: URLs first, then plain text and the
    /// item's own text, since some apps share a link only inside a sentence.
    private static func sharedText(_ items: [NSExtensionItem]) async -> String {
        var parts: [String] = []
        for item in items {
            for provider in item.attachments ?? [] {
                if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
                   let value = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier) {
                    if let url = value as? URL { parts.append(url.absoluteString) }
                    else if let data = value as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) { parts.append(url.absoluteString) }
                    else if let string = value as? String { parts.append(string) }
                } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier),
                          let value = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier),
                          let string = value as? String {
                    parts.append(string)
                }
            }
            if let text = item.attributedContentText?.string { parts.append(text) }
        }
        return parts.joined(separator: "\n")
    }
}

struct ShareView: View {
    @ObservedObject var model: ShareModel

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                switch model.state {
                case .working:
                    ProgressView("Working…")
                case let .finished(text, imported):
                    Image(systemName: imported ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .font(.system(size: 44))
                        .foregroundStyle(imported ? .green : .orange)
                    Text(text).multilineTextAlignment(.center)
                }
            }
            .padding()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .navigationTitle("Share to Lunaschal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { model.close() } }
            }
        }
    }
}
