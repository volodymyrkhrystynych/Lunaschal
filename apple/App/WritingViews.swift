import SwiftUI
import LunaschalCore

/// More → Writing: a book's chapters, notes and discussions. Chapters and notes
/// are kept on the phone and can be written offline; a discussion is somewhere
/// to talk ideas through, with what gets settled proposed back into the book.
struct WritingRootView: View {
    @StateObject private var writing: WritingModel

    init(capture: CaptureModel) {
        _writing = StateObject(wrappedValue: WritingModel(capture: capture))
    }

    var body: some View { WritingProjectsView(writing: writing) }
}

private struct WritingProjectsView: View {
    @ObservedObject var writing: WritingModel
    @State private var creating = false
    /// A book just started from the sheet, opened straight away.
    @State private var opened: WritingProject?
    @State private var editing: WritingProject?
    @State private var deleting: WritingProject?

    var body: some View {
        List {
            if let problem = writing.problem {
                Label(problem, systemImage: "wifi.slash").font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(writing.projects) { project in
                NavigationLink { WritingProjectView(writing: writing, project: project) } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(project.title)
                        if let description = project.description, !description.isEmpty {
                            Text(description).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                }
                .accessibilityIdentifier("writing-project")
                .swipeActions {
                    Button("Delete", role: .destructive) { deleting = project }
                    Button("Rename") { editing = project }.tint(.blue)
                        .accessibilityIdentifier("writing-rename")
                }
            }
            if writing.projects.isEmpty && writing.problem == nil {
                Text("No books yet — tap + to start one.").foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Writing")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { creating = true } label: { Label("New book", systemImage: "plus") }
                    .accessibilityIdentifier("writing-new-project")
            }
        }
        .sheet(isPresented: $creating) {
            NewBookSheet(writing: writing) { project in
                creating = false
                opened = project
            }
        }
        .sheet(item: $editing) { project in BookDetailsSheet(writing: writing, project: project) }
        .confirmationDialog("Delete this book?", isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }
        ), titleVisibility: .visible, presenting: deleting) { project in
            Button("Delete “\(project.title)”", role: .destructive) {
                Task { await writing.deleteProject(project) }
            }
        } message: { _ in
            Text("Every chapter, note and discussion in it goes too. This can't be undone.")
        }
        .navigationDestination(item: $opened) { project in
            WritingProjectView(writing: writing, project: project)
        }
        .task { await writing.loadProjects() }
        .refreshable { await writing.loadProjects() }
    }
}

private struct BookDetailsSheet: View {
    @ObservedObject var writing: WritingModel
    let project: WritingProject
    @State private var title = ""
    @State private var description = ""
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section { TextField("Title", text: $title) }
                Section {
                    TextField("What is it about?", text: $description, axis: .vertical).lineLimit(3...8)
                } footer: {
                    Text("The description is part of every discussion about this book.")
                }
            }
            .navigationTitle("Book details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            await writing.updateProject(project, title: title, description: description)
                            dismiss()
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear {
                title = project.title
                description = project.description ?? ""
            }
        }
    }
}

/// A title and, optionally, what the book is about — the description is read
/// into every discussion's prompt, so a line or two here is worth writing.
private struct NewBookSheet: View {
    @ObservedObject var writing: WritingModel
    let onCreated: (WritingProject) -> Void
    @State private var title = ""
    @State private var description = ""
    @State private var saving = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Title", text: $title)
                        .accessibilityIdentifier("writing-new-title")
                }
                Section {
                    TextField("What is it about? For a fanfic, name the original.", text: $description,
                              axis: .vertical)
                        .lineLimit(3...8)
                } footer: {
                    Text("The description is part of every discussion about this book.")
                }
                if let problem = writing.problem, saving == false {
                    Text(problem).font(.caption).foregroundStyle(.red)
                }
            }
            .navigationTitle("New book")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Creating…" : "Create") {
                        saving = true
                        Task {
                            let project = await writing.createProject(title: title, description: description)
                            saving = false
                            if let project { onCreated(project) }
                        }
                    }
                    .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || saving)
                    .accessibilityIdentifier("writing-new-create")
                }
            }
        }
    }
}

struct WritingProjectView: View {
    enum Section: String, CaseIterable, Identifiable {
        case chapters = "Chapters", notes = "Notes", discussions = "Discussions", canon = "Canon"
        var id: Self { self }
    }

