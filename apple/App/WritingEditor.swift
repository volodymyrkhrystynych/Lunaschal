import LunaschalCore
import SwiftUI
import UIKit

/// One open chapter or note. The text on screen is written to WritingStore every
/// moment someone stops typing and then offered to the server, so nothing about
/// the network decides whether it is kept.
@MainActor
final class WritingEditorModel: ObservableObject {
    /// Longest a keystroke sits only in memory.
    static let persistDelay: Duration = .seconds(0.4)
    /// How long typing has to stop before the server is told.
    static let syncDelay: Duration = .seconds(1.5)

    let key: WritingDocumentKey
    let projectID: String
    let writing: WritingModel
    let dictation: Dictation

    @Published private(set) var document: WritingDocument?
    @Published var title = ""
    @Published private(set) var text = ""
    @Published private(set) var words = 0
    @Published private(set) var waitingClips = 0
    /// The document was deleted and the author has dealt with it.
    @Published private(set) var gone = false
    /// A highlighted passage the author asked to talk through.
    @Published var discussing: WritingPassageRequest?
    @Published var problem: String?

    weak var textView: UITextView?
    private(set) var cursor = 0
    private var persistedText = ""
    private var persistedTitle = ""
    private var persistTask: Task<Void, Never>?
    private var syncTask: Task<Void, Never>?

    private var store: WritingStore { writing.store }
    private var clips: WritingDictationStore { writing.capture.writingClips }

    init(writing: WritingModel, document: WritingDocument) {
        self.writing = writing
        key = document.key
        projectID = document.projectId
        dictation = Dictation(capture: writing.capture)
        adopt(store.document(key, project: projectID) ?? document)
        cursor = self.document?.cursor ?? 0
        waitingClips = clips.clips(for: key).count
    }

    var hasUnsavedTyping: Bool { text != persistedText || title != persistedTitle }

    var status: String {
        if document?.hold != nil { return "Needs a decision" }
        if hasUnsavedTyping || document?.isDirty == true { return "On this phone" }
        return "Saved"
    }

    private func adopt(_ latest: WritingDocument) {
        document = latest
        title = latest.title
        text = latest.content
        persistedTitle = latest.title
        persistedText = latest.content
        words = WritingStats.words(latest.content)
    }

    func textChanged(_ value: String) {
        guard value != text else { return }
        if waitingClips > 0 {
            let edit = WritingTextEdit.between(text, value)
            try? clips.shift(key, location: edit.location, removed: edit.removed, inserted: edit.inserted)
        }
        text = value
        scheduleSave()
    }

    func titleChanged() {
        guard title != persistedTitle else { return }
        scheduleSave()
    }

    func cursorMoved(to location: Int) { cursor = location }

    /// Opens a discussion about the selected words, with everything typed so
    /// far on disk first so the discussion is about the text on screen.
    func discuss(_ passage: String) {
        persist()
        discussing = WritingPassageRequest(passage: passage)
    }

    /// The selection as a passage, or nil when there is nothing (or too much)
    /// to discuss. Only a chapter's prose is rewritten this way, not a note.
    func passage(in view: UITextView) -> String? {
        guard key.kind == .chapter else { return nil }
        let range = view.selectedRange
        return WritingPassage.selection(in: view.text ?? "", location: range.location, length: range.length)
    }

    private func scheduleSave() {
        persistTask?.cancel()
        persistTask = Task {
            try? await Task.sleep(for: Self.persistDelay)
            guard !Task.isCancelled else { return }
            persist()
        }
        syncTask?.cancel()
        syncTask = Task {
            try? await Task.sleep(for: Self.syncDelay)
            guard !Task.isCancelled else { return }
            await sync()
        }
    }

