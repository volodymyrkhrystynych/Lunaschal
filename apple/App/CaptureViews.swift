import SwiftUI
import AVFoundation
import CoreLocation
import PhotosUI
import UniformTypeIdentifiers
import LunaschalCore

struct CaptureRoot: View {
    @ObservedObject var model: CaptureModel
    @StateObject private var chat: ChatModel
    @StateObject private var todo: TodoModel
    @StateObject private var learning: LearningModel
    @StateObject private var context: JournalContextModel
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var widthClass

    /// Every destination gets a tab of its own only on an iPad window wide
    /// enough for the floating tab bar, which pages through them. A compact one
    /// (Split View, Slide Over, a narrow Stage Manager window) has the iPhone's
    /// five-slot bottom bar, and the rest went into iPadOS's own More list —
    /// which nests each tab's NavigationStack inside its navigation controller:
    /// two bars, two back buttons. So a compact iPad gets the iPhone's tabs.
    private var everyDestinationIsATab: Bool {
        UIDevice.current.userInterfaceIdiom == .pad && widthClass != .compact
    }

    init(model: CaptureModel) {
        self.model = model
        _chat = StateObject(wrappedValue: ChatModel(capture: model))
        _todo = StateObject(wrappedValue: TodoModel(capture: model))
        _learning = StateObject(wrappedValue: LearningModel(capture: model))
        _context = StateObject(wrappedValue: JournalContextModel(capture: model))
    }

    var body: some View {
        TabView {
            NavigationStack { CaptureTab(model: model) }
                .tabItem { Label("Capture", systemImage: "square.and.pencil") }
            NavigationStack { JournalTab(model: model, context: context, chat: chat) }
                .tabItem { Label("Journal", systemImage: "book.closed") }
            NavigationStack { ChatView(chat: chat, capture: model) }
                .tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
            NavigationStack { TodoView(todo: todo) }
                .tabItem { Label("Todo", systemImage: "checklist") }
                // Red, like a notification: to-dos due today or overdue.
                .badge(todo.dueCount)
            if everyDestinationIsATab {
                NavigationStack { StudyLibraryView(model: model) }
                    .tabItem { Label("Study", systemImage: "doc.text") }
                NavigationStack { DrawingLibraryView(model: model) }
                    .tabItem { Label("Draw", systemImage: "pencil.tip") }
                NavigationStack { LibraryView(model: model) }
                    .tabItem { Label("Library", systemImage: "books.vertical") }
                NavigationStack { JobsFeedView(capture: model) }
                    .tabItem { Label("Jobs", systemImage: "briefcase") }
                NavigationStack { WritingRootView(capture: model) }
                    .tabItem { Label("Writing", systemImage: "text.book.closed") }
                NavigationStack { LearningView(learning: learning) }
                    .tabItem { Label("Learning", systemImage: "graduationcap") }
                    .badge(learning.stats.due)
                NavigationStack { ConnectionSettings(model: model) }
                    .tabItem { Label("Settings", systemImage: "gear") }
            } else {
                NavigationStack {
                    MoreMenu(model: model, learning: learning,
                             showsStudyAndDraw: UIDevice.current.userInterfaceIdiom == .pad)
                }
                    .tabItem { Label("More", systemImage: "line.3.horizontal") }
            }
        }
        .alert("Lunaschal", isPresented: Binding(get: { model.message != nil }, set: { if !$0 { model.message = nil } })) {
            Button("OK") { model.message = nil }
        } message: { Text(model.message ?? "") }
        // The Todo badge stays current, but neither list is fetched again for a
        // pass that didn't touch it.
        .onChange(of: model.syncChanges) { _, changes in
            if changes.touches([SyncChanges.todos]) { Task { await todo.refresh() } }
            if changes.full { Task { await learning.refresh() } }
        }
        .task(id: scenePhase) {
            guard scenePhase == .active else { model.leaveForeground(); return }
            model.takeSharedLinks()
            model.resumeFicDownloads()
            // Everything once on the way in, then one small question every
            // 30 seconds: has the server anything new? Only what it names is pulled.
            model.syncOnForeground()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                model.checkIn()
            }
        }
    }
}

/// The Capture tab: a switch in place of its title between the entry
/// composer, which it opens on, and the Daily log.
private struct CaptureTab: View {
    enum Page: String, CaseIterable, Identifiable {
        case entry = "Entry", daily = "Daily", workout = "Workout"
        var id: Self { self }
    }

    @ObservedObject var model: CaptureModel
    @State private var page = Page.entry
    @State private var notebook: Notebook?
    @State private var fetchingPaper = false

