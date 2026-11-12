import LunaschalCore
import SwiftUI

// MARK: Task list

/// The Code page: ask a question or start a change, then everything that
/// needs you, what the agent is working on, and what is finished.
struct CodeTasksView: View {
    @ObservedObject var code: CodeModel
    @State private var draft = ""
    @State private var repoID: String?
    @State private var mode: CodeTaskMode = .plan
    @StateObject private var dictation: Dictation

    init(code: CodeModel) {
        self.code = code
        _dictation = StateObject(wrappedValue: Dictation(capture: code.capture) { [weak code] in
            code?.capture.chatAPI() != nil
        })
    }

    var body: some View {
        List {
            Section {
                Picker("Kind of task", selection: $mode) {
                    ForEach(CodeTaskMode.allCases, id: \.self) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("code-new-mode")
                HStack(alignment: .top) {
                    TextField(mode.placeholder, text: $draft, axis: .vertical)
                        .lineLimit(3...8)
                        .accessibilityIdentifier("code-new-prompt")
                    CodeMicButton(dictation: dictation, code: code, text: $draft)
                        .buttonStyle(.borderless)
                }
                if dictation.status == .recording {
                    Label("Recording — tap stop to transcribe", systemImage: "waveform")
                        .font(.caption).foregroundStyle(.red)
                }
                if code.repos.count > 1 {
                    Picker("Repository", selection: $repoID) {
                        ForEach(code.repos) { Text($0.name).tag(Optional($0.id)) }
                    }
                }
                Button {
                    Task { if await code.create(prompt: draft, repoID: repoID, mode: mode) { draft = "" } }
                } label: {
                    Label(mode.startLabel, systemImage: mode == .ask ? "questionmark.bubble" : "hammer")
                }
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || code.busy)
                .accessibilityIdentifier("code-new-start")
            } footer: {
                Text(mode.explanation)
            }
            if let problem = code.loadProblem {
                Label(problem, systemImage: "wifi.slash").font(.footnote).foregroundStyle(.secondary)
            }
            if let notice = code.notice ?? dictation.error {
                Text(notice).font(.footnote).foregroundStyle(.red)
            }
            section("Needs you", code.needsYou)
            section("Working", code.working)
            section("Finished", code.finished)
        }
        .refreshable { await code.refresh() }
        // Destinations are registered by ChatView, which is always on screen.
        .task { await code.poll() }
        .task { await code.askForNotifications() }
        .onAppear { if repoID == nil { repoID = code.repos.first(where: \.isDefaultRepo)?.id } }
        .onDisappear { dictation.cancel() }
    }

    @ViewBuilder private func section(_ title: String, _ tasks: [CodeTask]) -> some View {
        if !tasks.isEmpty {
            Section(title) {
                ForEach(tasks) { task in
                    NavigationLink(value: CodeRoute.task(task.id)) { CodeTaskRow(task: task) }
                        .accessibilityIdentifier("code-task")
                }
            }
        }
    }
}

enum CodeRoute: Hashable {
    case task(String)
    case diff(String)
}

private struct CodeTaskRow: View {
    let task: CodeTask

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(task.title).lineLimit(2)
            HStack(spacing: 8) {
                if task.isActive {
                    ProgressView().controlSize(.mini)
                        .accessibilityLabel("The agent is working")
                }
                CodeStatusBadge(task: task)
                if task.linesAdded + task.linesRemoved > 0 {
                    Text("+\(task.linesAdded) −\(task.linesRemoved)").font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                if let passed = task.testVerdict {
                    Image(systemName: passed ? "checkmark.circle" : "xmark.circle")
                        .foregroundStyle(passed ? .green : .red).font(.caption)
                        .accessibilityLabel(passed ? "Tests passed" : "Tests failed")
                }
            }
        }
    }
}

struct CodeStatusBadge: View {
    let task: CodeTask

    var body: some View {
        Text(task.statusLabel)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        switch task.status {
        case "awaiting_plan", "needs_input": return .orange
        case "answered", "done", "pushed": return .green
        case "error", "interrupted": return .red
        case "cancelled", "discarded": return .secondary
        default: return .blue
        }
    }
}