    /// Puts what is on screen on disk. Called on every pause, and whenever the
    /// screen or the app might be going away.
    func persist() {
        persistTask?.cancel()
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            if hasUnsavedTyping {
                // An emptied title isn't a title; the old one stands.
                document = try store.edit(key, project: projectID, title: trimmed.isEmpty ? nil : trimmed, content: text)
                persistedText = text
                persistedTitle = title
                words = WritingStats.words(text)
            }
            if cursor != document?.cursor { try store.setCursor(key, project: projectID, cursor) }
            problem = nil
        } catch {
            problem = "Couldn't save on this phone: \(error.localizedDescription)"
        }
    }

    func sync() async {
        persist()
        await writing.capture.pushWriting()
        reload()
    }

    /// Takes in what a sync or a pass changed, unless there is typing newer
    /// than it. Then the next save decides, and the server can refuse it.
    func reload() {
        guard let latest = store.document(key, project: projectID) else {
            if document != nil, clips.clips(for: key).isEmpty { gone = true }
            return
        }
        waitingClips = clips.clips(for: key).count
        if hasUnsavedTyping { document = latest; return }
        adopt(latest)
    }

    func restored() { adopt(store.document(key, project: projectID) ?? document!) }

    /// Puts a clip's words into the text, where the view can undo them, and on
    /// disk before returning, since the audio is deleted next.
    func insertDictation(_ clip: WritingClip, _ spoken: String) throws {
        let insertion = clip.anchor.inserting(spoken, into: text)
        if let view = textView,
           let start = view.position(from: view.beginningOfDocument, offset: insertion.location),
           let range = view.textRange(from: start, to: start) {
            // Reaches textChanged through the delegate, which moves any later clips.
            view.replace(range, withText: insertion.inserted)
        } else {
            textChanged(insertion.text)
        }
        persist()
        if let problem { throw WritingStoreFailure(message: problem) }
        waitingClips = max(0, clips.clips(for: key).count - 1)
    }

    /// Stopping keeps the clip, so it lands where the cursor is at that moment.
    func toggleDictation() {
        Task {
            await dictation.toggle { [weak self] url in self?.clipRecorded(url) }
            if let error = dictation.error { problem = error }
        }
    }

    private func clipRecorded(_ url: URL) {
        do {
            persist()
            try clips.add(url, key: key, projectId: projectID, anchor: DictationAnchor(offset: cursor))
            waitingClips = clips.clips(for: key).count
            Task {
                await writing.capture.pushWriting()
                reload()
            }
        } catch { problem = error.localizedDescription }
    }

    func resolve(_ resolution: WritingResolution) {
        persist()
        do {
            try store.resolve(key, project: projectID, resolution)
            writing.reloadDocuments()
            if let latest = store.document(key, project: projectID) { adopt(latest) } else { gone = true }
            Task { await sync() }
        } catch { problem = error.localizedDescription }
    }
}

struct WritingPassageRequest: Identifiable {
    let id = UUID()
    let passage: String
}

private struct WritingStoreFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Text editing for long documents: undo, find, a keyboard bar, a cursor that can
/// be put back, and a place for spoken words to land. SwiftUI's TextEditor offers
/// none of those, and exposes neither the selection nor the view.
struct WritingTextEditor: UIViewRepresentable {
    @ObservedObject var model: WritingEditorModel
    let serif: Bool
    let isRecording: Bool

    func makeCoordinator() -> Coordinator { Coordinator(model) }

    func makeUIView(context: Context) -> WritingUITextView {
        let view = WritingUITextView()
        view.delegate = context.coordinator
        view.backgroundColor = .clear
        var font = UIFont.preferredFont(forTextStyle: .body)
        if serif, let descriptor = font.fontDescriptor.withDesign(.serif) {
            font = UIFont(descriptor: descriptor, size: 0)
        }
        view.font = UIFontMetrics(forTextStyle: .body).scaledFont(for: font)
        view.adjustsFontForContentSizeCategory = true
        view.isFindInteractionEnabled = true
        view.alwaysBounceVertical = true
        view.keyboardDismissMode = .interactive
        view.accessibilityIdentifier = "writing-editor"
        view.onSave = { [weak model] in Task { await model?.sync() } }
        view.text = model.text
        view.inputAccessoryView = context.coordinator.makeBar(for: view)
        model.textView = view
        let cursor = min(model.cursor, view.text.utf16.count)
        view.selectedRange = NSRange(location: cursor, length: 0)
        DispatchQueue.main.async { view.scrollRangeToVisible(view.selectedRange) }
        return view
    }