    var body: some View {
        // All three stay built and only the chosen one shows: building a page
        // at the moment of the tap stalled the switch's slide into a snap.
        ZStack {
            KeptPage(shown: page == .entry) {
                CaptureComposer(model: model, recorder: model.recorder) { openNotes() }
            }
            KeptPage(shown: page == .daily) { DailyView(model: model) }
            KeptPage(shown: page == .workout) { WorkoutView(model: model) }
        }
        .ignoresSafeArea()
        // The pages stay built, so their own onAppear no longer marks opening one.
        .onChange(of: page) { _, page in if page != .entry { model.requestSync() } }
        // Still titled for VoiceOver and the back button; the switch is what shows.
        .navigationTitle("Capture")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { model.location.refresh() }
        .toolbar {
            // On every page, so the switch beside it never shifts.
            ToolbarItem(placement: .topBarLeading) { CurrentWeatherButton(weather: model.weather) }
            ToolbarItem(placement: .principal) {
                Picker("Capture page", selection: $page) {
                    ForEach(Page.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .fixedSize()
            }
            // The notes canvas wants a Pencil and room, so it is the iPad's.
            if UIDevice.current.userInterfaceIdiom == .pad {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button { Task { await openNewspaper() } } label: {
                        if fetchingPaper { ProgressView() } else { Label("Newspaper", systemImage: "newspaper") }
                    }
                    .disabled(fetchingPaper)
                    .accessibilityIdentifier("capture-newspaper")
                    Button { openNotes() } label: { Label("Notes", systemImage: "pencil.and.scribble") }
                        .accessibilityIdentifier("capture-notes")
                }
            }
        }
        .navigationDestination(item: $notebook) { NotebookEditor(owner: model, notebook: $0) }
        // Back from a notebook: the composer's notes row shows what was drawn.
        .onChange(of: notebook) { _, shown in if shown == nil { model.reloadCaptures() } }
    }

    /// The entry draft's own notes, so Back and Notes again find them as left.
    private func openNotes() {
        notebook = model.openDraftNotes()
    }

    /// Today's paper by the 4am day, downloaded first if it isn't archived
    /// yet. Before 4am the day is yesterday's, and a missing paper stays missing.
    private func openNewspaper() async {
        fetchingPaper = true
        defer { fetchingPaper = false }
        switch NewspaperIssue.choose(await model.newspaperIssues()) {
        case .today(let issue): await open(issue)
        case .download(let date):
            if let issue = await model.downloadNewspaper(date: date) { await open(issue) }
        case .missing(let date):
            model.message = "Today's paper (\(date)) doesn't exist. Downloads open at 4 am."
        }
    }

    private func open(_ issue: NewspaperIssue) async {
        fetchingPaper = true
        defer { fetchingPaper = false }
        if let opened = await model.openNewspaper(issue) { notebook = opened }
    }
}

/// A page that stays built while another is showing. A hidden page is taken
/// out of the window rather than made transparent: SwiftUI's opacity, and even
/// UIKit's `isHidden`, leave its rows readable to VoiceOver. Its controller,
/// and so its state, lives on, and putting it back is cheap.
private struct KeptPage<Content: View>: UIViewControllerRepresentable {
    let shown: Bool
    @ViewBuilder let content: Content

    final class Container: UIViewController {
        let host: UIHostingController<Content>
        init(_ content: Content) {
            host = UIHostingController(rootView: content)
            super.init(nibName: nil, bundle: nil)
            addChild(host)
            host.didMove(toParent: self)
        }
        required init?(coder: NSCoder) { fatalError("not used") }

        func show(_ shown: Bool) {
            view.isUserInteractionEnabled = shown
            if shown, host.view.superview == nil {
                host.view.frame = view.bounds
                host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
                host.view.backgroundColor = .clear
                view.addSubview(host.view)
            } else if !shown, host.view.superview != nil {
                host.view.removeFromSuperview()
            }
        }
    }

    func makeUIViewController(context: Context) -> Container {
        let container = Container(content)
        container.view.backgroundColor = .clear
        return container
    }

    func updateUIViewController(_ container: Container, context: Context) {
        container.host.rootView = content
        container.show(shown)
    }
}

private struct CaptureComposer: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var recorder: Recorder
    /// Opens the draft's notes (the iPad's Notes button does the same).
    var openNotes: () -> Void = {}
    // Preserve an unfinished draft across app termination: text and links
    // here, recordings, photos, files and the notes in the capture store's draft.
    @AppStorage(CaptureModel.draftTextKey) private var text = ""
    @AppStorage(CaptureModel.draftLinksKey) private var draftLinks = ""
    @AppStorage(CaptureModel.draftURLKey) private var youtubeURL = ""
    @State private var saved = false
    @State private var saving = false
    @State private var confirmingDiscard = false
    @FocusState private var typing: Bool
    @State private var typingText = false

