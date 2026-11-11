import AVFoundation
import Foundation
import LunaschalCore
import SwiftUI

/// Writing on the phone: a book's chapters, notes and discussions (the desktop's
/// src/components/Writing/). Chapters and notes are read from WritingStore, so
/// they work with no server; a discussion is a chat conversation the server knows
/// is a Writing one, so the stream, the cards and a dictated clip all go through
/// the Chat tab's routes and the server picks the prompt, the tools and the project.
@MainActor
final class WritingModel: ObservableObject {
    @Published private(set) var projects: [WritingProject] = []
    @Published private(set) var documents: [String: [WritingDocument]] = [:]
    @Published private(set) var discussions: [String: [WritingDiscussion]] = [:]
    @Published private(set) var stats: [String: WritingWordStats] = [:]
    /// Every imported wiki, and which of them each book is linked to.
    @Published private(set) var canonSources: [WritingCanonSource] = []
    @Published private(set) var linkedCanon: [String: Set<String>] = [:]
    @Published var problem: String?

    let capture: CaptureModel
    /// Set by `-writingFixture`: canned data and no server, for UI tests.
    let fixture: WritingFixture?

    init(capture: CaptureModel) {
        self.capture = capture
        fixture = WritingFixture.ifAsked()
        projects = capture.writingStore.projects()
    }

    var store: WritingStore { capture.writingStore }

    func api() -> JournalAPI? { capture.chatAPI() }

    private var offlineMessage: String {
        capture.server == nil ? "Connect to your server in Settings to use Writing."
                              : "Sign in again in Settings to use Writing."
    }

    /// Says why something that needs the server didn't happen.
    private func report(needing action: String) { problem = "\(action) needs your server." }

    func chapters(_ project: String) -> [WritingDocument] {
        (documents[project] ?? []).filter { $0.kind == .chapter }
    }

    func notes(_ project: String) -> [WritingDocument] {
        (documents[project] ?? []).filter { $0.kind == .note }
    }

    func reloadDocuments() {
        projects = store.projects()
        for id in documents.keys { documents[id] = store.documents(project: id) }
        if !capture.writingRefusals.isEmpty {
            problem = capture.writingRefusals.joined(separator: "\n")
            capture.writingRefusals = []
        }
    }

    func loadProjects() async {
        projects = store.projects()
        if fixture != nil { return }
        guard api() != nil else {
            if projects.isEmpty { problem = offlineMessage }
            return
        }
        let reached = await capture.pushWriting(pull: true)
        reloadDocuments()
        problem = reached || !projects.isEmpty ? nil : "Couldn't reach your server."
    }

    /// Starts a book and returns it, or nil with `problem` saying why. Unlike a
    /// chapter, a book needs the server: it is what everything else hangs off.
    func createProject(title: String, description: String) async -> WritingProject? {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let description = description.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        if fixture != nil {
            let project = WritingProject(id: ULID.make(), title: title, description: description.isEmpty ? nil : description)
            try? store.saveProjects([project] + store.projects())
            projects = store.projects()
            return project
        }
        guard let api = api() else { problem = offlineMessage; return nil }
        do {
            let id = try await api.createWritingProject(title: title, description: description.isEmpty ? nil : description)
            await loadProjects()
            return projects.first { $0.id == id }
                ?? WritingProject(id: id, title: title, description: description.isEmpty ? nil : description)
        } catch { report(error); return nil }
    }

    func updateProject(_ project: WritingProject, title: String, description: String) async {
        guard let api = api(), fixture == nil else { return report(needing: "Changing a book") }
        do {
            try await api.updateWritingProject(project.id, title: title, description: description)
            await loadProjects()
        } catch { report(error) }
    }

    func deleteProject(_ project: WritingProject) async {
        guard let api = api(), fixture == nil else { return report(needing: "Deleting a book") }
        do {
            try await api.deleteWritingProject(project.id)
            try store.removeProject(project.id)
            documents[project.id] = nil
            await loadProjects()
        } catch { report(error) }
    }