    func updateUIView(_ view: WritingUITextView, context: Context) {
        context.coordinator.model = model
        if view.text != model.text {
            // Only a change that came from elsewhere: a sync, or a restore.
            let selection = view.selectedRange
            view.text = model.text
            view.selectedRange = NSRange(location: min(selection.location, view.text.utf16.count), length: 0)
        }
        context.coordinator.update(words: model.words, isRecording: isRecording)
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var model: WritingEditorModel
        private weak var mic: UIBarButtonItem?
        private weak var discuss: UIBarButtonItem?
        private weak var count: UILabel?
        private var shownRecording = false

        init(_ model: WritingEditorModel) { self.model = model }

        func textViewDidChange(_ textView: UITextView) {
            let value = textView.text ?? ""
            let cursor = textView.selectedRange.location
            MainActor.assumeIsolated {
                model.cursorMoved(to: cursor)
                model.textChanged(value)
            }
        }

        func textViewDidChangeSelection(_ textView: UITextView) {
            let cursor = textView.selectedRange.location
            MainActor.assumeIsolated {
                model.cursorMoved(to: cursor)
                // Only when it changes: see `update` for why the bar is touched sparingly.
                let canDiscuss = model.passage(in: textView) != nil
                if discuss?.isEnabled != canDiscuss { discuss?.isEnabled = canDiscuss }
            }
        }

        /// "Discuss rewrite" beside Cut/Copy/Paste for a selected passage.
        func textView(_ textView: UITextView, editMenuForTextIn range: NSRange,
                      suggestedActions: [UIMenuElement]) -> UIMenu? {
            MainActor.assumeIsolated {
                guard let passage = model.passage(in: textView) else { return nil }
                let discuss = UIAction(title: "Discuss rewrite", image: UIImage(systemName: "text.bubble")) {
                    [weak self] _ in MainActor.assumeIsolated { self?.model.discuss(passage) }
                }
                return UIMenu(children: [discuss] + suggestedActions)
            }
        }

        /// What the keyboard bar's button does: the same as the menu item.
        func discussSelection(in textView: UITextView) {
            MainActor.assumeIsolated {
                guard let passage = model.passage(in: textView) else { return }
                textView.resignFirstResponder()
                model.discuss(passage)
            }
        }

        func makeBar(for view: UITextView) -> UIToolbar {
            let bar = UIToolbar(frame: CGRect(x: 0, y: 0, width: 320, height: 44))
            bar.sizeToFit()
            func button(_ image: String, _ label: String, _ action: @escaping () -> Void) -> UIBarButtonItem {
                let item = UIBarButtonItem(image: UIImage(systemName: image), primaryAction: UIAction { _ in action() })
                item.accessibilityLabel = label
                return item
            }
            let undo = button("arrow.uturn.backward", "Undo") { [weak view] in view?.undoManager?.undo() }
            let redo = button("arrow.uturn.forward", "Redo") { [weak view] in view?.undoManager?.redo() }
            let mic = button("mic", "Dictate") { [weak self] in
                MainActor.assumeIsolated { self?.model.toggleDictation() }
            }
            mic.accessibilityIdentifier = "writing-dictate"
            let label = UILabel()
            label.font = .monospacedDigitSystemFont(ofSize: 13, weight: .regular)
            label.textColor = .secondaryLabel
            label.accessibilityIdentifier = "writing-word-count"
            let done = button("keyboard.chevron.compact.down", "Hide keyboard") { [weak view] in view?.resignFirstResponder() }
            var items = [undo, redo, mic]
            if MainActor.assumeIsolated({ model.key.kind == .chapter }) {
                let discuss = button("text.bubble", "Discuss rewrite") { [weak self, weak view] in
                    if let view { self?.discussSelection(in: view) }
                }
                discuss.accessibilityIdentifier = "writing-discuss-selection"
                discuss.isEnabled = false
                items.append(discuss)
                self.discuss = discuss
            }
            bar.items = items + [.flexibleSpace(), UIBarButtonItem(customView: label), .flexibleSpace(), done]
            self.mic = mic
            count = label
            return bar
        }

        /// Touches the bar only when something changed. A bar item that is set
        /// again, even to the same thing, makes the bar update, which brought
        /// SwiftUI back here: the app spun at full CPU and stopped responding.
        func update(words: Int, isRecording: Bool) {
            let wordCount = "\(words.formatted()) \(words == 1 ? "word" : "words")"
            if count?.text != wordCount {
                count?.text = wordCount
                count?.sizeToFit()
            }
            guard isRecording != shownRecording else { return }
            shownRecording = isRecording
            mic?.image = UIImage(systemName: isRecording ? "stop.circle.fill" : "mic")
            mic?.tintColor = isRecording ? .systemRed : nil
            mic?.accessibilityLabel = isRecording ? "Stop dictating" : "Dictate"
        }
    }
}