    @ObservedObject var writing: WritingModel
    let project: WritingProject
    @State private var section = Section.chapters
    @State private var newTitle = ""
    @State private var adding = false
    @State private var settingGoal = false
    @State private var goalText = ""
    @State private var deleting: WritingDocument?
    @State private var renaming: WritingDocument?
    @State private var renamingDiscussion: WritingDiscussion?
    @State private var deletingDiscussion: WritingDiscussion?
    @State private var renameText = ""
    /// A chapter or note just made, opened straight away.
    @State private var opened: WritingDocument?
    /// A discussion just created from the toolbar, opened straight away.
    @State private var newDiscussion: WritingDiscussion?

    private var chapters: [WritingDocument] { writing.chapters(project.id) }
    private var notes: [WritingDocument] { writing.notes(project.id) }
    private var discussions: [WritingDiscussion] { writing.discussions[project.id] ?? [] }
    private var goal: Int? { writing.stats[project.id]?.dailyWordGoal ?? project.dailyWordGoal }

    var body: some View { dialogs(list) }

    private var list: some View {
        List {
            Picker("Section", selection: $section) {
                ForEach(Section.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .listRowBackground(Color.clear)

            if let problem = writing.problem {
                Text(problem).font(.footnote).foregroundStyle(.secondary)
            }

            switch section {
            case .chapters: chapterRows
            case .notes: noteRows
            case .discussions: discussionRows
            case .canon:
                WritingCanonSection(writing: writing, project: project)
            }
        }
        .navigationTitle(project.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if section == .chapters, chapters.count > 1 {
                ToolbarItem(placement: .topBarTrailing) { EditButton() }
            }
            if section != .canon {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        if section == .discussions {
                            Task {
                                if let id = await writing.createDiscussion(project: project.id) {
                                    newDiscussion = WritingDiscussion(id: id, title: nil)
                                }
                            }
                        } else {
                            adding = true
                        }
                    } label: { Label(addLabel, systemImage: "plus") }
                    .disabled(section == .discussions && writing.fixture != nil)
                    .accessibilityIdentifier("writing-add")
                }
            }
        }
        .navigationDestination(item: $newDiscussion) { discussion in
            WritingDiscussionView(model: WritingDiscussionModel(id: discussion.id, writing: writing),
                                  title: discussion.displayTitle)
        }
        .navigationDestination(item: Binding(
            get: { opened.map { OpenedDocument(key: $0.key, project: $0.projectId) } },
            set: { if $0 == nil { opened = nil } }
        )) { target in
            if let document = writing.store.document(target.key, project: target.project) {
                WritingEditorView(writing: writing, document: document)
            }
        }
    }