    func setGoal(_ project: WritingProject, wordsPerDay: Int?) async {
        guard let api = api(), fixture == nil else { return report(needing: "Setting a goal") }
        do {
            try await api.setWritingGoal(project.id, wordsPerDay: wordsPerDay)
            stats[project.id] = try await api.writingWordStats(projectID: project.id)
        } catch { report(error) }
    }

    func load(project id: String) async {
        documents[id] = store.documents(project: id)
        if let fixture {
            discussions[id] = fixture.discussions(for: id)
            return
        }
        guard let api = api() else { return }
        do {
            async let fetchedDiscussions = api.writingDiscussions(projectID: id)
            async let fetchedStats = api.writingWordStats(projectID: id)
            discussions[id] = try await fetchedDiscussions
            stats[id] = try await fetchedStats
            problem = nil
        } catch {
            // The book is on the phone; only the talk and the counts wait for the server.
        }
    }

    /// Today's words: what the server counted plus what it hasn't seen yet.
    func wordsToday(_ project: String) -> Int {
        WritingStats.wordsToday(serverWords: stats[project]?.todayWords ?? 0,
                                unsynced: WritingStats.unsyncedWords(documents[project] ?? []))
    }

    /// A new chapter or note, on the phone first so that it works offline.
    func create(_ kind: WritingDocumentKind, project: String, title: String) -> WritingDocument? {
        do {
            let document = try store.create(kind, project: project, title: title)
            documents[project] = store.documents(project: project)
            Task { await capture.pushWriting() }
            return document
        } catch { report(error); return nil }
    }

    /// A new title, on the phone first: it travels with the draft, so it
    /// works offline and is revision-checked like any other edit.
    func rename(_ document: WritingDocument, to title: String) {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, title != document.title else { return }
        do {
            try store.edit(document.key, project: document.projectId, title: title)
            documents[document.projectId] = store.documents(project: document.projectId)
            Task { await capture.pushWriting() }
        } catch { report(error) }
    }

    func delete(_ document: WritingDocument) async {
        if document.onServer, fixture == nil {
            guard let api = api() else { return report(needing: "Deleting") }
            do { try await api.deleteWritingDocument(document.key) } catch { return report(error) }
        }
        try? store.remove(document.key, project: document.projectId)
        documents[document.projectId] = store.documents(project: document.projectId)
    }

    /// Everything must be on the server for the order to be: it refuses a list
    /// that doesn't name exactly the chapters it has.
    func reorder(project: String, ids: [String]) async {
        if fixture == nil {
            guard let api = api() else { return report(needing: "Reordering") }
            guard chapters(project).allSatisfy(\.onServer) else {
                problem = "A new chapter is still on its way to the server. Try again in a moment."
                return
            }
            do { try await api.orderWritingChapters(projectID: project, ids: ids) } catch { return report(error) }
        }
        for (position, id) in ids.enumerated() {
            try? store.setPosition(WritingDocumentKey(.chapter, id), project: project, position)
        }
        documents[project] = store.documents(project: project)
    }

    func setPinned(_ document: WritingDocument, _ pinned: Bool) async {
        if fixture == nil {
            guard let api = api() else { return report(needing: "Pinning") }
            do { try await api.updateWritingNote(document.id, WritingNoteEdit(pinned: pinned)) } catch { return report(error) }
        }
        try? store.setPinned(document.key, project: document.projectId, pinned)
        documents[document.projectId] = store.documents(project: document.projectId)
    }

    func setDocType(_ document: WritingDocument, _ docType: String) async {
        if fixture == nil {
            guard let api = api() else { return report(needing: "Changing the type") }
            do { try await api.updateWritingNote(document.id, WritingNoteEdit(docType: docType)) } catch { return report(error) }
        }
        try? store.setDocType(document.key, project: document.projectId, docType)
        documents[document.projectId] = store.documents(project: document.projectId)
    }