    private var links: [String] { draftLinks.split(separator: "\n").map(String.init) }
    private var linkList: Binding<[String]> {
        Binding(get: { links }, set: { draftLinks = $0.joined(separator: "\n") })
    }
    private var typedURL: String { youtubeURL.trimmingCharacters(in: .whitespacesAndNewlines) }
    /// The notes going with this entry, if any were opened for it.
    private var notes: Notebook? { model.draftNotebook }
    private var canSave: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || !links.isEmpty || !model.draft.isEmpty || !typedURL.isEmpty
            || !(notes?.markedPages.isEmpty ?? true) || notes?.youtubeURL != nil
    }
    /// Anything at all to lose: the draft is kept until saved or discarded.
    private var hasDraft: Bool { !text.isEmpty || canSave }
    /// A meal needs words, a photo or a clip; YouTube links stay behind for
    /// the next journal entry, and a non-media file cannot go to the food log.
    private var canSaveFood: Bool {
        (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.draft.isEmpty)
            && model.draft.files.allSatisfy(\.isFoodMedia)
    }
    /// A purchase is the meal's shape with no videos: a receipt photo, a clip
    /// about it, or a few words ("coffee with Sam, 4.50").
    private var canSaveSpending: Bool {
        (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.draft.isEmpty)
            && model.draft.files.allSatisfy(\.isSpendingMedia)
    }

    var body: some View {
        Form {
            Section {
                CaptureEntryEditor(text: $text, focused: $typingText) { image in
                    guard let data = image.pngData() else { return }
                    model.stage { try $0.stageFile(data: data, name: "Pasted image \(ULID.make()).png", contentType: "image/png") }
                    saved = false
                }
                .frame(minHeight: 160)
                AttachmentButtons(model: model, recorder: recorder) { saved = false; typing = false; typingText = false }
                if saved { Text("Saved on this device").foregroundStyle(.secondary) }
            }
            if !model.draft.isEmpty || notes != nil {
                Section("Attachments") {
                    if let notes { DraftNotesRow(model: model, notebook: notes, open: openNotes) }
                    StagedAttachmentRows(model: model, recorder: recorder, draft: model.draft)
                }
            }
            YouTubeLinksSection(model: model, links: linkList, typed: $youtubeURL, typing: $typing) { saved = false }
        }
        // Pinned rather than inside a section, so it stays in the same place
        // however far the form has scrolled.
        .safeAreaInset(edge: .bottom) {
            HStack {
                // Icons only, so all four fit on a phone; the titles stay as
                // their accessibility labels. Discard asks before clearing anything.
                Button(role: .destructive) { confirmingDiscard = true } label: {
                    Label("Discard draft", systemImage: "trash")
                }
                .buttonStyle(.glass)
                .disabled(!hasDraft || recorder.activeID != nil)
                Spacer()
                // Glass rather than a plain tint: the form scrolls under this
                // bar, and an unblurred button prints the rows through itself.
                Button { saveFood() } label: { Label("Food", systemImage: "fork.knife") }
                    .buttonStyle(.glass)
                    .disabled(!canSaveFood)
                    .accessibilityHint(model.draft.files.allSatisfy(\.isFoodMedia) ? ""
                        : "Food entries hold photos, videos and recordings only.")
                Spacer()
                Button { saveSpending() } label: { Label("Spent", systemImage: "banknote") }
                    .buttonStyle(.glass)
                    .disabled(!canSaveSpending)
                    .accessibilityLabel("Save spending")
                    .accessibilityHint(model.draft.files.allSatisfy(\.isSpendingMedia) ? ""
                        : "Spending entries hold photos and recordings only.")
                Spacer()
                Button { save() } label: { Label("Save", systemImage: "checkmark") }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canSave || saving)
            }
            .labelStyle(.iconOnly)
            .controlSize(.large)
            .padding()
        }
        .onChange(of: text) { _, value in if !value.isEmpty { saved = false } }
        .onChange(of: typing) { _, focused in if focused { typingText = false } }
        .onChange(of: typingText) { _, focused in if focused { typing = false } }
        .alert("Discard this draft?", isPresented: $confirmingDiscard) {
            Button("Discard", role: .destructive) { discard() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Its text, links, recordings, photos and files are removed from this device. This cannot be undone. Its notes stay in Draw.")
        }
    }

    private func discard() {
        guard model.discardComposerDraft() else { return }
        text = ""; draftLinks = ""; youtubeURL = ""; saved = false; typing = false; typingText = false
    }

    private func save() {
        // A URL typed but not yet added still belongs to this entry.
        guard YouTubeLinksSection.add(typed: $youtubeURL, to: linkList, model: model) else { return }
        // Read now, as tapped: the words must not wait behind rendering the notes.
        let words = text, chosen = links
        guard let notes else {
            if model.saveEntry(words, youtubeURLs: chosen) { cleared() }
            return
        }
        saving = true
        Task {
            defer { saving = false }
            guard await model.saveEntryWithNotes(words, youtubeURLs: chosen, notebook: notes) else { return }
            // Anything typed while the pages rendered is the next entry's.
            let more = text == words ? "" : text
            cleared()
            text = more
        }
    }

    private func cleared() {
        text = ""; draftLinks = ""; saved = true; typing = false; typingText = false
    }

    private func saveFood() {
        // Links, a half-typed URL and the notes are left where they are, for the next entry.
        if model.saveEntry(text, youtubeURLs: [], kind: .food) {
            text = ""; saved = true; typing = false; typingText = false
        }
    }

    /// The same as a meal, filed as a purchase: the server reads the amount,
    /// the merchant and the category out of the receipt and what was said.
    private func saveSpending() {
        if model.saveEntry(text, youtubeURLs: [], kind: .spending) {
            text = ""; saved = true; typing = false; typingText = false
        }
    }
}