// MARK: One task

/// A task is read as a terminal session: what you asked, every tool the agent
/// ran, its thinking folded, the model calls, the test run — top to bottom on
/// a dark screen, newest at the bottom, with the reply line under it. The
/// lines come from `CodeTerminal` in Core, shared with the desktop's.
struct CodeTaskDetailView: View {
    @ObservedObject var code: CodeModel
    let taskID: String
    @State private var reply = ""
    @State private var confirmPush = false
    @State private var confirmDiscard = false
    @State private var confirmStop = false
    @StateObject private var dictation: Dictation

    init(code: CodeModel, taskID: String) {
        self.code = code
        self.taskID = taskID
        _dictation = StateObject(wrappedValue: Dictation(capture: code.capture) { [weak code] in
            code?.capture.chatAPI() != nil
        })
    }

    private var task: CodeTask? { code.task(taskID) }
    private var events: [CodeTaskEvent] { code.details[taskID]?.events ?? [] }
    private var lines: [CodeTerminalLine] { CodeTerminal.lines(events) }

    var body: some View {
        VStack(spacing: 0) {
            terminal
            if let task {
                actionBar(task)
                if let activity = CodeActivity.of(task, events: events) {
                    CodeActivityBar(activity: activity) { confirmStop = true }
                }
                if task.canMessage { composer(task) }
            }
        }
        .background(Terminal.background)
        .environment(\.colorScheme, .dark)
        .navigationTitle(task?.title ?? "Task")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbar }
        .task { await code.pollDetail(taskID) }
        .onDisappear { dictation.cancel() }
        .confirmationDialog("Push this branch and open a pull request?", isPresented: $confirmPush,
                            titleVisibility: .visible) {
            Button("Push & open PR") { Task { await code.push(taskID) } }
        } message: {
            Text("The machine account pushes agent/\(taskID) and opens a PR. Nothing reaches main until you merge it.")
        }
        .confirmationDialog("Discard this task?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { Task { await code.discard(taskID) } }
        } message: { Text("Its container and clone are deleted.") }
        // The banner's Stop sits just above the composer, so a stray tap must
        // not end a turn on its own.
        .confirmationDialog("Stop the agent?", isPresented: $confirmStop, titleVisibility: .visible) {
            Button("Stop", role: .destructive) { Task { await code.cancel(taskID) } }
        } message: { Text("This turn ends now, and its work is not tested.") }
    }

    private var terminal: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 6) {
                if let task {
                    HStack(spacing: 8) {
                        CodeStatusBadge(task: task)
                        Text("\(task.harness) · agent/\(task.id.suffix(6).lowercased())")
                            .foregroundStyle(Terminal.dim)
                    }
                    .padding(.bottom, 4)
                    ForEach(lines) { CodeTerminalRow(line: $0) }
                    if task.isActive { TerminalCursor(label: task.statusLabel) }
                } else {
                    ProgressView().tint(Terminal.dim).padding(.top, 60).frame(maxWidth: .infinity)
                }
            }
            .font(Terminal.font)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // A terminal follows its newest line.
        .defaultScrollAnchor(.bottom)
        .defaultScrollAnchor(.bottom, for: .sizeChanges)
        .accessibilityIdentifier("code-terminal")
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarTrailing) {
            if let task {
                Menu {
                    if task.canPush {
                        Button { confirmPush = true } label: { Label("Push & open PR", systemImage: "arrow.up.circle") }
                    }
                    if task.canCancel {
                        Button(role: .destructive) { Task { await code.cancel(taskID) } } label: {
                            Label("Cancel", systemImage: "stop.circle")
                        }
                    }
                    if task.canDiscard {
                        Button(role: .destructive) { confirmDiscard = true } label: {
                            Label("Discard", systemImage: "trash")
                        }
                    }
                    if let url = task.prUrl.flatMap(URL.init(string:)) {
                        Link(destination: url) { Label("Open pull request", systemImage: "safari") }
                    }
                } label: { Image(systemName: "ellipsis.circle") }
                .accessibilityIdentifier("code-task-menu")
            }
        }
    }

    /// What can be done right now, in one row above the reply line: the
    /// terminal is for reading, this is for the next step.
    @ViewBuilder private func actionBar(_ task: CodeTask) -> some View {
        let files = task.diffStat ?? []
        let hasActions = task.awaitsPlanApproval || task.canPlanFromAnswer || task.testVerdict != nil || !files.isEmpty || task.canPush
            || task.canContinue || task.prUrl != nil || code.notice != nil || dictation.error != nil
        if hasActions {
            VStack(alignment: .leading, spacing: 6) {
                if let notice = code.notice ?? dictation.error {
                    Text(notice).font(.caption).foregroundStyle(Terminal.failure)
                }
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        if task.awaitsPlanApproval {
                            Button { Task { await code.approve(taskID) } } label: {
                                Label("Approve & build", systemImage: "checkmark")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(code.busy)
                            .accessibilityIdentifier("code-approve")
                        }
                        if task.canPlanFromAnswer {
                            // Whatever is typed in the reply line says what to change.
                            Button {
                                let text = reply
                                reply = ""
                                Task { await code.planFromAnswer(taskID, text: text) }
                            } label: {
                                Label(reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                      ? "Plan this" : "Plan what I typed", systemImage: "hammer")
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(code.busy)
                            .accessibilityIdentifier("code-plan-from-answer")
                        }
                        if let passed = task.testVerdict {
                            Label(passed ? "Tests passed" : "Tests failed",
                                  systemImage: passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .font(.caption)
                                .foregroundStyle(passed ? Terminal.success : Terminal.failure)
                        }
                        if !files.isEmpty {
                            NavigationLink(value: CodeRoute.diff(taskID)) {
                                Text("\(files.count) file\(files.count == 1 ? "" : "s") +\(task.linesAdded) −\(task.linesRemoved)")
                                    .font(.caption.monospacedDigit())
                            }
                            .buttonStyle(.bordered)
                            .accessibilityIdentifier("code-open-diff")
                        }
                        if task.canPush {
                            Button { confirmPush = true } label: { Label("Push & open PR", systemImage: "arrow.up.circle") }
                                .buttonStyle(.bordered)
                                .accessibilityIdentifier("code-push")
                        }
                        if task.canContinue {
                            Button { Task { await code.resume(taskID) } } label: {
                                Label("Continue", systemImage: "play.circle")
                            }
                            .buttonStyle(.bordered)
                            .disabled(code.busy)
                        }
                        if let url = task.prUrl.flatMap(URL.init(string:)) {
                            Link(destination: url) { Label("Pull request", systemImage: "arrow.triangle.pull") }
                                .buttonStyle(.bordered)
                        }
                    }
                    .controlSize(.small)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(alignment: .top) { Divider() }
        }
    }

    private func composer(_ task: CodeTask) -> some View {
        HStack(alignment: .bottom, spacing: 8) {
            Text("❯").font(Terminal.font).foregroundStyle(Terminal.prompt).padding(.bottom, 9)
            TextField(task.awaitsPlanApproval ? "What should change in the plan?"
                      : task.pending?.question != nil ? "Your answer"
                      : task.canPlanFromAnswer ? "Ask a follow-up, or say what to change"
                      : "Ask for a follow-up change",
                      text: $reply, axis: .vertical)
                .font(Terminal.font)
                .lineLimit(1...5)
                .padding(.vertical, 8)
                .accessibilityIdentifier("code-reply")
            CodeMicButton(dictation: dictation, code: code, text: $reply)
            Button {
                let text = reply
                reply = ""
                Task {
                    if task.awaitsPlanApproval { await code.revise(taskID, text: text) }
                    else { await code.message(taskID, text: text) }
                }
            } label: { Image(systemName: "arrow.up.circle.fill").font(.title2).frame(width: 36, height: 36) }
            .disabled(reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || code.busy)
            .accessibilityLabel("Send")
        }
        .padding(.horizontal, 12).padding(.vertical, 4)
        .background(Terminal.bar)
        .overlay(alignment: .top) { Divider() }
    }
}

/// Says, where the reply line would be, that the agent is at work: what it is
/// doing and for how long, with Stop beside it. Without it a running follow-up
/// looked exactly like a finished task with the previous run's verdict showing.
struct CodeActivityBar: View {
    let activity: CodeActivity
    let stop: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            ProgressView().tint(Terminal.prompt)
            VStack(alignment: .leading, spacing: 2) {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    HStack(spacing: 4) {
                        Text(activity.headline).foregroundStyle(Terminal.text)
                        if let since = activity.since {
                            Text("· " + CodeActivity.elapsed(context.date.timeIntervalSince(since)))
                                .monospacedDigit()
                                .foregroundStyle(Terminal.prompt.opacity(0.8))
                        }
                    }
                    .font(.subheadline.weight(.medium))
                }
                if let doing = activity.doing {
                    Text(doing).font(Terminal.font).foregroundStyle(Terminal.dim).lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Spacer(minLength: 0)
            Button(role: .destructive, action: stop) { Label("Stop", systemImage: "stop.circle") }
                .buttonStyle(.bordered).controlSize(.small)
                .accessibilityIdentifier("code-stop")
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Terminal.prompt.opacity(0.12))
        .overlay(alignment: .top) { Divider() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("code-activity")
    }
}

/// The terminal's palette. Dark in both appearances: it is a terminal.
enum Terminal {
    static let background = Color(red: 0.07, green: 0.075, blue: 0.085)
    static let bar = Color(red: 0.11, green: 0.115, blue: 0.13)
    static let text = Color(white: 0.92)
    static let dim = Color(white: 0.55)
    static let prompt = Color(red: 0.45, green: 0.8, blue: 1.0)
    static let tool = Color(red: 0.5, green: 0.85, blue: 0.55)
    static let success = Color(red: 0.45, green: 0.85, blue: 0.5)
    static let failure = Color(red: 1.0, green: 0.45, blue: 0.42)
    static let font = Font.system(.footnote, design: .monospaced)
}

private struct CodeTerminalRow: View {
    let line: CodeTerminalLine
    @State private var expanded = false

    /// Folded lines show this much until tapped.
    private static let foldedOutputLines = 15

    var body: some View {
        switch line.style {
        case .thought:
            Text("∴ " + line.text)
                .italic()
                .foregroundStyle(Terminal.dim)
                .lineLimit(expanded ? nil : 2)
                .onTapGesture { expanded.toggle() }
                .accessibilityHint(expanded ? "Folds the thinking" : "Shows all of the thinking")
        case .output:
            let all = line.text.components(separatedBy: "\n")
            let folded = !expanded && all.count > Self.foldedOutputLines
            VStack(alignment: .leading, spacing: 2) {
                if folded {
                    Text("… \(all.count - Self.foldedOutputLines) more lines — tap to show")
                        .foregroundStyle(Terminal.dim)
                }
                Text(folded ? all.suffix(Self.foldedOutputLines).joined(separator: "\n") : line.text)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(Color(white: 0.72))
                    .textSelection(.enabled)
            }
            .onTapGesture { expanded.toggle() }
        default:
            Text(line.text)
                .foregroundStyle(color)
                .fontWeight(line.style == .prompt ? .semibold : .regular)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, line.style == .prompt ? 6 : 0)
        }
    }

    private var color: Color {
        switch line.style {
        case .prompt: return Terminal.prompt
        case .text: return Terminal.text
        case .tool: return Terminal.tool
        case .success: return Terminal.success
        case .failure: return Terminal.failure
        case .dim, .thought, .output: return Terminal.dim
        }
    }
}

/// The blinking block at the bottom while the agent is working.
private struct TerminalCursor: View {
    let label: String
    @State private var on = true

    var body: some View {
        HStack(spacing: 6) {
            Text("▌").foregroundStyle(Terminal.text).opacity(on ? 1 : 0)
            Text(label.lowercased() + "…").foregroundStyle(Terminal.dim)
        }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(550))
                on.toggle()
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Record, and on Stop the server's words are added to the text field to be
/// read over: an instruction to a coding agent is checked before it is sent,
/// unlike Chat's mic, which sends on Stop.
struct CodeMicButton: View {
    @ObservedObject var dictation: Dictation
    @ObservedObject var code: CodeModel
    @Binding var text: String

    var body: some View {
        Button {
            Task {
                await dictation.toggle { clip in
                    Task { @MainActor in
                        if let words = await code.transcribe(clip) { text = CodeDraft.appending(words, to: text) }
                    }
                }
            }
        } label: {
            Group {
                if dictation.status == .starting || code.transcribing {
                    ProgressView()
                } else {
                    Image(systemName: dictation.status == .recording ? "stop.circle.fill" : "mic")
                        .foregroundStyle(dictation.status == .recording ? Color.red : Color.accentColor)
                }
            }
            .font(.title3)
            .frame(width: 36, height: 36)
        }
        .disabled(dictation.status == .starting || code.transcribing)
        .accessibilityLabel(dictation.status == .recording ? "Stop and transcribe" : "Transcribe")
        .accessibilityIdentifier("code-transcribe")
    }
}

// MARK: Diff

/// One collapsible section per file, numbered lines, horizontal scroll for
/// long ones: a phone-width diff is read file by file, not as one wall.
struct CodeDiffView: View {
    @ObservedObject var code: CodeModel
    let taskID: String
    @State private var files: [DiffFile] = []
    @State private var commits: [String] = []
    @State private var truncated = false
    @State private var problem: String?
    @State private var loaded = false

    var body: some View {
        List {
            if let problem { Text(problem).foregroundStyle(.secondary) }
            if !commits.isEmpty {
                Section("Commits") {
                    ForEach(commits, id: \.self) { Text($0).font(.caption.monospaced()) }
                }
            }
            ForEach(files) { file in
                Section {
                    DisclosureGroup {
                        if file.isBinary {
                            Text("Binary file").font(.caption).foregroundStyle(.secondary)
                        } else if file.hunks.isEmpty {
                            Text(file.oldPath.map { "Renamed from \($0)" } ?? "No line changes")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        ForEach(Array(file.hunks.enumerated()), id: \.offset) { _, hunk in
                            CodeHunkView(hunk: hunk)
                        }
                    } label: {
                        HStack {
                            Text(file.path).font(.caption.monospaced()).lineLimit(2)
                            Spacer()
                            Text("+\(file.added) −\(file.removed)").font(.caption2.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("code-diff-file")
                }
            }
            if truncated {
                Text("The diff was too long to show in full.").font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Changes")
        .navigationBarTitleDisplayMode(.inline)
        .overlay { if !loaded { ProgressView() } }
        .task {
            do {
                let diff = try await code.diff(taskID)
                files = UnifiedDiff.parse(diff.patch)
                commits = diff.commits
                truncated = diff.truncated
            } catch {
                problem = error.localizedDescription
            }
            loaded = true
        }
    }
}

private struct CodeHunkView: View {
    let hunk: DiffHunk

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(hunk.header).font(.caption2.monospaced()).foregroundStyle(.secondary).padding(.vertical, 2)
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(hunk.lines.enumerated()), id: \.offset) { _, line in
                        HStack(spacing: 6) {
                            Text(line.newNumber.map(String.init) ?? line.oldNumber.map(String.init) ?? "")
                                .frame(width: 34, alignment: .trailing)
                                .foregroundStyle(.tertiary)
                            Text(marker(line) + line.text)
                        }
                        .font(.system(size: 11, design: .monospaced))
                        .padding(.trailing, 8)
                        .background(background(line))
                    }
                }
            }
        }
    }

    private func marker(_ line: DiffLine) -> String {
        switch line.kind {
        case .added: return "+"
        case .removed: return "−"
        case .context: return " "
        case .note: return ""
        }
    }

    private func background(_ line: DiffLine) -> Color {
        switch line.kind {
        case .added: return .green.opacity(0.15)
        case .removed: return .red.opacity(0.15)
        default: return .clear
        }
    }
}
