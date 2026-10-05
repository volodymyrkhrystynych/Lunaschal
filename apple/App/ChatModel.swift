import AVFoundation
import Foundation
import LunaschalCore
import SwiftUI

/// The Chat tab: today's one conversation, as the desktop's ChatPanel runs it.
/// Sending, staging photos, the cards and the to-dos need the server; a voice
/// message doesn't, because it goes through the sync outbox and the server
/// answers it on its own.
@MainActor
final class ChatModel: NSObject, ObservableObject, AVAudioRecorderDelegate {
    @Published private(set) var conversation: ChatConversation?
    @Published private(set) var todos: [ChatTodo] = []
    @Published var input = ""
    @Published private(set) var staged: [ChatAttachment] = []
    @Published private(set) var uploadingPhotos = false
    /// Something to say under the composer: a failed attach, a refused send.
    @Published var notice: String?
    /// Why today's conversation couldn't be fetched, while offline or signed out.
    @Published private(set) var loadProblem: String?

    // The reply this phone is streaming, until the saved row catches up.
    @Published private(set) var isStreaming = false
    @Published private(set) var liveContent = ""
    @Published private(set) var liveThinking = ""
    @Published private(set) var liveSteps: [AgentStep] = []
    @Published private(set) var liveMessageID: String?
    @Published private(set) var streamError: String?

    /// "Flashcard this" drafts, waiting for Approve.
    @Published private(set) var noteCards: [DraftCard] = []
    @Published private(set) var duplicateHint: (cardID: String, question: String, score: Double)?

    @Published private(set) var recordingID: String?
    @Published private(set) var recordingStarting = false

    /// Set when the next scroll should go to the newest "New chat" divider.
    @Published var scrollToBreak = false

    let capture: CaptureModel
    private var recorder: AVAudioRecorder?
    private var photoCache: [String: Data] = [:]
    private let cacheURL: URL

    init(capture: CaptureModel) {
        self.capture = capture
        cacheURL = capture.chatRecordings.root.appendingPathComponent("today.cache")
        super.init()
        conversation = try? JSONDecoder().decode(ChatConversation.self, from: Data(contentsOf: cacheURL))
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted),
            name: AVAudioSession.interruptionNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    var messages: [ChatMessage] { conversation?.messages ?? [] }
    var hasCurrentSegment: Bool { ChatSegments.hasCurrentSegment(messages) }
    var isRecording: Bool { recordingID != nil }

    /// Whether the saved row has caught up with what this phone streamed.
    /// Until it has, the live bubble is the only copy of the reply on screen.
    var handedOff: Bool {
        guard let liveMessageID, let row = messages.first(where: { $0.id == liveMessageID }) else { return false }
        return !row.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || row.status == "done" || row.status == "error"
    }

    var shouldPoll: Bool {
        ChatSegments.shouldPoll(messages) || (liveMessageID != nil && !handedOff)
            || staged.contains { $0.descriptionStatus == "running" }
            || capture.chatRecordingQueue.contains { $0.state == .pending }
    }

    // MARK: Loading

    /// `todos` is off for the fast ticks: the bar changes only when something
    /// writes to it, and those writes refresh it themselves.
    func refresh(todos withTodos: Bool = true) async {
        guard let api = capture.chatAPI() else {
            loadProblem = capture.server == nil ? "Connect to your server in Settings to chat."
                                                : "Sign in again in Settings to chat."
            return
        }
        do {
            let today = try await api.chatToday()
            apply(today)
            loadProblem = nil
            if withTodos { todos = (try? await api.chatTodos()) ?? todos }
            await refreshStaged(using: api)
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
            loadProblem = conversation == nil ? "Can't reach your server. Voice messages still record and send when it's back."
                                              : "Offline — showing the conversation as last seen."
        }
    }

    private func apply(_ today: ChatConversation?) {
        let hadAddTodo = Self.todoWrites(conversation)
        conversation = today
        if let today, let data = try? JSONEncoder().encode(today) { try? data.write(to: cacheURL, options: .atomic) }
        else if today == nil { try? FileManager.default.removeItem(at: cacheURL) }
        // A to-do the assistant wrote reaches the bar only through the poll
        // after a dropped stream; notice it from the saved steps too.
        if Self.todoWrites(today) > hadAddTodo { Task { await refreshTodos() } }
        if !isStreaming, handedOff { clearLive() }
    }

    private static func todoWrites(_ conversation: ChatConversation?) -> Int {
        (conversation?.messages ?? []).reduce(0) { $0 + $1.meta.steps.filter(\.writesChatTodo).count }
    }

    /// While the tab is open: quickly while something is still being written
    /// on the server, slowly otherwise.
    func poll() async {
        var fast = false
        while !Task.isCancelled {
            await refresh(todos: !fast)
            fast = shouldPoll
            let wait: Double = fast ? ChatSegments.pollInterval : 20
            do { try await Task.sleep(for: .seconds(wait)) } catch { return }
        }
    }

    // MARK: Sending

