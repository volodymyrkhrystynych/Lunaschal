import SwiftUI
import AVFoundation
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
                NavigationStack { LibraryCategoryView(model: model, category: .documents, title: "Study") }
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
    // Preserve an unfinished typed draft across app termination as well.
    @AppStorage("journalDraft") private var text = ""
    @AppStorage("youtubeDraftURL") private var youtubeURL = ""
    @AppStorage("youtubeDraftCommentary") private var commentary = ""
    @State private var saved = false

    var body: some View {
        Form {
            Section("Write") {
                TextEditor(text: $text).frame(minHeight: 160).accessibilityLabel("Journal text")
                Button("Save entry") {
                    if model.saveText(text) { text = ""; saved = true }
                }.disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if saved { Text("Saved on this device").foregroundStyle(.secondary) }
            }
            Section {
                if recorder.activeID != nil {
                    Label("Recording…", systemImage: "waveform").foregroundStyle(.red)
                    Button("Stop and save", role: .destructive) { recorder.stop() }
                } else {
                    Button { Task { await recorder.start(mode: .transcribe) } } label: {
                        Label("Transcribe", systemImage: "mic")
                    }.disabled(recorder.isStarting)
                    Button { Task { await recorder.start(mode: .record) } } label: {
                        Label("Record", systemImage: "record.circle")
                    }.disabled(recorder.isStarting)
                    if recorder.isStarting { ProgressView("Starting microphone…") }
                }
            } header: { Text("Speak") } footer: {
                Text("Stopping saves a separate journal entry with the original audio. Transcribe also asks the server to add the words when connected.")
            }
            Section {
                Text(model.signedIn
                     ? (model.backgroundSyncEnabled ? "Captures sync while open and when iOS grants background time." : "Captures sync while the app is open.")
                     : "Capture works offline. Sign in under Settings to sync.")
                    .foregroundStyle(.secondary)
                if let message = model.syncMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
            }
            Section {
                TextField("YouTube video URL", text: $youtubeURL)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                TextField("Your thoughts (optional)", text: $commentary, axis: .vertical)
                Button("Save link and thoughts") {
                    if model.saveLink(youtubeURL, commentary: commentary) { youtubeURL = ""; commentary = ""; saved = true }
                }.disabled(youtubeURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            } header: { Text("YouTube") } footer: {
                Text("The link and your thoughts are saved offline. The server imports the video when connected; archive playback stays on the server.")
            }
        }
        .navigationTitle("Capture")
        .onChange(of: text) { _, value in if !value.isEmpty { saved = false } }
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
                            Text(capture.snapshot?.title ?? (capture.text.isEmpty ? "Recording" : capture.text))
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
                if let link = capture.youtubeURL, let url = URL(string: link) {
                    Section("Saved YouTube link") { Link(link, destination: url) }
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