final class WritingUITextView: UITextView {
    private static let readableWidth: CGFloat = 720
    var onSave: (() -> Void)?

    /// Long lines are hard to read, so a wide window gets margins, not wider text.
    override func layoutSubviews() {
        super.layoutSubviews()
        let side = max(16, (bounds.width - Self.readableWidth) / 2)
        if textContainerInset.left != side {
            textContainerInset = UIEdgeInsets(top: 12, left: side, bottom: 40, right: side)
        }
    }

    override var keyCommands: [UIKeyCommand]? {
        [UIKeyCommand(title: "Save", action: #selector(saveNow), input: "s", modifierFlags: .command)]
    }

    @objc private func saveNow() { onSave?() }
}

struct WritingEditorView: View {
    @ObservedObject var writing: WritingModel
    @StateObject private var model: WritingEditorModel
    @State private var showHistory = false
    @State private var showConflict = false
    @State private var showPanel = false
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss

    init(writing: WritingModel, document: WritingDocument) {
        self.writing = writing
        _model = StateObject(wrappedValue: WritingEditorModel(writing: writing, document: document))
    }

    private var isChapter: Bool { model.key.kind == .chapter }

    var body: some View {
        VStack(spacing: 0) {
            header
            if model.document?.hold != nil {
                Button { showConflict = true } label: {
                    Label(conflictSummary, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.bordered).tint(.orange).padding(.horizontal)
                .accessibilityIdentifier("writing-conflict")
            }
            if model.waitingClips > 0 {
                Label(model.waitingClips == 1 ? "A clip is waiting to be transcribed"
                                              : "\(model.waitingClips) clips are waiting to be transcribed",
                      systemImage: "waveform")
                    .font(.caption).foregroundStyle(.secondary).padding(.vertical, 4)
                    .accessibilityIdentifier("writing-clips-waiting")
            }
            if let problem = model.problem ?? writing.problem {
                Text(problem).font(.caption).foregroundStyle(.red).padding(.horizontal)
            }
            WritingTextEditor(model: model, serif: isChapter, isRecording: model.dictation.status == .recording)
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(model.status).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("writing-status")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { showPanel.toggle() } label: { Label("Notes and discussion", systemImage: "sidebar.right") }
                    .accessibilityIdentifier("writing-panel")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button { model.persist(); showHistory = true } label: {
                    Label("History", systemImage: "clock.arrow.circlepath")
                }
            }
        }
        .inspector(isPresented: $showPanel) {
            WritingSidePanel(writing: writing, editing: model.key, project: model.projectID)
                .inspectorColumnWidth(min: 300, ideal: 360)
        }
        .sheet(isPresented: $showHistory) {
            NavigationStack {
                WritingHistoryView(writing: writing, key: model.key, project: model.projectID) {
                    model.restored()
                    showHistory = false
                }
            }
        }
        .sheet(isPresented: $showConflict) {
            NavigationStack {
                WritingConflictView(model: model) { showConflict = false }
            }
        }
        // Accepting a rewrite there changes this chapter, so take it in on the way back.
        .sheet(item: $model.discussing, onDismiss: { model.reload() }) { request in
            if let chapter = model.document {
                WritingPassageSheet(writing: writing, chapter: chapter, passage: request.passage)
            }
        }
        .onAppear {
            writing.capture.openWritingEditor = model
            Task {
                await writing.capture.pushWriting(pull: true)
                model.reload()
            }
        }
        // Anything not yet on disk is flushed rather than dropped when the screen goes.
        .onDisappear {
            if model.dictation.status == .recording { model.dictation.stop() }
            model.persist()
            if writing.capture.openWritingEditor === model { writing.capture.openWritingEditor = nil }
            writing.reloadDocuments()
            Task { await writing.capture.pushWriting() }
        }
        .onChange(of: scenePhase) { _, phase in if phase != .active { model.persist() } }
        .onChange(of: writing.capture.writingTick) { _, _ in model.reload() }
        .onChange(of: model.gone) { _, gone in if gone { dismiss() } }
    }

    private var conflictSummary: String {
        if case .deleted = model.document?.hold { return "Deleted on another device. Your text is safe here." }
        return "Changed on another device. Your text is safe here."
    }

    private var header: some View {
        VStack(spacing: 4) {
            TextField("Title", text: $model.title)
                .font(.headline)
                .submitLabel(.next)
                .onChange(of: model.title) { _, _ in model.titleChanged() }
                .accessibilityIdentifier("writing-title")
            if let document = model.document, !isChapter {
                HStack {
                    Picker("Type", selection: Binding(
                        get: { document.docType },
                        set: { type in Task { await writing.setDocType(document, type); model.reload() } }
                    )) {
                        ForEach(WritingDocType.all, id: \.id) { Text($0.label).tag($0.id) }
                    }
                    Toggle("Pinned", isOn: Binding(
                        get: { document.pinned },
                        set: { on in Task { await writing.setPinned(document, on); model.reload() } }
                    ))
                    .toggleStyle(.button)
                    .accessibilityIdentifier("writing-note-pin")
                    Spacer()
                }
            }
        }
        .padding(.horizontal).padding(.vertical, 6)
    }
}

/// Two versions of a document, and the three ways out. Nothing is decided
/// without the author: both texts are on the phone until one of these is tapped.
private struct WritingConflictView: View {
    @ObservedObject var model: WritingEditorModel
    let onDone: () -> Void

    private var latest: WritingServerCopy? {
        if case let .changed(copy) = model.document?.hold { return copy }
        return nil
    }

    var body: some View {
        List {
            Section("On this phone") {
                Text(model.text.isEmpty ? "(empty)" : model.text).textSelection(.enabled)
                    .accessibilityIdentifier("writing-conflict-mine")
            }
            if let latest {
                Section("On the server") {
                    Text(latest.content.isEmpty ? "(empty)" : latest.content).textSelection(.enabled)
                        .accessibilityIdentifier("writing-conflict-theirs")
                }
                Section {
                    Button("Keep mine") { finish(.keepMine) }
                        .accessibilityIdentifier("writing-conflict-keep")
                    Button("Save mine as a copy") { finish(.saveCopy) }
                        .accessibilityIdentifier("writing-conflict-copy")
                    Button("Use the server's", role: .destructive) { finish(.useLatest) }
                        .accessibilityIdentifier("writing-conflict-latest")
                } footer: {
                    Text("Keeping yours puts the server's version in this document's history. Using the server's discards the text on this phone.")
                }
            } else {
                Section {
                    Button("Save as new") { finish(.saveCopy) }
                        .accessibilityIdentifier("writing-conflict-copy")
                    Button("Discard mine", role: .destructive) { finish(.useLatest) }
                } footer: {
                    Text("This was deleted on another device.")
                }
            }
        }
        .navigationTitle("Two versions")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Later") { onDone() } } }
    }

    private func finish(_ resolution: WritingResolution) {
        model.resolve(resolution)
        onDone()
    }
}

private struct WritingHistoryView: View {
    @ObservedObject var writing: WritingModel
    let key: WritingDocumentKey
    let project: String
    let onRestored: () -> Void
    @State private var revisions: [WritingNoteRevision]?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            if let revisions {
                if revisions.isEmpty { Text("No earlier versions yet.").foregroundStyle(.secondary) }
                ForEach(revisions) { revision in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(revision.label).font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button("Restore") {
                                Task {
                                    await writing.capture.pushWriting()
                                    if await writing.restore(key, project: project, revision: revision) { onRestored() }
                                }
                            }
                            .buttonStyle(.bordered)
                        }
                        Text(revision.content.isEmpty ? "(empty)" : revision.content).lineLimit(5)
                    }
                }
            } else {
                ProgressView()
            }
        }
        .navigationTitle("History")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        .task { revisions = await writing.revisions(key) }
    }
}