    func send() async {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        // A photo on its own is a whole message: "what is this?" is implied.
        guard !text.isEmpty || !staged.isEmpty, !isStreaming else { return }
        guard let api = capture.chatAPI() else { notice = "Connect to your server to send a typed message."; return }
        let photos = staged
        input = ""
        staged = []
        notice = nil
        streamError = nil
        isStreaming = true
        liveContent = ""; liveThinking = ""; liveSteps = []; liveMessageID = nil
        var liveID: String?
        var saved = false
        do {
            let conversationID = try await conversationID(api)
            let attachmentIDs = photos.map(\.id)
            let id = try await api.addChatMessage(conversationID: conversationID, content: text, attachmentIDs: attachmentIDs)
            saved = true
            let turns = ChatTurn.request(history: messages, adding: ChatTurn(
                id: id, role: "user", content: text, metadata: nil,
                createdAt: ISO8601DateFormatter().string(from: Date()), attachmentIds: attachmentIDs))
            // Show the question straight away, as the desktop's refetch does.
            if let today = try? await api.chatToday() { apply(today) }
            var drafts: [String] = []
            do {
                for try await event in api.streamChatReply(conversationID: conversationID, turns: turns) {
                    switch event {
                    case let .messageID(id): liveID = id; liveMessageID = id
                    case let .step(step):
                        liveSteps.append(step)
                        if step.writesChatTodo { Task { await refreshTodos() } }
                    case let .thinking(text): liveThinking += text
                    case let .content(text): liveContent += text
                    case let .done(flashcardDrafts): drafts = flashcardDrafts
                    case let .error(message): throw ChatError.server(message)
                    case .end: break
                    }
                }
            } catch where liveID != nil {
                // The server has this reply and keeps writing it; the poll
                // picks it up from the saved row.
            }
            isStreaming = false
            await refresh()
            if liveID == nil { clearLive() }
            for draft in drafts { await draftCards(from: draft) }
        } catch {
            isStreaming = false
            clearLive()
            // Not saved: put the words back so they aren't lost. Saved but
            // unanswered: they are in the conversation already.
            if !saved {
                if input.isEmpty { input = text }
                if staged.isEmpty { staged = photos }
            }
            streamError = error.localizedDescription
            await refresh()
        }
    }

    private func clearLive() {
        liveContent = ""; liveThinking = ""; liveSteps = []; liveMessageID = nil
    }

    private func conversationID(_ api: JournalAPI) async throws -> String {
        if let id = conversation?.id { return id }
        let id = try await api.chatConversationID()
        if conversation == nil { conversation = ChatConversation(id: id, messages: []) }
        return id
    }

    /// "New chat" compacts this segment into a summary the next one starts
    /// from; "Clean slate" starts with nothing. Both keep the history visible.
    func startNewChat(carryContext: Bool) async {
        guard let api = capture.chatAPI(), let id = conversation?.id, hasCurrentSegment, !isStreaming else { return }
        do {
            try await api.startNewChat(conversationID: id, carryContext: carryContext)
            scrollToBreak = true
            await refresh()
        } catch { notice = error.localizedDescription }
    }

    // MARK: Photos

    func attach(_ photos: [(data: Data, filename: String, contentType: String)]) async {
        guard !photos.isEmpty else { return }
        guard let api = capture.chatAPI() else { notice = "Connect to your server to attach a photo."; return }
        uploadingPhotos = true
        defer { uploadingPhotos = false }
        notice = nil
        capture.location.refresh()
        do {
            let id = try await conversationID(api)
            let fix = capture.location.recent
            let uploaded = try await api.uploadChatPhotos(conversationID: id, photos: photos,
                                                          latitude: fix?.latitude, longitude: fix?.longitude)
            staged += uploaded
            for (photo, row) in zip(photos, uploaded) { photoCache[row.id] = photo.data }
        } catch { notice = error.localizedDescription }
    }

    func removeStaged(_ attachment: ChatAttachment) async {
        staged.removeAll { $0.id == attachment.id }
        // Left behind it is only orphaned, and goes with the conversation.
        try? await capture.chatAPI()?.deleteChatAttachment(attachment.id)
    }

    private func refreshStaged(using api: JournalAPI) async {
        for photo in staged where photo.descriptionStatus == "running" {
            guard let fresh = try? await api.chatAttachment(photo.id),
                  let index = staged.firstIndex(where: { $0.id == photo.id }) else { continue }
            staged[index] = fresh
        }
    }

    var photoStatus: String? { ChatPhotoStatus.message(staged) }

    func attachmentData(_ id: String) async -> Data? {
        if let data = photoCache[id] { return data }
        guard let data = try? await capture.chatAPI()?.chatAttachmentData(id) else { return nil }
        photoCache[id] = data
        return data
    }

    // MARK: Voice messages

