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
                    Button("Edit") { editing = project }.tint(.blue)
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
        case chapters = "Chapters", notes = "Notes", discussions = "Discussions"
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
    /// A chapter or note just made, opened straight away.
    @State private var opened: WritingDocument?
    /// A discussion just created from the toolbar, opened straight away.
    @State private var newDiscussion: WritingDiscussion?

    private var chapters: [WritingDocument] { writing.chapters(project.id) }
    private var notes: [WritingDocument] { writing.notes(project.id) }
    private var discussions: [WritingDiscussion] { writing.discussions[project.id] ?? [] }
    private var goal: Int? { writing.stats[project.id]?.dailyWordGoal ?? project.dailyWordGoal }

    var body: some View {
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
            case .chapters:
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
                    .swipeActions { Button("Delete", role: .destructive) { deleting = chapter } }
                    .accessibilityIdentifier("writing-chapter")
                }
                .onMove { from, to in
                    var ids = chapters.map(\.id)
                    ids.move(fromOffsets: from, toOffset: to)
                    Task { await writing.reorder(project: project.id, ids: ids) }
                }
                if chapters.isEmpty { Text("No chapters yet — tap + to start one.").foregroundStyle(.secondary) }
            case .notes:
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
                        Button(note.pinned ? "Unpin" : "Pin") {
                            Task { await writing.setPinned(note, !note.pinned) }
                        }
                        .tint(.orange)
                    }
                    .accessibilityIdentifier("writing-note")
                }
                if notes.isEmpty { Text("No notes yet").foregroundStyle(.secondary) }
            case .discussions:
                ForEach(discussions) { discussion in
                    NavigationLink {
                        WritingDiscussionView(model: WritingDiscussionModel(id: discussion.id, writing: writing),
                                              title: discussion.displayTitle)
                    } label: { Text(discussion.displayTitle) }
                    .accessibilityIdentifier("writing-discussion")
                }
                if discussions.isEmpty { Text("No discussions yet").foregroundStyle(.secondary) }
            }
        }
        .navigationTitle(project.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if section == .chapters, chapters.count > 1 {
                ToolbarItem(placement: .topBarTrailing) { EditButton() }
            }
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
        .task { await writing.load(project: project.id) }
        .refreshable {
            await writing.loadProjects()
            await writing.load(project: project.id)
        }
        .onChange(of: writing.capture.writingTick) { _, _ in writing.reloadDocuments() }
    }

    private var addLabel: String {
        switch section {
        case .chapters: return "New chapter"
        case .notes: return "New note"
        case .discussions: return "New discussion"
        }
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
    @FocusState private var typing: Bool

    init(model: WritingDiscussionModel, title: String) {
        _model = StateObject(wrappedValue: model)
        self.title = title
    }

    var body: some View {
        VStack(spacing: 0) {
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
