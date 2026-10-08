import AVFoundation
import PhotosUI
import SwiftUI
import LunaschalCore

/// The Chat tab, laid out like the desktop's: the day's conversation, any
/// drafted lesson cards, the to-do bar, then the composer.
struct ChatView: View {
    @ObservedObject var chat: ChatModel
    @ObservedObject var capture: CaptureModel

    var body: some View {
        VStack(spacing: 0) {
            ChatTranscript(chat: chat)
            if !chat.noteCards.isEmpty { NoteCardsPanel(chat: chat) }
            ChatTodoBar(chat: chat)
            ChatComposer(chat: chat, capture: capture)
        }
        .navigationTitle("Chat")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button { Task { await chat.startNewChat(carryContext: true) } } label: {
                        Label("New chat", systemImage: "arrow.right.circle")
                        Text("Compact this chat and carry its durable context into a new one")
                    }
                    Button { Task { await chat.startNewChat(carryContext: false) } } label: {
                        Label("Clean slate", systemImage: "eraser")
                        Text("Start without carrying a summary from this chat")
                    }
                } label: { Label("New chat", systemImage: "square.and.pencil") }
                .disabled(!chat.hasCurrentSegment || chat.isStreaming)
                .accessibilityIdentifier("chat-new")
            }
        }
        // Polls only while the tab is on screen, quickly while a reply is running.
        .task { await chat.poll() }
        // A voice message just uploaded, or a conversation that changed on the server.
        .onChange(of: capture.syncChanges) { _, changes in
            if changes.touches([SyncChanges.chat, "conversations", "messages"]) { Task { await chat.refresh() } }
        }
    }
}

// MARK: Transcript

private struct ChatTranscript: View {
    @ObservedObject var chat: ChatModel

    private var lastBreakID: String? { chat.messages.last(where: \.isBreak)?.id }
    private var trailingBreak: Bool { chat.messages.last?.isBreak == true }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if let problem = chat.loadProblem {
                        Label(problem, systemImage: "wifi.slash").font(.footnote).foregroundStyle(.secondary)
                            .accessibilityIdentifier("chat-problem")
                    }
                    if !chat.messages.contains(where: { $0.role != "system" }) && chat.liveMessageID == nil && !chat.isStreaming {
                        Welcome()
                    }
                    ForEach(chat.messages) { message in
                        if message.isBreak {
                            BreakDivider(message: message).id(message.id)
                        } else if message.role != "system", !(message.id == chat.liveMessageID && !chat.handedOff) {
                            MessageRow(chat: chat, message: message).id(message.id)
                        }
                    }
                    LiveReply(chat: chat)
                    Color.clear.frame(height: 1).id("end")
                    // Room for a fresh segment to start at the top after "New chat".
                    if trailingBreak { Color.clear.frame(height: 360) }
                }
                .padding()
            }
            .defaultScrollAnchor(.bottom)
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await chat.refresh() }
            .onChange(of: chat.messages.count) { _, _ in follow(proxy) }
            .onChange(of: chat.liveContent) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
            .onChange(of: chat.isStreaming) { _, _ in proxy.scrollTo("end", anchor: .bottom) }
        }
    }

    private func follow(_ proxy: ScrollViewProxy) {
        if chat.scrollToBreak, let lastBreakID {
            chat.scrollToBreak = false
            withAnimation { proxy.scrollTo(lastBreakID, anchor: .top) }
        } else {
            proxy.scrollTo("end", anchor: .bottom)
        }
    }
}

private struct Welcome: View {
    var body: some View {
        VStack(spacing: 8) {
            Text("Welcome to Lunaschal").font(.title3)
            Text("Start a conversation, ask me anything, or ask me to do something.")
            Text("Try: \"Quiz me on React hooks\", \"note to self: ...\", \"remind me to call the dentist\", or \"what's the latest on ...\"")
                .font(.footnote)
        }
        .multilineTextAlignment(.center).foregroundStyle(.secondary)
        .frame(maxWidth: .infinity).padding(.vertical, 40)
    }
}

private struct BreakDivider: View {
    let message: ChatMessage