    private func dialogs<Content: View>(_ content: Content) -> some View {
        content
        .alert(addLabel, isPresented: $adding) {
            TextField("Title", text: $newTitle)
            Button("Add") {
                let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
                newTitle = ""
                guard !title.isEmpty else { return }
                opened = writing.create(section == .chapters ? .chapter : .note, project: project.id, title: title)
            }
            Button("Cancel", role: .cancel) { newTitle = "" }
        }
        .alert("Daily goal", isPresented: $settingGoal) {
            TextField("Words a day", text: $goalText).keyboardType(.numberPad)
            Button("Save") {
                let words = Int(goalText.trimmingCharacters(in: .whitespaces))
                Task { await writing.setGoal(project, wordsPerDay: words.flatMap { $0 > 0 ? $0 : nil }) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Leave it empty for no goal.") }
        .confirmationDialog("Delete this?", isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }
        ), titleVisibility: .visible, presenting: deleting) { document in
            Button("Delete “\(document.title)”", role: .destructive) {
                Task { await writing.delete(document) }
            }
        } message: { _ in Text("Its history goes too. This can't be undone.") }
        .alert("Rename", isPresented: Binding(
            get: { renaming != nil || renamingDiscussion != nil },
            set: { if !$0 { renaming = nil; renamingDiscussion = nil } }
        )) {
            TextField("Title", text: $renameText).accessibilityIdentifier("writing-rename-field")
            Button("Save") {
                if let document = renaming { writing.rename(document, to: renameText) }
                if let discussion = renamingDiscussion {
                    Task { await writing.renameDiscussion(discussion, project: project.id, to: renameText) }
                }
                renaming = nil; renamingDiscussion = nil
            }
            .accessibilityIdentifier("writing-rename-save")
            Button("Cancel", role: .cancel) { renaming = nil; renamingDiscussion = nil }
        }
        .confirmationDialog("Delete this discussion?", isPresented: Binding(
            get: { deletingDiscussion != nil }, set: { if !$0 { deletingDiscussion = nil } }
        ), titleVisibility: .visible, presenting: deletingDiscussion) { discussion in
            Button("Delete “\(discussion.displayTitle)”", role: .destructive) {
                Task { await writing.deleteDiscussion(discussion, project: project.id) }
            }
        } message: { _ in Text("Its messages go too. Notes and chapters it changed stay as they are.") }
        .task { await writing.load(project: project.id) }
        .refreshable {
            await writing.loadProjects()
            await writing.load(project: project.id)
        }
        .onChange(of: writing.capture.writingTick) { _, _ in writing.reloadDocuments() }
    }

    @ViewBuilder private var chapterRows: some View {
        wordsToday
        ForEach(chapters) { chapter in
            NavigationLink { WritingEditorView(writing: writing, document: chapter) } label: {
                HStack {
                    Text(chapter.title)
                    Spacer()
                    documentState(chapter)
                    Text(WritingStats.words(chapter.content), format: .number)
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            .swipeActions {
                Button("Delete", role: .destructive) { deleting = chapter }
                    .accessibilityIdentifier("writing-delete")
                renameButton { renaming = chapter; renameText = chapter.title }
            }
            .accessibilityIdentifier("writing-chapter")
        }
        .onMove { from, to in
            var ids = chapters.map(\.id)
            ids.move(fromOffsets: from, toOffset: to)
            Task { await writing.reorder(project: project.id, ids: ids) }
        }
        if chapters.isEmpty { Text("No chapters yet — tap + to start one.").foregroundStyle(.secondary) }
    }

    @ViewBuilder private var noteRows: some View {
        ForEach(notes) { note in
            NavigationLink { WritingEditorView(writing: writing, document: note) } label: {
                HStack {
                    Text(WritingDocType.label(note.docType)).font(.caption).foregroundStyle(.secondary)
                    Text(note.title)
                    Spacer()
                    documentState(note)
                    if note.pinned {
                        Image(systemName: "pin.fill").font(.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("Pinned")
                    }
                }
            }
            .swipeActions {
                Button("Delete", role: .destructive) { deleting = note }
                    .accessibilityIdentifier("writing-delete")
                renameButton { renaming = note; renameText = note.title }
                Button(note.pinned ? "Unpin" : "Pin") {
                    Task { await writing.setPinned(note, !note.pinned) }
                }
                .tint(.orange)
            }
            .accessibilityIdentifier("writing-note")
        }
        if notes.isEmpty { Text("No notes yet").foregroundStyle(.secondary) }
    }

    @ViewBuilder private var discussionRows: some View {
        ForEach(discussions) { discussion in
            NavigationLink {
                WritingDiscussionView(model: WritingDiscussionModel(id: discussion.id, writing: writing),
                                      title: discussion.displayTitle, passage: discussion.writingPassage)
            } label: {
                Label(discussion.displayTitle,
                      systemImage: discussion.writingPassage == nil ? "bubble.left.and.bubble.right" : "text.bubble")
            }
            .swipeActions {
                // A discussion lives on the server, so these need it.
                Button("Delete", role: .destructive) { deletingDiscussion = discussion }
                    .accessibilityIdentifier("writing-delete")
                renameButton { renamingDiscussion = discussion; renameText = discussion.title ?? "" }
            }
            .accessibilityIdentifier("writing-discussion")
        }
        if discussions.isEmpty { Text("No discussions yet").foregroundStyle(.secondary) }
    }

    private var addLabel: String {
        switch section {
        case .chapters: return "New chapter"
        case .notes: return "New note"
        case .discussions, .canon: return "New discussion"
        }
    }

    private func renameButton(_ action: @escaping () -> Void) -> some View {
        Button("Rename", action: action).tint(.blue).accessibilityIdentifier("writing-rename")
    }

    /// Today across every device, and how far along the goal that is.
    private var wordsToday: some View {
        let words = writing.wordsToday(project.id)
        return Button {
            goalText = goal.map(String.init) ?? ""
            settingGoal = true
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("Today").foregroundStyle(.primary)
                    Spacer()
                    Text(goal.map { "\(words.formatted()) of \($0.formatted()) words" } ?? "\(words.formatted()) words")
                        .foregroundStyle(.secondary).monospacedDigit()
                }
                .font(.subheadline)
                if let progress = WritingStats.goalProgress(words: words, goal: goal) {
                    ProgressView(value: progress)
                }
            }
        }
        .accessibilityIdentifier("writing-words-today")
    }

    @ViewBuilder private func documentState(_ document: WritingDocument) -> some View {
        if document.hold != nil {
            Image(systemName: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.orange)
                .accessibilityLabel("Needs a decision")
        } else if document.isDirty {
            Image(systemName: "iphone").font(.caption).foregroundStyle(.secondary)
                .accessibilityLabel("Not yet on the server")
        }
    }
}

private struct OpenedDocument: Hashable {
    let key: WritingDocumentKey
    let project: String
}

// MARK: Discussion

struct WritingDiscussionView: View {
    @StateObject var model: WritingDiscussionModel
    let title: String
    /// The highlighted words a discussion was opened about, kept in view.
    let passage: String?
    @FocusState private var typing: Bool

    init(model: WritingDiscussionModel, title: String, passage: String? = nil) {
        _model = StateObject(wrappedValue: model)
        self.title = title
        self.passage = passage
    }

    var body: some View {
        VStack(spacing: 0) {
            if let passage, !passage.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    RoundedRectangle(cornerRadius: 1.5).fill(Color.accentColor).frame(width: 3)
                    Text(passage).font(.callout).italic().foregroundStyle(.secondary).lineLimit(4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal).padding(.vertical, 8)
                .background(.bar)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Passage: \(passage)")
                .accessibilityIdentifier("writing-passage")
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        if model.messages.isEmpty && !model.isStreaming {
                            Text("Talk an idea through. What gets settled is proposed for you to confirm.")
                                .font(.footnote).foregroundStyle(.secondary).frame(maxWidth: .infinity)
                                .multilineTextAlignment(.center).padding(.vertical, 40)
                        }
                        ForEach(model.messages) { message in
                            if !(message.id == model.liveMessageID && !model.handedOff) {
                                WritingMessageRow(model: model, message: message)
                            }
                        }
                        if model.isStreaming || (model.liveMessageID != nil && !model.handedOff) {
                            VStack(alignment: .leading, spacing: 4) {
                                Group {
                                    if model.liveContent.isEmpty { ThinkingLabel() }
                                    else { MarkdownText(text: model.liveContent) }
                                }
                                .padding(.horizontal, 14).padding(.vertical, 9)
                                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                                StepsDisclosure(steps: model.liveSteps, thinking: model.liveThinking, live: true)
                            }
                        }
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding()
                }
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await model.refresh() }
                .onChange(of: model.messages.count) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
                .onChange(of: model.liveContent) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
            }
            composer
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.poll() }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 4) {
            if model.isRecording {
                Label("Recording — tap stop to send", systemImage: "waveform").font(.caption).foregroundStyle(.red)
            }
            if let notice = model.notice { Text(notice).font(.caption).foregroundStyle(.red) }
            HStack(alignment: .bottom, spacing: 8) {
                TextField("Discuss your story…", text: $model.input, axis: .vertical)
                    .lineLimit(1...6)
                    .focused($typing)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
                    .disabled(model.isStreaming)
                    .accessibilityIdentifier("writing-input")
                Button { Task { await model.toggleRecording() } } label: {
                    Image(systemName: model.isRecording ? "stop.circle.fill" : "mic")
                        .font(.title3).frame(width: 36, height: 36)
                        .foregroundStyle(model.isRecording ? .red : .accentColor)
                }
                .disabled(model.isStreaming || model.writing.fixture != nil)
                .accessibilityLabel(model.isRecording ? "Stop and send" : "Speak to send")
                Button { typing = false; Task { await model.send() } } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.title).frame(width: 36, height: 36)
                }
                .disabled(model.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isStreaming)
                .accessibilityLabel("Send")
            }
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }
}

