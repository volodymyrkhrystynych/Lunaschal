import SwiftUI
import AVFoundation
import PhotosUI
import UniformTypeIdentifiers
import LunaschalCore

struct CaptureRoot: View {
    @ObservedObject var model: CaptureModel
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        TabView {
            NavigationStack { CaptureComposer(model: model, recorder: model.recorder) }
                .tabItem { Label("Capture", systemImage: "square.and.pencil") }
            NavigationStack { CaptureList(model: model) }
                .tabItem { Label("Journal", systemImage: "book.closed") }
            NavigationStack { LibraryView(model: model) }
                .tabItem { Label("Library", systemImage: "books.vertical") }
            if UIDevice.current.userInterfaceIdiom == .pad {
                NavigationStack { StudyLibraryView(model: model) }
                    .tabItem { Label("Study", systemImage: "doc.text") }
                NavigationStack { DrawingLibraryView(model: model) }
                    .tabItem { Label("Draw", systemImage: "pencil.tip") }
            }
            NavigationStack { ConnectionSettings(model: model) }
                .tabItem { Label("Settings", systemImage: "gear") }
        }
        .alert("Lunaschal", isPresented: Binding(get: { model.message != nil }, set: { if !$0 { model.message = nil } })) {
            Button("OK") { model.message = nil }
        } message: { Text(model.message ?? "") }
        .task(id: scenePhase) {
            guard scenePhase == .active else { model.leaveForeground(); return }
            while !Task.isCancelled {
                model.requestSync()
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
            }
        }
    }
}