    var body: some View {
        HStack(spacing: 8) {
            Rectangle().frame(height: 1).foregroundStyle(.quaternary)
            Text(label).font(.caption).foregroundStyle(.secondary).fixedSize()
            Rectangle().frame(height: 1).foregroundStyle(.quaternary)
        }
        .accessibilityElement(children: .combine)
    }

    private var label: String {
        var parts = ["New chat"]
        if message.status == "streaming" { parts.append("compacting…") }
        if message.status == "error" { parts.append("context handoff unavailable") }
        if let time = ChatTime.format(message.createdAt) { parts.append(time) }
        return parts.joined(separator: " · ")
    }
}

enum ChatTime {
    static func format(_ iso: String?) -> String? {
        guard let iso else { return nil }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = ISO8601DateFormatter().date(from: iso) ?? parser.date(from: iso) else { return nil }
        return date.formatted(date: .omitted, time: .shortened)
    }
}

private struct MessageRow: View {
    @ObservedObject var chat: ChatModel
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        let meta = message.meta
        HStack(alignment: .top) {
            if isUser { Spacer(minLength: 48) }
            VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
                if message.hasBody {
                    VStack(alignment: .leading, spacing: 8) {
                        if !message.photos.isEmpty {
                            // Its own width when it fits; a scroll view would take the whole row.
                            let strip = HStack { ForEach(message.photos) { ChatPhoto(chat: chat, attachment: $0, side: 160) } }
                            ViewThatFits(in: .horizontal) {
                                strip
                                ScrollView(.horizontal, showsIndicators: false) { strip }
                            }
                        }
                        ForEach(message.clips) { ClipRow(chat: chat, clip: $0) }
                        if !message.content.isEmpty {
                            if isUser { Text(message.content).textSelection(.enabled) }
                            else { MarkdownText(text: message.content) }
                        }
                    }
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .foregroundStyle(isUser ? Color.white : Color.primary)
                    .background(isUser ? Color.accentColor : Color(.secondarySystemBackground),
                                in: RoundedRectangle(cornerRadius: 16))
                    .accessibilityIdentifier(isUser ? "chat-user-message" : "chat-reply")
                }
                if message.isBlankReply {
                    Text(meta.truncated ? "No reply — it hit the output token limit while reasoning (Settings → llama.cpp)" : "No reply")
                        .font(.caption).italic().foregroundStyle(.secondary)
                }
                if meta.timedOut && message.hasBody {
                    Text("Cut off at the reply time limit (Settings → llama.cpp)").font(.caption).italic().foregroundStyle(.secondary)
                }
                if isUser, let raw = message.rawContent, !raw.isEmpty {
                    DisclosureGroup("As dictated") { Text(raw).frame(maxWidth: .infinity, alignment: .leading) }
                        .font(.caption).foregroundStyle(.secondary)
                }
                StepsDisclosure(steps: meta.steps, thinking: meta.thinking, live: message.status == "streaming")
                if message.role == "assistant" && message.status == "streaming" && !chat.isStreaming {
                    ThinkingLabel().font(.caption)
                }
                if message.role == "assistant" && message.status == "error" {
                    Text("Error: \(message.error ?? "The reply failed.")").font(.caption).foregroundStyle(.red)
                }
                ForEach(meta.sources, id: \.url) { source in
                    if let url = URL(string: source.url), ["http", "https"].contains(url.scheme?.lowercased()) {
                        Link(source.title?.isEmpty == false ? source.title! : source.url, destination: url)
                            .font(.caption).lineLimit(1)
                    }
                }
                if !meta.proposals.isEmpty {
                    ProposalCards(chat: chat, messageID: message.id, proposals: meta.proposals)
                }
                let time = ChatTime.format(message.stampedAt)
                if meta.savedAsJournal || time != nil {
                    HStack(spacing: 8) {
                        if meta.savedAsJournal { Text("Saved to journal") }
                        if let time { Text(time) }
                    }
                    .font(.caption2).foregroundStyle(.secondary)
                }
            }
            if !isUser { Spacer(minLength: 48) }
        }
    }
}

/// The reply this phone is streaming. It stays until the saved row has the
/// reply in it, so a finished reply never blinks out while the poll catches up.
private struct LiveReply: View {
    @ObservedObject var chat: ChatModel

