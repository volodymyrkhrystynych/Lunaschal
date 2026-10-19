import SwiftUI
import LunaschalCore

/// Entries that are only photos and sounds, waiting for a word about what
/// was happening. The server picks the day's batch and remembers dismissals,
/// so every client shows the same few.
@MainActor
final class JournalContextModel: ObservableObject {
    @Published private(set) var prompts: [JournalContextPrompt] = []
    @Published private(set) var problem: String?
    @Published private(set) var loaded = false
    let capture: CaptureModel

    init(capture: CaptureModel) { self.capture = capture }

    private var transport: (any JournalContextTransport)? {
        #if DEBUG
        if let fixture = JournalContextFixture.server { return fixture }
        #endif
        return capture.chatAPI()
    }

    func refresh() async {
        guard let transport else {
            problem = capture.server == nil ? "Connect to your server in Settings to see entries waiting for context."
                                            : "Sign in again in Settings to see entries waiting for context."
            loaded = true
            return
        }
        do {
            let fresh = try await transport.journalContextPrompts()
            // An entry with recordings already queued for it has its answer.
            prompts = fresh.filter { capture.pendingAdditions(to: $0.id).isEmpty }
            problem = nil
        } catch {
            if Task.isCancelled { return }
            problem = "Couldn't refresh the entries waiting for context."
        }
        loaded = true
    }

    func dismiss(_ prompt: JournalContextPrompt) async -> String? {
        guard let transport else { return "Connect to your server to dismiss this prompt." }
        do {
            try await transport.resolveJournalContext(prompt.id, resolution: .init(action: .dismiss))
            prompts.removeAll { $0.id == prompt.id }
            return nil
        } catch { return error.localizedDescription }
    }

    func save(_ prompt: JournalContextPrompt, text: String) async -> String? {
        let content = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Text first: a fast transcript must not race the context write. The
        // clip stays in its durable draft if this request fails.
        if !content.isEmpty {
            guard let transport else { return "Connect to your server to save typed context. Your recordings remain on this device." }
            do {
                try await transport.resolveJournalContext(prompt.id, resolution: .init(action: .save, content: content))
            } catch { return error.localizedDescription }
        }
        guard capture.commitJournalContextClips(prompt.id) else { return capture.message ?? "Couldn't save the recording." }
        prompts.removeAll { $0.id == prompt.id }
        return nil
    }
}

/// Journal → Context: the third page of the Journal tab, beside Calendar.
struct JournalContextPage: View {
    @ObservedObject var context: JournalContextModel
    @ObservedObject var capture: CaptureModel
    /// Chat's voice message holds the microphone too.
    @ObservedObject var chat: ChatModel
    @State private var opened: JournalContextPrompt?
    @State private var busy: String?
    @State private var problem: String?