/// The composer's one line of icon buttons: the two microphone modes, then the
/// three ways to attach something. While recording, the line becomes the Stop
/// button, which keeps the clip in the draft rather than saving anything.
///
/// Shared by the Capture tab (`entryID` nil, its own draft) and by editing a
/// journal entry or a meal, where everything lands in that entry's draft and
/// is sent only when the edit is saved. `kind` limits Attach file to what that
/// log keeps: pictures, videos and voice memos for a meal, no videos for a
/// purchase.
struct AttachmentButtons: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var recorder: Recorder
    var entryID: String? = nil
    var kind: CaptureKind = .journal
    /// Called when a button is used: the composer clears its "Saved" note and
    /// drops the keyboard.
    var onUse: () -> Void = {}
    @State private var showCamera = false
    @State private var showFiles = false
    @State private var photoItems: [PhotosPickerItem] = []

    private var importable: [UTType] {
        switch kind {
        case .journal: return [.item]
        case .food: return [.image, .movie, .audio]
        case .spending: return [.image, .audio]
        }
    }

    var body: some View {
        Group {
            if recorder.activeID != nil {
                // Where Record and Transcribe were: the thumb that started the
                // clip doesn't have to cross the row to stop it.
                HStack {
                    Spacer()
                    Button(role: .destructive) { recorder.stop() } label: {
                        Label("Stop recording", systemImage: "stop.circle.fill")
                    }
                }
                // A plain button in a list row claims the whole row.
                .buttonStyle(.borderless)
            } else {
                // Most-used last: the right end of the row is under the thumb.
                HStack {
                    Button { onUse(); showFiles = true } label: {
                        Label("Attach file", systemImage: "paperclip")
                    }
                    Spacer()
                    PhotosPicker(selection: $photoItems, matching: .images) {
                        Label("Choose photo", systemImage: "photo.on.rectangle")
                    }
                    Spacer()
                    // The camera hands back pixels without the photo's GPS,
                    // so a fresh fix is what places a meal. Asked for now so it
                    // has landed by the time the capture is saved.
                    Button { onUse(); model.location.refresh(); showCamera = true } label: {
                        Label("Take photo", systemImage: "camera")
                    }.disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
                    Spacer()
                    Button { onUse(); Task { await recorder.startClip(transcribe: false, into: entryID) } } label: {
                        Label("Record", systemImage: "record.circle")
                    }.disabled(recorder.isStarting)
                    Spacer()
                    Button { onUse(); Task { await recorder.startClip(transcribe: true, into: entryID) } } label: {
                        Label("Transcribe", systemImage: "mic")
                    }.disabled(recorder.isStarting)
                }
                .labelStyle(.iconOnly).font(.title2)
                // Without this a tap anywhere on the row fires every button in it.
                .buttonStyle(.borderless)
                .overlay { if recorder.isStarting { ProgressView() } }
            }
        }
        .fullScreenCover(isPresented: $showCamera) {
            CameraPicker { image in
                guard let data = image.jpegData(compressionQuality: 0.9) else { return }
                model.stage { try $0.stageFile(data: data, name: photoName("jpg"), contentType: "image/jpeg", into: entryID, capturedAt: Date()) }
                onUse()
            }.ignoresSafeArea()
        }
        .fileImporter(isPresented: $showFiles, allowedContentTypes: importable,
                      allowsMultipleSelection: true) { result in
            guard case .success(let urls) = result else { return }
            for url in urls {
                let scoped = url.startAccessingSecurityScopedResource()
                defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                let type = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                model.stage { try $0.stageFile(from: url, name: url.lastPathComponent, contentType: type, into: entryID) }
                onUse()
            }
        }
        .onChange(of: photoItems) { _, items in
            guard !items.isEmpty else { return }
            photoItems = []
            Task { for item in items { await stagePhoto(item) } }
        }
    }

    private func stagePhoto(_ item: PhotosPickerItem) async {
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else { return }
            let type = item.supportedContentTypes.first { $0.conforms(to: .image) }
            model.stage { try $0.stageFile(data: data, name: photoName(type?.preferredFilenameExtension ?? "jpg"),
                                           contentType: type?.preferredMIMEType, into: entryID) }
            onUse()
        } catch { model.message = error.localizedDescription }
    }

    private func photoName(_ ext: String) -> String {
        "Photo \(Date().formatted(.iso8601.year().month().day().time(includingFractionalSeconds: false))).\(ext)"
    }
}

