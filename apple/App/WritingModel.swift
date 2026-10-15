import AVFoundation
import Foundation
import LunaschalCore
import SwiftUI

/// Writing on the phone: a book's notes and its discussions (the desktop's
/// src/components/Writing/). Fetched over REST, never replicated. A discussion
/// is a chat conversation the server knows is a Writing one, so the stream,
/// the cards and a dictated clip all go through the Chat tab's routes and the
/// server picks the prompt, the tools and the project.
@MainActor
final class WritingModel: ObservableObject {
    @Published private(set) var projects: [WritingProject] = []
    @Published private(set) var notes: [String: [WritingNote]] = [:]
    @Published private(set) var discussions: [String: [WritingDiscussion]] = [:]
    @Published var problem: String?

    let capture: CaptureModel
    /// Set by `-writingFixture`: canned data and no server, for UI tests.
    let fixture: WritingFixture?

    init(capture: CaptureModel) {
        self.capture = capture
        fixture = WritingFixture.ifAsked()
    }

    func api() -> JournalAPI? { capture.chatAPI() }

    private var offlineMessage: String {
        capture.server == nil ? "Connect to your server in Settings to use Writing."
                              : "Sign in again in Settings to use Writing."
    }

    func loadProjects() async {
        if let fixture { projects = fixture.projects; return }
        guard let api = api() else { problem = offlineMessage; return }
        do { projects = try await api.writingProjects(); problem = nil } catch { report(error) }
    }

    /// Starts a book and returns it, or nil with `problem` saying why. With
    /// the demo fixture it is kept in memory only, so the flow can be seen.
    func createProject(title: String, description: String) async -> WritingProject? {
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let description = description.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return nil }
        if fixture != nil {
            let project = WritingProject(id: ULID.make(), title: title, description: description.isEmpty ? nil : description)
            projects.insert(project, at: 0)
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

    func load(project id: String) async {
        if let fixture {
            notes[id] = fixture.notes.filter { $0.projectId == id }
            discussions[id] = fixture.discussions(for: id)
            return
        }
        guard let api = api() else { problem = offlineMessage; return }
        do {
            async let fetchedNotes = api.writingNotes(projectID: id)
            async let fetchedDiscussions = api.writingDiscussions(projectID: id)
            notes[id] = try await fetchedNotes
            discussions[id] = try await fetchedDiscussions
            problem = nil
        } catch { report(error) }
    }

    func note(_ id: String) async -> WritingNote? {
        if let fixture { return fixture.notes.first { $0.id == id } }
        guard let api = api() else { problem = offlineMessage; return nil }
        do { return try await api.writingNote(id) } catch { report(error); return nil }
    }

    func save(_ note: WritingNote, _ edit: WritingNoteEdit) async -> Bool {
        if fixture != nil { return true }
        guard let api = api() else { problem = offlineMessage; return false }
        do {
            try await api.updateWritingNote(note.id, edit)
            if edit.pinned != nil || edit.title != nil || edit.docType != nil { await load(project: note.projectId) }
            return true
        } catch { report(error); return false }
    }

    func createNote(project id: String, title: String) async -> String? {
        guard fixture == nil, let api = api() else { return nil }
        do {
            let noteID = try await api.createWritingNote(projectID: id, title: title)
            await load(project: id)
            return noteID
        } catch { report(error); return nil }
    }

    func createDiscussion(project id: String) async -> String? {
        guard fixture == nil, let api = api() else { return nil }
        do {
            let discussionID = try await api.createWritingDiscussion(projectID: id)
            await load(project: id)
            return discussionID
        } catch { report(error); return nil }
    }

    func revisions(_ noteID: String) async -> [WritingNoteRevision] {
        if let fixture { return fixture.revisions(noteID) }
        guard let api = api() else { return [] }
        return (try? await api.writingNoteRevisions(noteID)) ?? []
    }

    func restore(_ noteID: String, revision: WritingNoteRevision) async -> WritingNote? {
        guard fixture == nil, let api = api() else { return nil }
        do { return try await api.restoreWritingNote(noteID, revisionID: revision.id) } catch { report(error); return nil }
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
        do {
            try await api.resolveProposal(messageID: messageID, proposalID: proposal.id, accept: accept, data: data)
            await refresh()
            if let project = writing.projects.first(where: { writing.discussions[$0.id]?.contains { $0.id == id } == true }) {
                await writing.load(project: project.id)
            }
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
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
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