    var body: some View {
        let showing = !chat.handedOff && (!chat.liveContent.isEmpty || !chat.liveThinking.isEmpty)
        let waiting = (chat.isStreaming || (chat.liveMessageID != nil && !chat.handedOff))
            && chat.liveContent.isEmpty && chat.liveThinking.isEmpty
        if showing || waiting {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    if !chat.liveContent.isEmpty {
                        MarkdownText(text: chat.liveContent)
                            .padding(.horizontal, 14).padding(.vertical, 9)
                            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                    } else if waiting {
                        ThinkingLabel().padding(.horizontal, 14).padding(.vertical, 9)
                            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                    }
                    StepsDisclosure(steps: chat.liveSteps, thinking: chat.liveThinking, live: true)
                    if showing && !chat.isStreaming && chat.liveMessageID != nil { ThinkingLabel().font(.caption) }
                }
                .accessibilityIdentifier("chat-live-reply")
                Spacer(minLength: 48)
            }
        }
        if let error = chat.streamError {
            Text("Error: \(error)").font(.caption).foregroundStyle(.red).accessibilityIdentifier("chat-error")
        }
    }
}

/// The desktop's cycling "Thinking / Pondering / …": a label that changes
/// reads as working, where a frozen one reads as stuck.
struct ThinkingLabel: View {
    private static let words = ["Thinking", "Pondering", "Mulling", "Considering", "Working it out", "Turning it over"]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1.8)) { context in
            let index = reduceMotion ? 0 : Int(context.date.timeIntervalSinceReferenceDate / 1.8) % Self.words.count
            Text(Self.words[index] + "…").foregroundStyle(.secondary)
                .contentTransition(.opacity)
                .animation(.easeInOut, value: index)
        }
        .accessibilityLabel("Thinking")
    }
}

/// The tool trace, collapsed even while it grows, with the reasoning inside it.
private struct StepsDisclosure: View {
    let steps: [AgentStep]
    let thinking: String
    let live: Bool

    var body: some View {
        if steps.isEmpty {
            Reasoning(text: thinking, live: live)
        } else {
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { Text("· \($0.element.label)") }
                    Reasoning(text: thinking, live: live)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } label: {
                Text("\(live ? "· " : "")\(steps.count) step\(steps.count == 1 ? "" : "s")\(live ? " so far" : "")")
            }
            .font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct Reasoning: View {
    let text: String
    let live: Bool

    var body: some View {
        if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            DisclosureGroup(live ? "· Reasoning" : "Reasoning") {
                Text(text).font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.leading, 8)
                    .overlay(alignment: .leading) { Rectangle().frame(width: 2).foregroundStyle(.quaternary) }
            }
            .font(.caption).foregroundStyle(.secondary)
        }
    }
}

// MARK: Markdown

/// A reply's Markdown: blocks from `MarkdownBlock`, inline styling from
/// Foundation's own parser.
struct MarkdownText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(MarkdownBlock.parse(text).enumerated()), id: \.offset) { block($0.element) }
        }
        .textSelection(.enabled)
    }

    @ViewBuilder private func block(_ block: MarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inline(text).font(level == 1 ? .title3.bold() : level == 2 ? .headline : .subheadline.bold())
        case let .paragraph(text):
            inline(text)
        case let .listItem(marker, indent, text):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker).monospacedDigit()
                inline(text)
            }
            .padding(.leading, CGFloat(indent) * 16)
        case let .quote(text):
            inline(text).foregroundStyle(.secondary).padding(.leading, 10)
                .overlay(alignment: .leading) { Rectangle().frame(width: 3).foregroundStyle(.tertiary) }
        case let .code(_, text):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(text).font(.system(.footnote, design: .monospaced)).padding(8)
            }
            .background(Color(.tertiarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        case .rule:
            Divider()
        case let .table(rows):
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                        GridRow { ForEach(Array(row.enumerated()), id: \.offset) { inline($0.element).bold(index == 0) } }
                        if index == 0 { Divider() }
                    }
                }
            }
            .font(.footnote)
        }
    }

    private func inline(_ text: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        if let styled = try? AttributedString(markdown: text, options: options) { return Text(styled) }
        return Text(text)
    }
}

// MARK: Attachments