private struct WritingMessageRow: View {
    @ObservedObject var model: WritingDiscussionModel
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        let meta = message.meta
        HStack(alignment: .top) {
            if isUser { Spacer(minLength: 48) }
            VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
                if !message.content.isEmpty {
                    Group {
                        if isUser { Text(message.content).textSelection(.enabled) }
                        else { MarkdownText(text: message.content) }
                    }
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .foregroundStyle(isUser ? Color.white : Color.primary)
                    .background(isUser ? Color.accentColor : Color(.secondarySystemBackground),
                                in: RoundedRectangle(cornerRadius: 16))
                } else if isUser, !message.clips.isEmpty {
                    Label("Voice message — transcribing", systemImage: "waveform").font(.caption).foregroundStyle(.secondary)
                }
                StepsDisclosure(steps: meta.steps, thinking: meta.thinking, live: message.status == "streaming")
                if message.role == "assistant" && message.status == "streaming" && !model.isStreaming {
                    ThinkingLabel().font(.caption)
                }
                if message.role == "assistant" && message.status == "error" {
                    Text("Error: \(message.error ?? "The reply failed.")").font(.caption).foregroundStyle(.red)
                }
                if !meta.proposals.isEmpty {
                    ProposalCards(resolver: model, messageID: message.id, proposals: meta.proposals)
                }
            }
            if !isUser { Spacer(minLength: 48) }
        }
    }
}