    /// The discussion a chapter opens: its latest, or a new one bound to it.
    func discussion(forChapter id: String, project: String) async -> WritingDiscussion? {
        if let existing = discussions[project]?.first(where: { $0.writingChapterId == id }) { return existing }
        if let fixture { return fixture.discussions(for: project).first }
        guard let api = api() else { report(needing: "A discussion"); return nil }
        await capture.pushWriting()
        do {
            let created = try await api.createWritingDiscussion(projectID: project, chapterID: id)
            await load(project: project)
            return discussions[project]?.first { $0.id == created }
                ?? WritingDiscussion(id: created, title: nil, writingChapterId: id)
        } catch { report(error); return nil }
    }

    /// A new discussion about one highlighted passage of a chapter. What was
    /// typed goes up first: the rewrite it proposes is matched against the
    /// server's copy of the chapter, so the passage has to be there.
    func passageDiscussion(chapter: WritingDocument, passage: String) async -> WritingDiscussion? {
        if let fixture { return fixture.passageDiscussion(chapter: chapter.id, passage: passage) }
        guard let api = api() else { report(needing: "Discussing a passage"); return nil }
        await capture.pushWriting()
        do {
            let created = try await api.createWritingDiscussion(projectID: chapter.projectId, chapterID: chapter.id,
                                                                passage: passage)
            await load(project: chapter.projectId)
            return discussions[chapter.projectId]?.first { $0.id == created }
                ?? WritingDiscussion(id: created, title: nil, writingChapterId: chapter.id, writingPassage: passage)
        } catch { report(error); return nil }
    }

    func renameDiscussion(_ discussion: WritingDiscussion, project: String, to title: String) async {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        guard fixture == nil else { return }
        guard let api = api() else { return report(needing: "Renaming a discussion") }
        do {
            try await api.renameWritingDiscussion(discussion.id, title: title)
            await load(project: project)
        } catch { report(error) }
    }

    func deleteDiscussion(_ discussion: WritingDiscussion, project: String) async {
        guard fixture == nil else { return }
        guard let api = api() else { return report(needing: "Deleting a discussion") }
        do {
            try await api.deleteWritingDiscussion(discussion.id)
            discussions[project]?.removeAll { $0.id == discussion.id }
        } catch { report(error) }
    }

    // MARK: Canon — online only; a wiki dump is far too big to keep on the phone.

    func loadCanon(project: String) async {
        if let fixture {
            canonSources = fixture.canonSources
            if linkedCanon[project] == nil { linkedCanon[project] = fixture.linkedCanon(project) }
            return
        }
        guard let api = api() else { return report(needing: "Canon") }
        do {
            async let sources = api.writingCanonSources()
            async let linked = api.writingProjectCanon(projectID: project)
            canonSources = try await sources
            linkedCanon[project] = Set(try await linked)
        } catch { report(error) }
    }

    func setCanon(project: String, source: String, linked: Bool) async {
        var ids = linkedCanon[project] ?? []
        if linked { ids.insert(source) } else { ids.remove(source) }
        if fixture == nil {
            guard let api = api() else { return report(needing: "Linking a wiki") }
            do { try await api.setWritingProjectCanon(projectID: project, sourceIDs: ids.sorted()) }
            catch { return report(error) }
        }
        linkedCanon[project] = ids
    }