    /// Recording needs no server: stopping queues the clip, with whatever was
    /// typed and the photos staged, and the server answers it once it lands.
    func toggleRecording() async {
        if isRecording { stopRecording(); return }
        guard !recordingStarting else { return }
        recordingStarting = true
        defer { recordingStarting = false }
        notice = nil
        if capture.recorder.activeID != nil { capture.recorder.stop() }
        var item: ChatRecording?
        do {
            let free = (try FileManager.default.attributesOfFileSystem(forPath: capture.chatRecordings.root.path)[.systemFreeSize]
                        as? NSNumber)?.int64Value ?? 0
            guard free > 32 * 1024 * 1024 else { throw RecorderError.message("Free some device storage before recording.") }
            guard await AVAudioApplication.requestRecordPermission() else {
                throw RecorderError.message("Allow microphone access in Settings to record.")
            }
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
            try session.setActive(true)
            // Today's conversation if known; otherwise whichever is today's when it uploads.
            let begun = try capture.chatRecordings.begin(conversationID: conversation?.id, text: input,
                                                         attachmentIDs: staged.map(\.id))
            item = begun
            let audio = try AVAudioRecorder(url: capture.chatRecordings.audioURL(begun), settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44100,
                AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 64000,
            ])
            audio.delegate = self
            guard audio.prepareToRecord(), audio.record() else { throw RecorderError.message("Could not start the recorder.") }
            recorder = audio
            recordingID = begun.id
        } catch {
            if let item { try? capture.chatRecordings.remove(item) }
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            notice = error.localizedDescription
        }
    }

    func stopRecording() {
        guard let id = recordingID else { return }
        recordingID = nil
        recorder?.stop()
        recorder = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        do {
            if try capture.chatRecordings.finish(id) != nil {
                // The words and photos went with the clip.
                input = ""
                staged = []
            }
        } catch { notice = error.localizedDescription }
        capture.reload()
        capture.requestSync(manual: true)
    }

    @objc nonisolated private func interrupted(_ notification: Notification) {
        guard let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              type == AVAudioSession.InterruptionType.began.rawValue else { return }
        Task { @MainActor in
            guard self.isRecording else { return }
            self.stopRecording()
            self.notice = "The recording was cut short; what was said up to then is being sent."
        }
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.stopRecording()
        }
    }

    // MARK: Cards

    func resolve(messageID: String, proposal: ChatProposal, accept: Bool, data: [String: JSONValue]) async -> String? {
        guard let api = capture.chatAPI() else { return "Connect to your server to save this." }
        do {
            try await api.resolveProposal(messageID: messageID, proposalID: proposal.id, accept: accept, data: data)
            await refresh()
            return nil
        } catch { return error.localizedDescription }
    }

    // MARK: "Flashcard this"

    func draftCards(from note: String) async {
        guard let api = capture.chatAPI() else { return }
        do { noteCards += try await api.draftCards(from: note) } catch { notice = error.localizedDescription }
    }

    func approve(_ card: DraftCard, force: Bool = false) async {
        guard let api = capture.chatAPI() else { return }
        do {
            switch try await api.approveCard(card.id, force: force) {
            case let .duplicate(question, score): duplicateHint = (card.id, question, score)
            case .approved:
                duplicateHint = nil
                noteCards.removeAll { $0.id == card.id }
            }
        } catch { notice = error.localizedDescription }
    }

    func regenerate(_ card: DraftCard, direction: String) async -> Bool {
        guard let api = capture.chatAPI() else { return false }
        do {
            let replacements = try await api.regenerateCard(card.id, direction: direction)
            noteCards.removeAll { $0.id == card.id }
            noteCards += replacements
            return true
        } catch { notice = error.localizedDescription; return false }
    }

    func discard(_ card: DraftCard) async {
        do {
            try await capture.chatAPI()?.discardCard(card.id)
            noteCards.removeAll { $0.id == card.id }
            if duplicateHint?.cardID == card.id { duplicateHint = nil }
        } catch { notice = error.localizedDescription }
    }

    // MARK: Today's to-dos

    func refreshTodos() async {
        guard let api = capture.chatAPI(), let fresh = try? await api.chatTodos() else { return }
        todos = fresh
    }

    func toggle(_ todo: ChatTodo) async {
        await todoCall { try await $0.updateChatTodo(todo.id, done: !todo.done) }
    }

    func rename(_ todo: ChatTodo, to title: String) async {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != todo.title else { return }
        await todoCall { try await $0.updateChatTodo(todo.id, title: trimmed) }
    }

    func dismiss(_ todo: ChatTodo) async {
        await todoCall { try await $0.deleteChatTodo(todo.id) }
    }

    func promote(_ todo: ChatTodo, _ promotion: TodoPromotion) async -> Bool {
        await todoCall { try await $0.promoteChatTodo(todo.id, promotion) }
    }

    @discardableResult
    private func todoCall(_ call: (JournalAPI) async throws -> Void) async -> Bool {
        guard let api = capture.chatAPI() else { notice = "Connect to your server to change to-dos."; return false }
        do {
            try await call(api)
            await refreshTodos()
            return true
        } catch { notice = error.localizedDescription; return false }
    }
}