/// A chat photo. Its file needs the session cookie, so it is fetched here
/// rather than handed to AsyncImage.
private struct ChatPhoto: View {
    @ObservedObject var chat: ChatModel
    let attachment: ChatAttachment
    let side: CGFloat
    @State private var image: UIImage?
    @State private var enlarged = false

    var body: some View {
        Group {
            if let image {
                Button { enlarged = true } label: {
                    Image(uiImage: image).resizable().scaledToFill()
                }
                .buttonStyle(.plain)
            } else {
                Rectangle().fill(.quaternary).overlay { ProgressView() }
            }
        }
        .frame(width: side, height: side)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        // The reading is the photo's description, and all the model ever saw of it.
        .accessibilityLabel(attachment.description ?? "Attached photo")
        .task(id: attachment.id) {
            if image == nil, let data = await chat.attachmentData(attachment.id) { image = UIImage(data: data) }
        }
        .sheet(isPresented: $enlarged) {
            NavigationStack {
                ScrollView {
                    VStack(alignment: .leading) {
                        if let image { Image(uiImage: image).resizable().scaledToFit() }
                        if let description = attachment.description { Text(description).font(.footnote).foregroundStyle(.secondary) }
                    }
                    .padding()
                }
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { enlarged = false } } }
            }
        }
    }
}

private struct ClipRow: View {
    @ObservedObject var chat: ChatModel
    let clip: ChatAttachment
    @StateObject private var player = ClipPlayer()

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Button {
                Task { await player.toggle { await chat.attachmentData(clip.id) } }
            } label: {
                Label(player.playing ? "Stop" : "Voice message", systemImage: player.playing ? "stop.fill" : "play.fill")
            }
            .buttonStyle(.borderless)
            .tint(.primary)
            if clip.transcriptStatus == "running" { Text("Transcribing…").font(.caption).opacity(0.7) }
            if clip.transcriptStatus == "error" {
                Text("Couldn't transcribe this\(clip.transcriptError.map { " — \($0)" } ?? ""). The recording is saved.")
                    .font(.caption).opacity(0.9)
            }
        }
    }
}

@MainActor
private final class ClipPlayer: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var playing = false
    private var player: AVAudioPlayer?

    func toggle(load: () async -> Data?) async {
        if playing { player?.stop(); playing = false; return }
        guard let data = await load(), let audio = try? AVAudioPlayer(data: data) else { return }
        try? AVAudioSession.sharedInstance().setCategory(.playback)
        try? AVAudioSession.sharedInstance().setActive(true)
        audio.delegate = self
        player = audio
        playing = audio.play()
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.playing = false }
    }
}

// MARK: "Flashcard this"

private struct NoteCardsPanel: View {
    @ObservedObject var chat: ChatModel
    @State private var changing: String?
    @State private var direction = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Text("Save \(chat.noteCards.count > 1 ? "these lessons" : "this lesson") to Learning?").font(.subheadline.bold())
                ForEach(chat.noteCards) { card in
                    VStack(alignment: .leading, spacing: 6) {
                        MarkdownText(text: card.question)
                        MarkdownText(text: card.answer).foregroundStyle(.secondary)
                        if let hint = chat.duplicateHint, hint.cardID == card.id {
                            Text("This looks \(Int(hint.score * 100))% similar to an existing card: \"\(hint.question)\"")
                                .font(.caption).foregroundStyle(.orange)
                            Button("Save anyway") { Task { await chat.approve(card, force: true) } }.font(.caption)
                        }
                        if changing == card.id {
                            TextField("What should change?", text: $direction).textFieldStyle(.roundedBorder)
                            HStack {
                                Button("Cancel") { changing = nil; direction = "" }
                                Spacer()
                                Button("Update") {
                                    Task { if await chat.regenerate(card, direction: direction) { changing = nil; direction = "" } }
                                }
                                .disabled(direction.trimmingCharacters(in: .whitespaces).isEmpty)
                            }
                        } else {
                            HStack {
                                Button("Discard") { Task { await chat.discard(card) } }
                                Spacer()
                                Button("Request changes") { changing = card.id; direction = "" }
                                Button("Approve") { Task { await chat.approve(card) } }.buttonStyle(.borderedProminent)
                            }
                            .font(.callout)
                        }
                    }
                    .padding(10)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding()
        }
        .frame(maxHeight: 260)
        .background(.bar)
    }
}