// MARK: Canon

/// The original work's wikis, imported on the server: which are linked to this
/// book (and so searchable by its discussions), and a way into them.
private struct WritingCanonSection: View {
    @ObservedObject var writing: WritingModel
    let project: WritingProject
    @State private var importing = false
    @State private var importURL = ""

    private var linked: Set<String> { writing.linkedCanon[project.id] ?? [] }

    var body: some View {
        Group {
            NavigationLink {
                WritingCanonSearchView(writing: writing, project: project)
            } label: {
                Label("Search canon", systemImage: "magnifyingglass")
            }
            .disabled(linked.isEmpty)
            .accessibilityIdentifier("writing-canon-search")
            if linked.isEmpty {
                Text("Link a wiki below to search it here and in this book's discussions.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(writing.canonSources) { source in
                Toggle(isOn: Binding(
                    get: { linked.contains(source.id) },
                    set: { on in Task { await writing.setCanon(project: project.id, source: source.id, linked: on) } }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(source.displayName)
                        Text(source.summary).font(.caption)
                            .foregroundStyle(source.status == "error" ? Color.red : Color.secondary)
                    }
                }
                .disabled(source.status == "importing" && !linked.contains(source.id))
                .accessibilityIdentifier("writing-canon-source")
            }
            Button { importing = true } label: { Label("Import a wiki…", systemImage: "square.and.arrow.down") }
                .disabled(writing.fixture != nil)
        }
        .task { await writing.loadCanon(project: project.id) }
        .alert("Import a wiki", isPresented: $importing) {
            TextField("https://example.fandom.com", text: $importURL)
                .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
            Button("Import") {
                let url = importURL
                importURL = ""
                Task { await writing.importCanon(url: url, project: project.id) }
            }
            Button("Cancel", role: .cancel) { importURL = "" }
        } message: {
            Text("The server downloads the wiki's dump and imports it in the background. Large wikis take a while.")
        }
    }
}

private struct WritingCanonSearchView: View {
    @ObservedObject var writing: WritingModel
    let project: WritingProject
    @State private var query = ""
    @State private var hits: [WritingCanonHit] = []
    @State private var searched = false

    var body: some View {
        List(hits) { hit in
            NavigationLink { WritingCanonArticleView(writing: writing, project: project, hit: hit) } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(hit.title)
                    Text(hit.snippet).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    if (writing.linkedCanon[project.id]?.count ?? 0) > 1 {
                        Text(hit.sourceName).font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
            .accessibilityIdentifier("writing-canon-hit")
        }
        .overlay {
            if hits.isEmpty {
                Text(searched ? "Nothing in the linked wikis matches." : "Search a character, place or event.")
                    .foregroundStyle(.secondary)
            }
        }
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search canon")
        .onSubmit(of: .search) {
            Task {
                hits = await writing.searchCanon(project: project.id, query: query)
                searched = true
            }
        }
        .navigationTitle("Canon")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct WritingCanonArticleView: View {
    @ObservedObject var writing: WritingModel
    let project: WritingProject
    let hit: WritingCanonHit
    @State private var article: WritingCanonArticle?
    @State private var loaded = false

    var body: some View {
        ScrollView {
            if let article {
                VStack(alignment: .leading, spacing: 12) {
                    Text(article.text).textSelection(.enabled)
                        .accessibilityIdentifier("writing-canon-article")
                    Text(article.attribution).font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: 720, alignment: .leading).padding()
            } else if loaded {
                Text(writing.problem ?? "Couldn't open this page.").foregroundStyle(.secondary).padding()
            } else {
                ProgressView().padding()
            }
        }
        .navigationTitle(article?.title ?? hit.title)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            article = await writing.canonArticle(project: project.id, id: hit.id)
            loaded = true
        }
    }
}