    func searchCanon(project: String, query: String) async -> [WritingCanonHit] {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return [] }
        if let fixture { return fixture.canonHits(query) }
        guard let api = api() else { report(needing: "Searching canon"); return [] }
        do { return try await api.searchWritingCanon(projectID: project, query: query) }
        catch { report(error); return [] }
    }

    func canonArticle(project: String, id: String) async -> WritingCanonArticle? {
        if let fixture { return fixture.canonArticle(id) }
        guard let api = api() else { report(needing: "Reading canon"); return nil }
        do { return try await api.writingCanonArticle(projectID: project, id: id) }
        catch { report(error); return nil }
    }

    /// Starts a server-side import from a wiki's address; it shows up in the
    /// list as importing, and can be linked once it is ready.
    func importCanon(url: String, project: String) async {
        let url = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !url.isEmpty, fixture == nil else { return }
        guard let api = api() else { return report(needing: "Importing a wiki") }
        do {
            _ = try await api.importWritingCanon(url: url)
            await loadCanon(project: project)
        } catch { report(error) }
    }

    func createDiscussion(project id: String) async -> String? {
        guard fixture == nil else { return nil }
        guard let api = api() else { report(needing: "A discussion"); return nil }
        do {
            let discussionID = try await api.createWritingDiscussion(projectID: id)
            await load(project: id)
            return discussionID
        } catch { report(error); return nil }
    }

    func revisions(_ key: WritingDocumentKey) async -> [WritingNoteRevision] {
        if let fixture { return fixture.revisions(key.id) }
        guard let api = api() else { return [] }
        let found = key.kind == .chapter ? try? await api.writingChapterRevisions(key.id)
                                         : try? await api.writingNoteRevisions(key.id)
        return found ?? []
    }

    func restore(_ key: WritingDocumentKey, project: String, revision: WritingNoteRevision) async -> Bool {
        guard fixture == nil, let api = api() else { return false }
        do {
            let server: WritingServerCopy
            if key.kind == .chapter {
                let chapter = try await api.restoreWritingChapter(key.id, revisionID: revision.id)
                server = WritingServerCopy(title: chapter.title, content: chapter.content ?? "", revision: chapter.revision)
            } else {
                let note = try await api.restoreWritingNote(key.id, revisionID: revision.id)
                server = WritingServerCopy(title: note.title, content: note.content ?? "", revision: note.revision)
            }
            try store.restored(key, project: project, server: server)
            return true
        } catch { report(error); return false }
    }

    private func report(_ error: Error) {
        if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
        problem = error.localizedDescription
    }
}

/// One Writing discussion: its transcript, the reply streaming into it, and a
/// voice clip that goes through the Chat tab's recording outbox. The clip
/// names this conversation, so the server's autoreply answers it as Writing.
@MainActor
final class WritingDiscussionModel: NSObject, ObservableObject, ProposalResolver, AVAudioRecorderDelegate {
    @Published private(set) var conversation: ChatConversation?
    @Published var input = ""
    @Published var notice: String?
    @Published private(set) var isStreaming = false
    @Published private(set) var liveContent = ""
    @Published private(set) var liveThinking = ""
    @Published private(set) var liveSteps: [AgentStep] = []
    @Published private(set) var liveMessageID: String?
    @Published private(set) var recordingID: String?

    let id: String
    let writing: WritingModel
    private var recorder: AVAudioRecorder?

    init(id: String, writing: WritingModel) {
        self.id = id
        self.writing = writing
        super.init()
        if let fixture = writing.fixture { conversation = fixture.conversation(id) }
    }

    var messages: [ChatMessage] { (conversation?.messages ?? []).filter { $0.role != "system" } }
    var isRecording: Bool { recordingID != nil }

    var handedOff: Bool {
        guard let liveMessageID, let row = messages.first(where: { $0.id == liveMessageID }) else { return false }
        return row.status == "done" || row.status == "error"
    }

    var shouldPoll: Bool {
        ChatSegments.shouldPoll(conversation?.messages ?? []) || (liveMessageID != nil && !handedOff)
            || writing.capture.chatRecordingQueue.contains { $0.state == .pending && $0.conversationID == id }
    }

    func refresh() async {
        guard writing.fixture == nil, let api = writing.api() else { return }
        do {
            conversation = try await api.writingConversation(id)
            if !isStreaming, handedOff { clearLive() }
        } catch {
            if (error as? URLError)?.code != .cancelled { notice = "Offline — showing the discussion as last seen." }
        }
    }