/// A draft's clips and files, each removable with a swipe.
/// The notes drawn for the composer's entry: a tap goes back to them, and
/// removing the row takes them out of this entry (they stay in Draw).
private struct DraftNotesRow: View {
    @ObservedObject var model: CaptureModel
    let notebook: Notebook
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            HStack(spacing: 12) {
                if let url = try? model.notebooks.previewURL(notebook), let image = UIImage(contentsOfFile: url.path) {
                    Image(uiImage: image).resizable().scaledToFit().frame(width: 44, height: 60)
                        .background(.white).clipShape(RoundedRectangle(cornerRadius: 4))
                        .overlay { RoundedRectangle(cornerRadius: 4).stroke(.separator) }
                } else {
                    Image(systemName: "pencil.and.scribble").frame(width: 44, height: 60)
                }
                VStack(alignment: .leading) {
                    Text("Notes")
                    Text("\(notebook.pageCount) page\(notebook.pageCount == 1 ? "" : "s")")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .foregroundStyle(.primary)
        .accessibilityIdentifier("capture-draft-notes")
        .swipeActions {
            Button("Remove", role: .destructive) { model.detachDraftNotebook() }
        }
    }
}

struct StagedAttachmentRows: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var recorder: Recorder
    let draft: CaptureDraft

    var body: some View {
        ForEach(draft.clips) { clip in
            ClipRow(clip: clip, url: try? model.store.clipURL(clip),
                    recording: recorder.activeID == clip.attachmentID)
        }
        .onDelete { offsets in offsets.map { draft.clips[$0] }.forEach(model.discard) }
        ForEach(draft.files) { file in StagedFileRow(model: model, file: file) }
            .onDelete { offsets in offsets.map { draft.files[$0] }.forEach(model.discard) }
    }
}

/// Keep ordinary text editing while routing clipboard pictures to the draft.
struct CaptureEntryEditor: UIViewRepresentable {
    @Binding var text: String
    var focused: Binding<Bool>
    var onPasteImage: (UIImage) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> CaptureEntryTextView {
        let view = CaptureEntryTextView()
        view.delegate = context.coordinator
        view.backgroundColor = .clear
        view.font = .preferredFont(forTextStyle: .body)
        view.adjustsFontForContentSizeCategory = true
        view.accessibilityLabel = "Journal text"
        view.textContainerInset = UIEdgeInsets(top: 8, left: 0, bottom: 8, right: 0)
        view.onPasteImage = onPasteImage
        return view
    }

    func updateUIView(_ view: CaptureEntryTextView, context: Context) {
        context.coordinator.parent = self
        view.onPasteImage = onPasteImage
        if view.text != text { view.text = text }
        if focused.wrappedValue, !view.isFirstResponder { view.becomeFirstResponder() }
        if !focused.wrappedValue, view.isFirstResponder { view.resignFirstResponder() }
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: CaptureEntryEditor
        init(_ parent: CaptureEntryEditor) { self.parent = parent }
        func textViewDidChange(_ textView: UITextView) { parent.text = textView.text }
        func textViewDidBeginEditing(_ textView: UITextView) { parent.focused.wrappedValue = true }
        func textViewDidEndEditing(_ textView: UITextView) { parent.focused.wrappedValue = false }
    }
}

