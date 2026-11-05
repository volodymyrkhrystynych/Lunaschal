import SwiftUI
import LunaschalCore

/// More → Writing: a book's notes and discussions. Chapters stay on the
/// desktop; on the phone a book is the notebook beside it and somewhere to
/// talk ideas through, with what gets settled proposed back into the notes.
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
        .navigationDestination(item: $opened) { project in
            WritingProjectView(writing: writing, project: project)
        }
        .task { await writing.loadProjects() }
        .refreshable { await writing.loadProjects() }
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

private struct WritingProjectView: View {
    enum Section: String, CaseIterable, Identifiable {
        case notes = "Notes", discussions = "Discussions"
        var id: Self { self }
    }

    @ObservedObject var writing: WritingModel
    let project: WritingProject
    @State private var section = Section.notes
    @State private var newNoteTitle = ""
    @State private var addingNote = false
    /// A discussion just created from the toolbar, opened straight away.
    @State private var newDiscussion: WritingDiscussion?

    private var notes: [WritingNote] { writing.notes[project.id] ?? [] }
    private var discussions: [WritingDiscussion] { writing.discussions[project.id] ?? [] }

    var body: some View {
        List {
            Picker("Section", selection: $section) {
                ForEach(Section.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .listRowBackground(Color.clear)

            switch section {
            case .notes:
                ForEach(notes) { note in
                    NavigationLink { WritingNoteView(writing: writing, noteID: note.id) } label: {
                        HStack {
                            Text(WritingDocType.label(note.docType)).font(.caption).foregroundStyle(.secondary)
                            Text(note.title)
                            Spacer()
                            if note.pinned {
                                Image(systemName: "pin.fill").font(.caption).foregroundStyle(.secondary)
                                    .accessibilityLabel("Pinned")
                            }
                        }
                    }
                    .swipeActions {
                        Button(note.pinned ? "Unpin" : "Pin") {
                            Task { _ = await writing.save(note, WritingNoteEdit(pinned: !note.pinned)) }
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
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    if section == .notes { addingNote = true }
                    else {
                        Task {
                            if let id = await writing.createDiscussion(project: project.id) {
                                newDiscussion = WritingDiscussion(id: id, title: nil)
                            }
                        }
                    }
                } label: { Label(section == .notes ? "New note" : "New discussion", systemImage: "plus") }
                .disabled(writing.fixture != nil)
            }
        }
        .navigationDestination(item: $newDiscussion) { discussion in
            WritingDiscussionView(model: WritingDiscussionModel(id: discussion.id, writing: writing),
                                  title: discussion.displayTitle)
        }
        .alert("New note", isPresented: $addingNote) {
            TextField("Title", text: $newNoteTitle)
            Button("Add") {
                let title = newNoteTitle.trimmingCharacters(in: .whitespacesAndNewlines)
                newNoteTitle = ""
                if !title.isEmpty { Task { _ = await writing.createNote(project: project.id, title: title) } }
            }
            Button("Cancel", role: .cancel) { newNoteTitle = "" }
        }
        .task { await writing.load(project: project.id) }
        .refreshable { await writing.load(project: project.id) }
    }
}

// MARK: Note

private struct WritingNoteView: View {
    @ObservedObject var writing: WritingModel
    let noteID: String
    @State private var note: WritingNote?
    @State private var content = ""
    @State private var saveTask: Task<Void, Never>?
    @State private var status = "Saved"
    @State private var showHistory = false

    var body: some View {
        Group {
            if let note {
                VStack(spacing: 0) {
                    HStack {
                        Picker("Type", selection: Binding(get: { note.docType }, set: { type in
                            self.note?.docType = type
                            Task { _ = await writing.save(note, WritingNoteEdit(docType: type)) }
                        })) {
                            ForEach(WritingDocType.all, id: \.id) { Text($0.label).tag($0.id) }
                        }
                        Toggle("Pinned", isOn: Binding(get: { note.pinned }, set: { on in
                            self.note?.pinned = on
                            Task { _ = await writing.save(note, WritingNoteEdit(pinned: on)) }
                        }))
                        .toggleStyle(.button)
                        .accessibilityIdentifier("writing-note-pin")
                        Spacer()
                        Text(status).font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(.horizontal).padding(.vertical, 6)
                    TextEditor(text: $content)
                        .padding(.horizontal, 8)
                        .accessibilityIdentifier("writing-note-content")
                        .onChange(of: content) { _, value in schedule(value) }
                }
                .navigationTitle(note.title)
            } else {
                ProgressView()
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showHistory = true } label: { Label("History", systemImage: "clock.arrow.circlepath") }
                    .disabled(note == nil)
            }
        }
        .sheet(isPresented: $showHistory) {
            NavigationStack {
                NoteHistoryView(writing: writing, noteID: noteID) { restored in
                    saveTask?.cancel()
                    note = restored
                    content = restored.content ?? ""
                    status = "Saved"
                    showHistory = false
                }
            }
        }
        .task {
            guard note == nil, let loaded = await writing.note(noteID) else { return }
            note = loaded
            content = loaded.content ?? ""
            status = "Saved"
        }
        // A pending edit is flushed rather than dropped when the screen goes.
        .onDisappear { if status == "Unsaved" { flush(content) } }
    }

    private func schedule(_ value: String) {
        guard let note, value != (note.content ?? "") || status != "Saved" else { return }
        status = "Unsaved"
        saveTask?.cancel()
        saveTask = Task {
            try? await Task.sleep(for: .seconds(1.5))
            guard !Task.isCancelled else { return }
            flush(value)
        }
    }

    private func flush(_ value: String) {
        guard let note else { return }
        status = "Saving…"
        Task {
            if await writing.save(note, WritingNoteEdit(content: value)) {
                self.note?.content = value
                status = "Saved"
            } else { status = "Unsaved" }
        }
    }
}

private struct NoteHistoryView: View {
    @ObservedObject var writing: WritingModel
    let noteID: String
    let onRestored: (WritingNote) -> Void
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
                                Task { if let note = await writing.restore(noteID, revision: revision) { onRestored(note) } }
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
        .task { revisions = await writing.revisions(noteID) }
    }
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
                            Text("Talk an idea through. What gets settled is proposed as a note for you to confirm.")
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