// MARK: Composer

private struct ChatComposer: View {
    @ObservedObject var chat: ChatModel
    @ObservedObject var capture: CaptureModel
    @State private var photoItems: [PhotosPickerItem] = []
    @FocusState private var typing: Bool

    private var canSend: Bool {
        (!chat.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !chat.staged.isEmpty) && !chat.isStreaming
    }
    private var queued: [ChatRecording] { capture.chatRecordingQueue }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !chat.staged.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(chat.staged) { photo in
                            ChatPhoto(chat: chat, attachment: photo, side: 64)
                                .overlay { if photo.descriptionStatus == "running" { Color.black.opacity(0.35).clipShape(RoundedRectangle(cornerRadius: 10)) } }
                                .overlay(alignment: .topTrailing) {
                                    Button { Task { await chat.removeStaged(photo) } } label: {
                                        Image(systemName: "xmark.circle.fill").symbolRenderingMode(.palette)
                                            .foregroundStyle(.white, .black.opacity(0.6))
                                    }
                                    .accessibilityLabel("Remove photo")
                                }
                        }
                    }
                }
            }
            statusLines
            HStack(alignment: .bottom, spacing: 8) {
                PhotosPicker(selection: $photoItems, matching: .images) {
                    Image(systemName: "photo").frame(width: 36, height: 36)
                }
                .disabled(chat.isStreaming || chat.uploadingPhotos)
                .accessibilityLabel("Attach a photo")
                TextField("Type a message...", text: $chat.input, axis: .vertical)
                    .lineLimit(1...6)
                    .focused($typing)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
                    .disabled(chat.isStreaming)
                    .accessibilityIdentifier("chat-input")
                Button { Task { await chat.toggleRecording() } } label: {
                    Image(systemName: chat.isRecording ? "stop.circle.fill" : "mic")
                        .font(.title3).frame(width: 36, height: 36)
                        .foregroundStyle(chat.isRecording ? .red : .accentColor)
                }
                .disabled(chat.isStreaming || chat.recordingStarting)
                .accessibilityLabel(chat.isRecording ? "Stop and send" : "Speak to send")
                .accessibilityIdentifier("chat-record")
                Button { typing = false; Task { await chat.send() } } label: {
                    Image(systemName: "arrow.up.circle.fill").font(.title).frame(width: 36, height: 36)
                }
                .disabled(!canSend)
                .accessibilityLabel("Send")
                .accessibilityIdentifier("chat-send")
            }
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
        .onChange(of: photoItems) { _, items in
            guard !items.isEmpty else { return }
            photoItems = []
            Task { await chat.attach(await Self.load(items)) }
        }
    }

    @ViewBuilder private var statusLines: some View {
        let pending = queued.filter { $0.state == .pending }
        let failed = queued.filter { $0.state == .failed }
        VStack(alignment: .leading, spacing: 2) {
            if chat.isRecording { Label("Recording — tap stop to send", systemImage: "waveform").foregroundStyle(.red) }
            if let notice = chat.notice { Text(notice).foregroundStyle(.red) }
            if chat.uploadingPhotos { Text("Attaching…") }
            if let status = chat.photoStatus { Text(status) }
            // The clip is on the phone either way; this says it hasn't landed yet.
            if !pending.isEmpty {
                Text(pending.count == 1 ? "Sending your recording…" : "Sending \(pending.count) recordings…")
                    .accessibilityIdentifier("chat-recording-pending")
            }
            ForEach(failed) { item in
                HStack {
                    Text("A recording wasn't accepted: \(item.lastError ?? "refused by the server")").foregroundStyle(.red)
                    Button("Remove", role: .destructive) { capture.discard(item) }
                }
            }
        }
        .font(.caption).foregroundStyle(.secondary)
    }

    private static func load(_ items: [PhotosPickerItem]) async -> [(data: Data, filename: String, contentType: String)] {
        var photos: [(data: Data, filename: String, contentType: String)] = []
        for item in items {
            guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
            let type = item.supportedContentTypes.first { $0.conforms(to: .image) }
            photos.append((data, "photo.\(type?.preferredFilenameExtension ?? "jpg")", type?.preferredMIMEType ?? "image/jpeg"))
        }
        return photos
    }
}