/// What sits beside the text: a note to look at, or the discussion about this
/// chapter. An inspector is a column on a wide iPad and a sheet on an iPhone, so
/// this one view is both.
private struct WritingSidePanel: View {
    enum Tab: String, CaseIterable, Identifiable {
        case notes = "Notes", discussion = "Discussion"
        var id: Self { self }
    }

    @ObservedObject var writing: WritingModel
    let editing: WritingDocumentKey
    let project: String
    @State private var tab = Tab.notes
    @State private var discussion: WritingDiscussion?

    private var notes: [WritingDocument] {
        writing.notes(project).filter { $0.key != editing }.sorted { $0.pinned && !$1.pinned }
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if editing.kind == .chapter {
                    Picker("Panel", selection: $tab) {
                        ForEach(Tab.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented).padding(.horizontal).padding(.vertical, 6)
                }
                switch tab {
                case .notes: notesList
                case .discussion: discussionPane
                }
            }
            .navigationBarTitleDisplayMode(.inline)
        }
        .task { await writing.load(project: project) }
    }

    private var notesList: some View {
        List(notes) { note in
            NavigationLink {
                ScrollView {
                    Text(note.content.isEmpty ? "(empty)" : note.content).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding()
                }
                .navigationTitle(note.title)
            } label: {
                HStack {
                    Text(note.title)
                    Spacer()
                    if note.pinned {
                        Image(systemName: "pin.fill").font(.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Pinned")
                    }
                }
            }
            .accessibilityIdentifier("writing-panel-note")
        }
        .overlay { if notes.isEmpty { Text("No notes yet").foregroundStyle(.secondary) } }
    }

    @ViewBuilder private var discussionPane: some View {
        if let discussion {
            WritingDiscussionView(model: WritingDiscussionModel(id: discussion.id, writing: writing),
                                  title: discussion.displayTitle)
                .id(discussion.id)
        } else {
            VStack(spacing: 8) {
                ProgressView()
                if let problem = writing.problem { Text(problem).font(.caption).foregroundStyle(.secondary) }
            }
            .frame(maxHeight: .infinity)
            .task { discussion = await writing.discussion(forChapter: editing.id, project: project) }
        }
    }
}

/// The discussion about one highlighted passage: made when the sheet opens, and
/// afterwards in the book's Discussions like any other, titled by its first words.
private struct WritingPassageSheet: View {
    @ObservedObject var writing: WritingModel
    let chapter: WritingDocument
    let passage: String
    @State private var discussion: WritingDiscussion?
    @State private var failed = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if let discussion {
                    WritingDiscussionView(model: WritingDiscussionModel(id: discussion.id, writing: writing),
                                          title: discussion.displayTitle, passage: passage)
                } else {
                    VStack(spacing: 12) {
                        if failed {
                            Text(writing.problem ?? "Couldn't start the discussion.").foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                            Button("Try again") { Task { await start() } }
                        } else {
                            ProgressView()
                        }
                    }
                    .padding().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
        }
        .task { if discussion == nil { await start() } }
    }

    private func start() async {
        failed = false
        discussion = await writing.passageDiscussion(chapter: chapter, passage: passage)
        failed = discussion == nil
    }
}