private struct CaptureComposer: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var recorder: Recorder
    // Preserve an unfinished draft across app termination: text and links
    // here, recordings, photos and files in the capture store's draft.
    @AppStorage("journalDraft") private var text = ""
    @AppStorage("youtubeDraftLinks") private var draftLinks = ""
    @AppStorage("youtubeDraftURL") private var youtubeURL = ""
    @State private var saved = false
    @State private var showCamera = false
    @State private var showFiles = false
    @State private var photoItems: [PhotosPickerItem] = []
    @FocusState private var typing: Bool

    private var links: [String] { draftLinks.split(separator: "\n").map(String.init) }
    private var typedURL: String { youtubeURL.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canSave: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !links.isEmpty || !model.draft.isEmpty || !typedURL.isEmpty
    }

    var body: some View {
        Form {
            Section {
                TextEditor(text: $text).frame(minHeight: 160).accessibilityLabel("Journal text")
                    .focused($typing)
                actions
                if saved { Text("Saved on this device").foregroundStyle(.secondary) }
            }
            if !model.draft.isEmpty {
                Section("Attachments") {
                    ForEach(model.draft.clips) { clip in
                        ClipRow(clip: clip, url: try? model.store.clipURL(clip),
                                recording: recorder.activeID == clip.attachmentID)
                    }
                    .onDelete { offsets in offsets.map { model.draft.clips[$0] }.forEach(model.discard) }
                    ForEach(model.draft.files) { file in StagedFileRow(model: model, file: file) }
                        .onDelete { offsets in offsets.map { model.draft.files[$0] }.forEach(model.discard) }
                }
            }
            Section("YouTube") {
                ForEach(links, id: \.self) { link in
                    Label(link, systemImage: "play.rectangle").lineLimit(1).truncationMode(.middle)
                }
                .onDelete { offsets in
                    var kept = links
                    kept.remove(atOffsets: offsets)
                    draftLinks = kept.joined(separator: "\n")
                }
                HStack {
                    TextField("YouTube video URL", text: $youtubeURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                        .focused($typing)
                        .onSubmit { _ = addTypedLink() }
                    Button("Add link") { _ = addTypedLink() }.disabled(typedURL.isEmpty)
                }
            }
        }
        // Pinned rather than inside a section, so it stays in the same place
        // however far the form has scrolled.
        .safeAreaInset(edge: .bottom, alignment: .trailing) {
            Button { save() } label: { Label("Save entry", systemImage: "checkmark") }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .disabled(!canSave)
                .padding()
        }
        .navigationTitle("Capture")
        .onChange(of: text) { _, value in if !value.isEmpty { saved = false } }
        .fullScreenCover(isPresented: $showCamera) {
            CameraPicker { image in
                guard let data = image.jpegData(compressionQuality: 0.9) else { return }
                model.stage { try $0.stageFile(data: data, name: photoName("jpg"), contentType: "image/jpeg") }
                saved = false
            }.ignoresSafeArea()
        }
        .fileImporter(isPresented: $showFiles, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
            guard case .success(let urls) = result else { return }
            for url in urls {
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                model.stage { try $0.stageFile(from: url, name: url.lastPathComponent, contentType: type) }
                saved = false
            }
        }
        .onChange(of: photoItems) { _, items in
            guard !items.isEmpty else { return }
            photoItems = []
            Task { for item in items { await stagePhoto(item) } }
        }
    }

    /// One line of icon buttons: the two microphone modes, then the three ways
    /// to attach something. While recording, the line becomes the Stop button,
    /// which keeps the clip in the draft rather than saving an entry.
    @ViewBuilder private var actions: some View {
        if recorder.activeID != nil {
            Button(role: .destructive) { recorder.stop() } label: {
                Label("Stop recording", systemImage: "stop.circle.fill")
            }
        } else {
            HStack {
                Button { saved = false; Task { await recorder.startClip(transcribe: true) } } label: {
                    Label("Transcribe", systemImage: "mic")
                }.disabled(recorder.isStarting)
                Spacer()
                Button { saved = false; Task { await recorder.startClip(transcribe: false) } } label: {
                    Label("Record", systemImage: "record.circle")
                }.disabled(recorder.isStarting)
                Spacer()
                Button { typing = false; showCamera = true } label: {
                    Label("Take photo", systemImage: "camera")
                }.disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
                Spacer()
                PhotosPicker(selection: $photoItems, matching: .images) {
                    Label("Choose photo", systemImage: "photo.on.rectangle")
                }
                Spacer()
                Button { typing = false; showFiles = true } label: {
                    Label("Attach file", systemImage: "paperclip")
                }
            }
            .labelStyle(.iconOnly).font(.title2)
            // Without this a tap anywhere on the row fires every button in it.
            .buttonStyle(.borderless)
            .overlay { if recorder.isStarting { ProgressView() } }
        }
    }

    private func stagePhoto(_ item: PhotosPickerItem) async {
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else { return }
            let type = item.supportedContentTypes.first { $0.conforms(to: .image) }
            model.stage { try $0.stageFile(data: data, name: photoName(type?.preferredFilenameExtension ?? "jpg"),
                                           contentType: type?.preferredMIMEType) }
            saved = false
        } catch { model.message = error.localizedDescription }
    }

    private func photoName(_ ext: String) -> String {
        "Photo \(Date().formatted(.iso8601.year().month().day().time(includingFractionalSeconds: false))).\(ext)"
    }

    /// Validates the typed URL and moves it into the entry's link list.
    private func addTypedLink() -> Bool {
        guard !typedURL.isEmpty else { return true }
        do {
            let link = try YouTubeLink.canonical(typedURL)
            if !links.contains(link) { draftLinks = (links + [link]).joined(separator: "\n") }
            youtubeURL = ""
            saved = false
            return true
        } catch { model.message = error.localizedDescription; return false }
    }

    private func save() {
        // A URL typed but not yet added still belongs to this entry.
        guard addTypedLink() else { return }
        if model.saveEntry(text, youtubeURLs: links) {
            text = ""; draftLinks = ""; saved = true; typing = false
        }
    }
}

private struct ClipRow: View {
    let clip: CaptureClip
    let url: URL?
    let recording: Bool

    var body: some View {
        HStack {
            Image(systemName: clip.transcribe ? "mic" : "record.circle").frame(width: 44, height: 44)
            VStack(alignment: .leading) {
                Text(clip.transcribe ? "Transcription" : "Recording")
                Text(detail).font(.caption).foregroundStyle(clip.state == .interrupted ? .orange : .secondary)
            }
        }
        .foregroundStyle(recording ? .red : .primary)
    }

    private var detail: String {
        if recording { return "Recording…" }
        let time = clip.createdAt.formatted(date: .omitted, time: .shortened)
        let length = url.flatMap { try? AVAudioPlayer(contentsOf: $0).duration }.flatMap { seconds in
            seconds > 0 ? Duration.seconds(seconds).formatted(.time(pattern: .minuteSecond)) : nil
        }
        let base = [time, length].compactMap { $0 }.joined(separator: " · ")
        return clip.state == .interrupted ? base + " · Interrupted — check before saving" : base
    }
}

private struct StagedFileRow: View {
    @ObservedObject var model: CaptureModel
    let file: CaptureFile