    func poll() async {
        while !Task.isCancelled {
            await refresh()
            let wait: Double = shouldPoll ? ChatSegments.pollInterval : 20
            do { try await Task.sleep(for: .seconds(wait)) } catch { return }
        }
    }

    func send() async {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }
        guard let api = writing.api() else { notice = "Connect to your server to send a typed message."; return }
        input = ""
        notice = nil
        isStreaming = true
        clearLive()
        var saved = false
        var liveID: String?
        do {
            let messageID = try await api.addChatMessage(conversationID: id, content: text, attachmentIDs: [])
            saved = true
            let turns = ChatTurn.request(history: conversation?.messages ?? [], adding: ChatTurn(
                id: messageID, role: "user", content: text, metadata: nil,
                createdAt: ISO8601DateFormatter().string(from: Date()), attachmentIds: []))
            await refresh()
            do {
                for try await event in api.streamChatReply(conversationID: id, turns: turns) {
                    switch event {
                    case let .messageID(value): liveID = value; liveMessageID = value
                    case let .step(step): liveSteps.append(step)
                    case let .thinking(value): liveThinking += value
                    case let .content(value): liveContent += value
                    case let .error(message): throw ChatError.server(message)
                    case .done, .end: break
                    }
                }
            } catch where liveID != nil {
                // The run owns the row and keeps going; the poll finishes the job.
            }
            isStreaming = false
            await refresh()
            if liveID == nil { clearLive() }
        } catch {
            isStreaming = false
            clearLive()
            if !saved, input.isEmpty { input = text }
            notice = error.localizedDescription
            await refresh()
        }
    }

    private func clearLive() {
        liveContent = ""; liveThinking = ""; liveSteps = []; liveMessageID = nil
    }

    func resolve(messageID: String, proposal: ChatProposal, accept: Bool, data: [String: JSONValue]) async -> String? {
        guard let api = writing.api() else { return "Connect to your server to save this." }
        // What was typed goes first: a card is judged against the server's text,
        // and accepting one must not leave the open editor conflicting with it.
        await writing.capture.pushWriting()
        do {
            try await api.resolveProposal(messageID: messageID, proposalID: proposal.id, accept: accept, data: data)
            await refresh()
            await writing.capture.pushWriting(pull: true)
            writing.reloadDocuments()
            return nil
        } catch { return error.localizedDescription }
    }

    // MARK: Voice

    /// Stopping queues the clip with whatever was typed; the server transcribes
    /// it and answers in this discussion, so the phone can be put away.
    func toggleRecording() async {
        if isRecording { stopRecording(); return }
        let store = writing.capture.chatRecordings
        var item: ChatRecording?
        do {
            guard await AVAudioApplication.requestRecordPermission() else {
                throw RecorderError.message("Allow microphone access in Settings to record.")
            }
            if writing.capture.recorder.activeID != nil { writing.capture.recorder.stop() }
            try RecordingSession.activate()
            let begun = try store.begin(conversationID: id, text: input, attachmentIDs: [])
            item = begun
            let audio = try AVAudioRecorder(url: store.audioURL(begun), settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100,
                AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000,
            ])
            audio.delegate = self
            guard audio.prepareToRecord(), audio.record() else { throw RecorderError.message("Could not start the recorder.") }
            recorder = audio
            recordingID = begun.id
        } catch {
            if let item { try? store.remove(item) }
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            notice = error.localizedDescription
        }
    }

    func stopRecording() {
        guard let recordingID else { return }
        self.recordingID = nil
        recorder?.stop()
        recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        do {
            if try writing.capture.chatRecordings.finish(recordingID) != nil { input = "" }
        } catch { notice = error.localizedDescription }
        writing.capture.reload()
        writing.capture.requestSync(manual: true)
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.stopRecording()
        }
    }
}