    var body: some View {
        List {
            if let error = problem ?? context.problem {
                Section {
                    Text(error).font(.footnote).foregroundStyle(.red)
                    Button("Retry") { Task { await context.refresh() } }
                }
            }
            if !context.prompts.isEmpty {
                Section {
                    ForEach(context.prompts) { prompt in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(prompt.displayTitle).font(.subheadline.bold())
                            if let date = JournalTimestamp.parse(prompt.createdAt) {
                                Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                            }
                            Text(summary(prompt)).font(.caption).foregroundStyle(.secondary)
                            ViewThatFits(in: .horizontal) {
                                HStack { actions(prompt) }
                                VStack(alignment: .leading) { actions(prompt) }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                } footer: {
                    Text("Photos and recordings saved without a word about them. What you add goes into the original entry; its date stays the same.")
                }
            } else if context.loaded && context.problem == nil {
                ContentUnavailableView("Nothing waiting", systemImage: "checkmark.circle",
                    description: Text("Photos and recordings saved without any words show up here, a few a day."))
            }
        }
        .refreshable { await context.refresh() }
        .task { await context.refresh() }
        .onChange(of: capture.syncChanges) { _, changes in
            if changes.touches(["journal_entries", "journal_attachments"]) { Task { await context.refresh() } }
        }
        .sheet(item: $opened) { prompt in
            JournalContextSheet(context: context, capture: capture, recorder: capture.recorder,
                                prompt: prompt, otherRecording: chat.isRecording)
        }
    }

    private func summary(_ prompt: JournalContextPrompt) -> String {
        let photos = prompt.media.filter { $0.media == .image }.count
        let clips = prompt.media.filter { $0.media == .audio }.count
        return [photos > 0 ? "\(photos) photo\(photos == 1 ? "" : "s")" : nil,
                clips > 0 ? "\(clips) recording\(clips == 1 ? "" : "s")" : nil]
            .compactMap { $0 }.joined(separator: " · ")
    }

    @ViewBuilder private func actions(_ prompt: JournalContextPrompt) -> some View {
        Button("Dismiss permanently") {
            busy = prompt.id
            Task { problem = await context.dismiss(prompt); busy = nil }
        }
        .buttonStyle(.borderless)
        .disabled(busy != nil)
        .accessibilityIdentifier("journal-context-dismiss-\(prompt.id)")
        Spacer()
        Button("Add context") { problem = nil; opened = prompt }
            .buttonStyle(.borderedProminent)
            .accessibilityIdentifier("journal-context-open-\(prompt.id)")
    }
}

private struct JournalContextSheet: View {
    @ObservedObject var context: JournalContextModel
    @ObservedObject var capture: CaptureModel
    @ObservedObject var recorder: Recorder
    let prompt: JournalContextPrompt
    var otherRecording = false
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var saving = false
    @State private var problem: String?

    private var draft: CaptureDraft { capture.entryDrafts[prompt.id] ?? CaptureDraft() }
    private var recordingHere: Bool {
        guard let id = recorder.activeID else { return false }
        return draft.clips.contains { $0.attachmentID == id }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(prompt.displayTitle).font(.headline)
                    if let date = JournalTimestamp.parse(prompt.createdAt) {
                        Text(date.formatted(date: .abbreviated, time: .shortened)).foregroundStyle(.secondary)
                    }
                    let photos = prompt.media.filter { $0.media == .image }
                    if !photos.isEmpty { PhotoStrip(model: capture, photos: photos) }
                    ForEach(prompt.media.filter { $0.media == .audio }) { item in
                        AudioRow(model: capture, item: item)
                    }
                }
                Section("What was happening?") {
                    TextEditor(text: $text).frame(minHeight: 120)
                        .accessibilityIdentifier("journal-context-text")
                    Button {
                        if recordingHere { recorder.stop() }
                        else { Task { await recorder.startClip(transcribe: true, into: prompt.id) } }
                    } label: {
                        Label(recordingHere ? "Stop" : recorder.isStarting ? "Starting…" : "Transcribe",
                              systemImage: recordingHere ? "stop.fill" : "mic")
                    }
                    .tint(recordingHere ? .red : .accentColor)
                    .disabled(saving || recorder.isStarting || otherRecording || (recorder.activeID != nil && !recordingHere))
                    .accessibilityIdentifier("journal-context-record")
                    Text("Recordings are attached and transcribed into this entry. Its original date stays the same.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if !draft.isEmpty {
                    Section("Recordings to add") {
                        StagedAttachmentRows(model: capture, recorder: recorder, draft: draft)
                    }
                }
                if let error = problem ?? capture.message {
                    Section { Text(error).foregroundStyle(.red).accessibilityIdentifier("journal-context-error") }
                }
                Section {
                    Button(saving ? "Saving…" : "Save context") {
                        if recordingHere { recorder.stop() }
                        saving = true
                        Task {
                            problem = await context.save(prompt, text: text)
                            saving = false
                            if problem == nil { dismiss() }
                        }
                    }
                    .disabled(saving || recorder.isStarting || (text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && draft.isEmpty))
                    .accessibilityIdentifier("journal-context-save")
                }
            }
            .navigationTitle("Add journal context")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        if capture.commitJournalContextClips(prompt.id) { dismiss() }
                        else { problem = capture.message }
                    }
                    .disabled(saving || recorder.isStarting)
                }
            }
            // Close explicitly commits stored clips; a swipe must not bypass it.
            .interactiveDismissDisabled()
        }
    }
}

#if DEBUG
/// Simulator-only stand-in: exercises the real cards and request payloads
/// without requiring a running server or modifying the user's journal.
enum JournalContextFixture {
    static let server: JournalContextDemoServer? = ProcessInfo.processInfo.arguments.contains("-journalContextFixture")
        ? JournalContextDemoServer() : nil
}

actor JournalContextDemoServer: JournalContextTransport {
    private var prompts: [JournalContextPrompt] = (try? JSONDecoder().decode([JournalContextPrompt].self, from: Data("""
    [{"id":"01K00000000000000000000001","title":"Game night","createdAt":"2026-10-13T20:00:00Z",
      "attachments":[{"id":"01K00000000000000000000003","kind":"image","name":"Victory screen","mime":"image/jpeg","position":0}]},
     {"id":"01K00000000000000000000002","title":"Evening sounds","createdAt":"2026-10-12T20:00:00Z",
      "attachments":[{"id":"01K00000000000000000000004","kind":"audio","name":"Game audio","mime":"audio/webm","position":0}]}]
    """.replacingOccurrences(of: "01K00000000000000000000002",
        with: ProcessInfo.processInfo.environment["JOURNAL_CONTEXT_RECORDING_ID"] ?? "01K00000000000000000000002").utf8))) ?? []

    func journalContextPrompts() -> [JournalContextPrompt] { prompts }
    func resolveJournalContext(_ id: String, resolution: JournalContextResolution) throws {
        if resolution.action == .save && resolution.content == "fail" { throw ChatError.server("Could not save context. Try again.") }
        prompts.removeAll { $0.id == id }
    }
}
#endif