    var body: some View {
        HStack {
            if file.isImage, let url = try? model.store.fileURL(file), let image = UIImage(contentsOfFile: url.path) {
                Image(uiImage: image).resizable().scaledToFill()
                    .frame(width: 44, height: 44).clipShape(RoundedRectangle(cornerRadius: 6))
            } else {
                Image(systemName: "doc").frame(width: 44, height: 44)
            }
            Text(file.name).lineLimit(1).truncationMode(.middle)
        }
    }
}

/// The system camera. iOS has no SwiftUI camera, so this wraps UIKit's.
private struct CameraPicker: UIViewControllerRepresentable {
    let onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(_ parent: CameraPicker) { self.parent = parent }

        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let image = info[.originalImage] as? UIImage { parent.onImage(image) }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}

private struct CaptureList: View {
    @ObservedObject var model: CaptureModel
    @State private var query = ""

    private var captures: [Capture] { model.captures.filter { $0.matchesSearch(query) } }

    var body: some View {
        List {
            if !model.pendingEdits.isEmpty {
                Section("Pending edits") {
                    ForEach(model.pendingEdits) { edit in
                        NavigationLink { PendingEditView(model: model, edit: edit) } label: {
                            VStack(alignment: .leading) {
                                Text(edit.original.title).lineLimit(1)
                                Text(edit.state == "pending" ? "Saved on device · Waiting to sync" : "Needs resolution")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            Section {
                ForEach(captures) { capture in
                    NavigationLink {
                        CaptureDetail(model: model, id: capture.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(capture.snapshot?.title ?? (capture.text.isEmpty ? (capture.files.first?.name ?? "Recording") : capture.text))
                                .lineLimit(2)
                            Text(capture.createdAt, format: .dateTime.month().day().hour().minute())
                                .font(.caption).foregroundStyle(.secondary)
                            Text(status(capture)).font(.caption)
                        }
                    }
                }
            } header: { Text("Captured on this device") }
            Section("Server journal · Available offline") {
                ForEach(model.journalRecords) { record in
                    NavigationLink { JournalRecordView(model: model, record: record) } label: {
                        VStack(alignment: .leading) {
                            Text(record.title).lineLimit(2)
                            Text(record.data?["createdAt"]?.string ?? "").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if model.journalRecords.count < model.journalCount {
                    Button("Load more entries (\(model.journalRecords.count) of \(model.journalCount))") { model.loadMoreJournal() }
                }
            }
        }
        .overlay {
            if captures.isEmpty && model.journalRecords.isEmpty && model.pendingEdits.isEmpty {
                ContentUnavailableView(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "No captures yet" : "No matching entries", systemImage: "book.closed")
            }
        }
        .navigationTitle("Journal")
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search saved journal")
        .onAppear { model.searchJournal(query) }
        .onChange(of: query) { _, value in model.searchJournal(value) }
        .toolbar {
            if model.syncing { ProgressView() }
            else { Button("Sync") { model.requestSync(manual: true) }.disabled(!model.signedIn) }
        }
    }
}

private func status(_ item: Capture) -> String {
    switch item.state {
    case .recording: return "Recording on this device"
    case .interrupted: return "Interrupted — review recording"
    case .pending: return "Saved on device · Waiting to sync"
    case .failed: return "Saved on device · Upload needs attention"
    case .synced: return "Synced · Original kept on device"
    }
}

private struct CaptureDetail: View {
    @ObservedObject var model: CaptureModel
    let id: String

    var body: some View {
        if let capture = model.captures.first(where: { $0.id == id }) {
            List {
                Section {
                    Text(capture.createdAt, format: .dateTime)
                    Text(status(capture))
                    if let error = capture.lastError { Text(error).foregroundStyle(.orange) }
                }
                if let snapshot = capture.snapshot, !snapshot.content.isEmpty {
                    Section("Journal entry") { Text(snapshot.content).textSelection(.enabled) }
                }
                if !capture.text.isEmpty {
                    Section("Original text") { Text(capture.text).textSelection(.enabled) }
                }
                if let raw = capture.snapshot?.rawContent, !raw.isEmpty, raw != capture.text {
                    Section("Server original text") { Text(raw).textSelection(.enabled) }
                }
                if let transcript = capture.recordingTranscript?.transcript, !transcript.isEmpty {
                    Section("Recording transcript") { Text(transcript).textSelection(.enabled) }
                }
                ForEach(capture.clips) { clip in
                    if let url = try? model.store.clipURL(clip) {
                        Section(clip.transcribe ? "Transcription audio" : "Recording") {
                            AudioPreview(url: url, recordingActive: model.recorder.activeID != nil)
                        }
                    }
                }
                if !capture.files.isEmpty {
                    Section("Attachments") {
                        ForEach(capture.files) { file in StagedFileRow(model: model, file: file) }
                    }
                }
                if !capture.links.isEmpty {
                    Section(capture.links.count == 1 ? "Saved YouTube link" : "Saved YouTube links") {
                        ForEach(capture.links, id: \.attachmentID) { link in
                            if let url = URL(string: link.url) { Link(link.url, destination: url) }
                        }
                    }
                }
                if capture.attachmentID != nil, capture.state != .recording,
                   let url = try? model.store.audioURL(capture) {
                    Section("Original recording") {
                        AudioPreview(url: url, recordingActive: model.recorder.activeID != nil)
                        ShareLink("Export audio", item: url)
                        if capture.mode == .transcribe {
                            Text("Transcription: \(capture.recordingTranscript?.transcriptStatus ?? "waiting for server")")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                if capture.state == .interrupted || capture.state == .failed {
                    Button(capture.state == .interrupted ? "Keep recovered recording and sync" : "Retry upload") {
                        model.retry(capture)
                    }
                }
            }
            .navigationTitle(capture.snapshot?.title ?? "Capture")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

private struct AudioPreview: View {
    let url: URL
    let recordingActive: Bool
    @State private var player: AVAudioPlayer?
    @State private var error: String?

    var body: some View {
        Button("Play audio") {
            do {
                try AVAudioSession.sharedInstance().setCategory(.playback)
                try AVAudioSession.sharedInstance().setActive(true)
                let audio = try AVAudioPlayer(contentsOf: url)
                guard audio.play() else { throw CaptureError.missingAudio }
                player = audio
                error = nil
            } catch { self.error = error.localizedDescription }
        }.disabled(recordingActive)
        Button("Stop playback") { player?.stop(); player = nil }
        if let error { Text(error).foregroundStyle(.orange) }
        Color.clear.frame(height: 0).onDisappear { player?.stop() }
    }
}

private struct ConnectionSettings: View {
    @ObservedObject var model: CaptureModel
    @AppStorage("allowCellularSync") private var allowCellular = true
    @AppStorage("backgroundSyncEnabled") private var backgroundSyncEnabled = true
    @State private var address = ""
    @State private var password = ""
    @State private var code = ""

    var body: some View {
        Form {
            Section {
                NavigationLink("Library downloads") { LibraryDownloadSettings(model: model) }
            }
            Section("Server") {
                if let message = model.syncMessage { Text(message).foregroundStyle(.secondary) }
                TextField("https://server.tailnet.ts.net", text: $address)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    .disabled(model.server != nil || model.signingIn)
                if model.signedIn {
                    Label("Signed in", systemImage: "checkmark.circle")
                    Button("Sign out") { model.signOut() }
                } else {
                    SecureField("Password", text: $password).textContentType(.password)
                    TextField("Display code", text: $code).keyboardType(.numberPad).textContentType(.oneTimeCode)
                    Button("Sign in") {
                        Task {
                            if await model.login(address: address, password: password, code: code) {
                                password = ""; code = ""
                            }
                        }
                    }.disabled(model.signingIn || password.isEmpty || code.isEmpty)
                    if model.signingIn { ProgressView() }
                }
                Text("Use your existing Tailscale HTTPS address and the display code from Lunaschal Settings. Captures stay linked to this server after signing out.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Transfers") {
                Toggle("Background sync", isOn: $backgroundSyncEnabled)
                    .onChange(of: backgroundSyncEnabled) { _, _ in model.backgroundPreferenceChanged() }
                Text(model.backgroundStatus).font(.footnote).foregroundStyle(.secondary)
                Text("iOS decides when background sync runs. Opening the app syncs sooner.")
                    .font(.footnote).foregroundStyle(.secondary)
                Toggle("Allow cellular sync", isOn: $allowCellular)
                    .onChange(of: allowCellular) { _, _ in model.cancelSync() }
                Text("Applies to journal text, audio, and chapter updates after your first library download. Bulk library downloads use Wi-Fi only.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            IntelligenceAvailabilityView()
        }
        .navigationTitle("Settings")
        .onAppear { address = model.server?.absoluteString ?? address }
    }
}