final class CaptureEntryTextView: UITextView {
    var onPasteImage: ((UIImage) -> Void)?

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        if action == #selector(paste(_:)), UIPasteboard.general.hasImages { return true }
        return super.canPerformAction(action, withSender: sender)
    }

    override func paste(_ sender: Any?) {
        let clipboard = UIPasteboard.general
        if clipboard.hasImages, let images = clipboard.images, !images.isEmpty, let onPasteImage {
            for image in images { onPasteImage(image) }
            return
        }
        super.paste(sender)
    }
}

/// The YouTube links going onto an entry, and the field a new one is typed in.
struct YouTubeLinksSection: View {
    @ObservedObject var model: CaptureModel
    @Binding var links: [String]
    @Binding var typed: String
    var typing: FocusState<Bool>.Binding
    var onChange: () -> Void = {}

    var body: some View {
        Section("YouTube") {
            ForEach(links, id: \.self) { link in
                Label(link, systemImage: "play.rectangle").lineLimit(1).truncationMode(.middle)
            }
            .onDelete { offsets in links.remove(atOffsets: offsets) }
            HStack {
                TextField("YouTube video URL", text: $typed)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    .focused(typing)
                    .onSubmit { if Self.add(typed: $typed, to: $links, model: model) { onChange() } }
                PasteButton(payloadType: String.self) { values in
                    guard let value = values.first else { return }
                    typed = value
                    if Self.add(typed: $typed, to: $links, model: model) { onChange() }
                }
                .labelStyle(.iconOnly)
                .accessibilityLabel("Paste and add YouTube link")
                .accessibilityIdentifier("youtube-paste")
                Button("Add link") { if Self.add(typed: $typed, to: $links, model: model) { onChange() } }
                    .disabled(typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }

    /// Validates the typed URL and moves it into the link list. False, with
    /// the reason shown, when it is not a YouTube video link.
    @MainActor
    static func add(typed: Binding<String>, to links: Binding<[String]>, model: CaptureModel) -> Bool {
        let value = typed.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return true }
        do {
            let link = try YouTubeLink.canonical(value)
            if !links.wrappedValue.contains(link) { links.wrappedValue.append(link) }
            typed.wrappedValue = ""
            return true
        } catch { model.message = error.localizedDescription; return false }
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
struct CameraPicker: UIViewControllerRepresentable {
    var front = false
    let onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        if front && UIImagePickerController.isCameraDeviceAvailable(.front) { picker.cameraDevice = .front }
        if front { picker.cameraFlashMode = .off }
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

/// The Journal tab: Sync on the left on every page, and a switch on the right
/// between the journal, the calendar, and the entries waiting for context.
private struct JournalTab: View {
    enum Page: String, CaseIterable, Identifiable {
        case journal = "Journal", calendar = "Calendar", context = "Context"
        var id: Self { self }
    }

    @ObservedObject var model: CaptureModel
    @ObservedObject var context: JournalContextModel
    // Not observed: Chat publishes on every refresh tick, and each one would
    // redraw the whole feed. Only the Context page watches it.
    let chat: ChatModel
    @SceneStorage("journalTabPage") private var page = Page.journal

    var body: some View {
        Group {
            switch page {
            case .journal: JournalFeedView(model: model)
            case .calendar: CalendarPage(model: model)
            case .context: JournalContextPage(context: context, capture: model, chat: chat)
            }
        }
        .onChange(of: page) { _, page in if page == .calendar { model.requestSync() } }
        .task { await context.refresh() }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                if model.syncing { ProgressView() }
                else { Button("Sync") { model.requestSync(manual: true) }.disabled(!model.signedIn) }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Picker("Journal page", selection: $page) {
                    // Icons: three words don't fit beside Sync on a phone, and the
                    // bar would fold the whole switch into its overflow menu.
                    ForEach(Page.allCases) { page in
                        Image(systemName: symbol(page)).tag(page)
                            .accessibilityLabel(page == .context && !context.prompts.isEmpty
                                                ? "\(page.rawValue) \(context.prompts.count)" : page.rawValue)
                    }
                }
                .pickerStyle(.segmented)
                .fixedSize()
                .accessibilityIdentifier("journal-page")
            }
        }
    }

    /// Context's bubble fills while anything is waiting, so the backlog is
    /// noticed from the other pages.
    private func symbol(_ page: Page) -> String {
        switch page {
        case .journal: return "book"
        case .calendar: return "calendar"
        case .context: return context.prompts.isEmpty ? "text.bubble" : "text.bubble.fill"
        }
    }
}

func captureStatus(_ item: Capture) -> String {
    switch item.state {
    case .recording: return "Recording on this device"
    case .interrupted: return "Interrupted — review recording"
    case .pending: return "Saved on device · Waiting to sync"
    case .failed: return "Saved on device · Upload needs attention"
    case .synced: return "Synced · Original kept on device"
    }
}

struct CaptureDetail: View {
    @ObservedObject var model: CaptureModel
    let id: String
    @State private var editing = false
    @State private var text = ""
    @FocusState private var typing: Bool

    var body: some View {
        if let capture = model.captures.first(where: { $0.id == id }) {
            List {
                if editing {
                    Section {
                        TextEditor(text: $text).frame(minHeight: 240).focused($typing)
                            .accessibilityLabel("Entry text")
                    }
                    Section {
                        Button("Save edit on this device") {
                            // Kept open on a refusal, so the words can still be copied out.
                            if model.editText(capture, to: text) { editing = false }
                        }
                        .disabled(text == capture.text
                            || (text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                && capture.files.isEmpty && capture.clips.isEmpty && capture.links.isEmpty))
                        Button("Cancel", role: .cancel) { editing = false }
                    }
                } else {
                    details(capture)
                }
            }
            .navigationTitle(capture.snapshot?.title ?? (capture.kind == .food ? "Meal" : capture.kind == .spending ? "Purchase" : "Capture"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // Only while the server certainly has none of it: a re-sent
                // entry is ignored there, so later words would be lost.
                if !editing && model.canEditText(capture) {
                    ToolbarItem(placement: .primaryAction) {
                        Button("Edit") { text = capture.text; editing = true; typing = true }
                    }
                }
            }
        }
    }

    @ViewBuilder private func details(_ capture: Capture) -> some View {
        Section {
            if capture.kind == .food { Label("Food log entry", systemImage: "fork.knife") }
            if capture.kind == .spending { Label("Spending entry", systemImage: "banknote") }
            Text(capture.createdAt, format: .dateTime)
            Text(captureStatus(capture))
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

// The last tab: everything that doesn't earn a slot of its own. The iPhone
// shows five tabs before iOS adds its own More overflow, so this owns that slot.
// A compact iPad window has the same five, and its iPad-only Study and Draw
// come in here too.
private struct MoreMenu: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var learning: LearningModel
    var showsStudyAndDraw = false

    var body: some View {
        List {
            if showsStudyAndDraw {
                NavigationLink { StudyLibraryView(model: model) } label: {
                    Label("Study", systemImage: "doc.text")
                }.accessibilityIdentifier("more-Study")
                NavigationLink { DrawingLibraryView(model: model) } label: {
                    Label("Draw", systemImage: "pencil.tip")
                }.accessibilityIdentifier("more-Draw")
            }
            NavigationLink { LibraryView(model: model) } label: {
                Label("Library", systemImage: "books.vertical")
            }.accessibilityIdentifier("more-Library")
            NavigationLink { JobsFeedView(capture: model) } label: {
                Label("Jobs", systemImage: "briefcase")
            }.accessibilityIdentifier("more-Jobs")
            NavigationLink { WritingRootView(capture: model) } label: {
                Label("Writing", systemImage: "text.book.closed")
            }.accessibilityIdentifier("more-Writing")
            NavigationLink { LearningView(learning: learning) } label: {
                Label("Learning", systemImage: "graduationcap")
            }
            // Cards due for review, as the desktop's Review button counts them.
            .badge(learning.stats.due)
            .accessibilityIdentifier("more-Learning")
            NavigationLink { PlacesScreen(model: model) } label: {
                Label("Places", systemImage: "mappin.and.ellipse")
            }.accessibilityIdentifier("more-Places")
            NavigationLink { ConnectionSettings(model: model) } label: {
                Label("Settings", systemImage: "gear")
            }.accessibilityIdentifier("more-Settings")
        }
        .navigationTitle("More")
    }
}

private struct ConnectionSettings: View {
    @ObservedObject var model: CaptureModel
    @AppStorage("allowCellularSync") private var allowCellular = true
    @AppStorage("backgroundSyncEnabled") private var backgroundSyncEnabled = true
    @AppStorage(LearningModel.speechModeKey) private var learningSpeechMode = false
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
                if let report = model.lastSyncReport {
                    Text(report).font(.footnote).monospacedDigit().foregroundStyle(.secondary)
                        .accessibilityIdentifier("last-sync-report")
                }
                Text("iOS decides when background sync runs. Opening the app syncs sooner.")
                    .font(.footnote).foregroundStyle(.secondary)
                Toggle("Allow cellular sync", isOn: $allowCellular)
                    .onChange(of: allowCellular) { _, _ in model.cancelSync() }
                Text("Applies to journal text, audio, and chapter updates after your first library download. Bulk library downloads use Wi-Fi only.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section("Learning") {
                Toggle("Speech mode", isOn: $learningSpeechMode)
                    .accessibilityIdentifier("settings-learning-speech")
                Text("Hear what you got wrong. For answers given while this is on, a short summary of what you missed is read aloud on the results, using your server's text-to-speech.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            LocationTrailSettings(model: model, trail: model.trail)
            Section {
                NavigationLink { PlacesScreen(model: model) } label: {
                    Label("Places, Home & Work", systemImage: "mappin.and.ellipse")
                }.accessibilityIdentifier("settings-places")
            } footer: {
                Text("Your Home and Works are watched as geofences while the trail is on, so the calendar's workday follows when you actually arrive and leave.")
            }
            if model.healthAvailable { HealthSettings(model: model) }
            IntelligenceAvailabilityView()
        }
        .navigationTitle("Settings")
        .onAppear { address = model.server?.absoluteString ?? address }
    }
}

/// The background trail behind the Journal's daily map. Most of this section
/// is about permission, because that is where it goes wrong: iOS asks for
/// Always at most once, and every later change has to be made in the Settings
/// app, so the section says plainly what's missing and links there.
private struct LocationTrailSettings: View {
    @ObservedObject var model: CaptureModel
    @ObservedObject var trail: LocationTracker
    @Environment(\.openURL) private var openURL

    var body: some View {
        Section("Location") {
            Toggle("Record where I go", isOn: Binding(
                get: { trail.enabled },
                set: { $0 ? trail.enable() : trail.disable() }))
                .accessibilityIdentifier("settings-location-trail")
            if trail.enabled {
                if let problem {
                    Text(problem).font(.footnote).foregroundStyle(.orange)
                    Button("Open iOS Settings") {
                        if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                    }
                } else {
                    Text(trail.mode == .fine ? "Recording your route now." : "Watching for walks and places, in low-power mode.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                let status = trail.status
                if let last = status.lastSuccess {
                    Text("Last uploaded \(last.formatted(date: .abbreviated, time: .shortened)) · \(trail.pending.formatted()) waiting")
                        .font(.footnote).foregroundStyle(.secondary)
                } else if trail.pending > 0 {
                    Text("\(trail.pending.formatted()) points waiting to upload.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if let error = status.lastError {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                if trail.pending > 0 {
                    Button("Upload now") { model.requestSync(manual: true) }.disabled(model.syncing)
                }
            }
            Text("Records the places you stop at, and your route while you walk, run or cycle, and draws a map of each day in your journal with a pin wherever you wrote an entry. Precise GPS runs only while you're moving on foot or by bike; the rest of the day uses iOS's low-power location. Everything goes only to your own Lunaschal server.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    /// What stops the trail from being recorded, if anything.
    private var problem: String? {
        switch trail.authorization {
        case .denied, .restricted:
            return "Location access is off for Lunaschal. Turn it on and choose Always: Settings → Lunaschal → Location."
        case .authorizedWhenInUse:
            return "Location is set to While Using, so nothing is recorded while Lunaschal is closed. Choose Always: Settings → Lunaschal → Location."
        case .notDetermined:
            return nil
        default:
            return trail.reducedAccuracy
                ? "Precise Location is off, so every point is blurred by kilometres. Turn on Precise Location: Settings → Lunaschal → Location."
                : nil
        }
    }
}

private struct HealthSettings: View {
    @ObservedObject var model: CaptureModel
    @AppStorage("healthSyncEnabled") private var enabled = false
    @State private var asking = false

    var body: some View {
        Section("Apple Health") {
            Toggle("Sync Apple Health", isOn: Binding(
                get: { enabled },
                set: { on in
                    if on {
                        asking = true
                        Task { _ = await model.enableHealth(); asking = false }
                    } else { model.disableHealth() }
                }))
                .disabled(asking)
            if enabled {
                let status = model.healthStatus
                if let last = status.lastSuccess {
                    Text("Last synced \(last.formatted(date: .abbreviated, time: .shortened)) · \(status.sent.formatted()) items sent")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    Text("Not synced yet. The first sync reads all of your Health history and can take a while.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if let error = status.lastError {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                Button("Sync now") { model.requestSync(manual: true) }.disabled(model.syncing)
                Button("Resend all Health data") { model.resendAllHealth() }.disabled(model.syncing)
            }
            Text("Reads sleep, workouts, exercise minutes, heart rate and everything else Health allows, and copies it to your Lunaschal server. Watch data arrives through the phone. Choose which types to share in the Health permission sheet; changing it later is in the Health app under Sharing → Apps.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }
}
