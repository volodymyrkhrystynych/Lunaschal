import LunaschalCore
import SwiftUI

// MARK: Task list

/// The Code page: start a task, then everything that needs you, what the
/// agent is working on, and what is finished.
struct CodeTasksView: View {
    @ObservedObject var code: CodeModel
    @State private var draft = ""
    @State private var repoID: String?

    var body: some View {
        List {
            Section {
                TextField("Describe a change for the agent…", text: $draft, axis: .vertical)
                    .lineLimit(3...8)
                    .accessibilityIdentifier("code-new-prompt")
                if code.repos.count > 1 {
                    Picker("Repository", selection: $repoID) {
                        ForEach(code.repos) { Text($0.name).tag(Optional($0.id)) }
                    }
                }
                Button {
                    Task { if await code.create(prompt: draft, repoID: repoID) { draft = "" } }
                } label: {
                    Label("Start planning", systemImage: "hammer")
                }
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || code.busy)
                .accessibilityIdentifier("code-new-start")
            } footer: {
                Text("The agent explores the code and writes a plan first. Nothing changes until you approve it.")
            }
            if let problem = code.loadProblem {
                Label(problem, systemImage: "wifi.slash").font(.footnote).foregroundStyle(.secondary)
            }
            if let notice = code.notice {
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
                CodeStatusBadge(task: task)
                if task.linesAdded + task.linesRemoved > 0 {
                    Text("+\(task.linesAdded) −\(task.linesRemoved)").font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                if let passed = task.testsPassed {
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
        case "done", "pushed": return .green
        case "error", "interrupted": return .red
        case "cancelled", "discarded": return .secondary
        default: return .blue
        }
    }
}

// MARK: One task

struct CodeTaskDetailView: View {
    @ObservedObject var code: CodeModel
    let taskID: String
    @State private var reply = ""
    @State private var showSteps = false
    @State private var confirmPush = false
    @State private var confirmDiscard = false

    private var task: CodeTask? { code.task(taskID) }
    private var events: [CodeTaskEvent] { code.details[taskID]?.events ?? [] }

    var body: some View {
        ScrollView {
            if let task {
                VStack(alignment: .leading, spacing: 16) {
                    header(task)
                    if let notice = code.notice { Text(notice).font(.footnote).foregroundStyle(.red) }
                    if let question = task.pending?.question { questionCard(question) }
                    if let plan = task.plan { planCard(task, plan) }
                    if task.status == "done" || task.status == "needs_input" || task.status == "pushed"
                        || task.diffStat != nil || task.testExit != nil {
                        resultCard(task)
                    }
                    if task.canContinue { continueCard(task) }
                    activity
                }
                .padding()
            } else {
                ProgressView().padding(.top, 60)
            }
        }
        .safeAreaInset(edge: .bottom) { if let task, task.canMessage { composer(task) } }
        .navigationTitle(task?.title ?? "Task")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbar }
        .task { await code.pollDetail(taskID) }
        .confirmationDialog("Push this branch and open a pull request?", isPresented: $confirmPush,
                            titleVisibility: .visible) {
            Button("Push & open PR") { Task { await code.push(taskID) } }
        } message: {
            Text("The machine account pushes agent/\(taskID) and opens a PR. Nothing reaches main until you merge it.")
        }
        .confirmationDialog("Discard this task?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { Task { await code.discard(taskID) } }
        } message: { Text("Its container and clone are deleted.") }
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

    private func header(_ task: CodeTask) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                CodeStatusBadge(task: task)
                Text(task.harness).font(.caption.monospaced()).foregroundStyle(.secondary)
                if task.isActive { ProgressView().controlSize(.small) }
            }
            Text(task.prompt).font(.callout)
            if let error = task.error { Text(error).font(.footnote).foregroundStyle(.red) }
        }
    }

    private func planCard(_ task: CodeTask, _ plan: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Plan", systemImage: "list.number").font(.headline)
            MarkdownText(text: plan)
            if task.awaitsPlanApproval {
                HStack {
                    Button { Task { await code.approve(taskID) } } label: {
                        Label("Approve & build", systemImage: "checkmark")
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("code-approve")
                    Text("or reply below to revise").font(.caption).foregroundStyle(.secondary)
                }
                .disabled(code.busy)
            }
        }
        .padding()
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    }

    private func questionCard(_ question: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("The agent asks", systemImage: "questionmark.bubble").font(.headline)
            Text(question)
            Text("Answer below; it carries on from there.").font(.caption).foregroundStyle(.secondary)
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityIdentifier("code-question")
    }

    private func resultCard(_ task: CodeTask) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Result", systemImage: "doc.text.magnifyingglass").font(.headline)
            if let summary = task.summary { MarkdownText(text: summary) }
            if let passed = task.testsPassed {
                DisclosureGroup {
                    ScrollView(.horizontal) {
                        Text(task.testOutput ?? "").font(.system(.caption2, design: .monospaced))
                            .textSelection(.enabled)
                    }
                } label: {
                    Label(passed ? "Tests passed" : "Tests failed (exit \(task.testExit ?? -1))",
                          systemImage: passed ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(passed ? .green : .red)
                }
            }
            if let files = task.diffStat, !files.isEmpty {
                NavigationLink(value: CodeRoute.diff(taskID)) {
                    HStack {
                        Label("\(files.count) file\(files.count == 1 ? "" : "s") changed", systemImage: "plusminus")
                        Spacer()
                        Text("+\(task.linesAdded) −\(task.linesRemoved)").font(.caption.monospacedDigit())
                    }
                }
                .accessibilityIdentifier("code-open-diff")
            }
            if task.canPush {
                Button { confirmPush = true } label: { Label("Push & open PR", systemImage: "arrow.up.circle") }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("code-push")
            }
            if let url = task.prUrl.flatMap(URL.init(string:)) {
                Link(destination: url) { Label("Pull request", systemImage: "arrow.triangle.pull") }
            }
        }
        .padding()
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
    }

    private func continueCard(_ task: CodeTask) -> some View {
        Button { Task { await code.resume(taskID) } } label: {
            Label("Continue from where it stopped", systemImage: "play.circle")
        }
        .buttonStyle(.bordered)
    }

    private var activity: some View {
        let shown = events.filter { showSteps || !$0.isDetail }
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Activity").font(.headline)
                Spacer()
                Toggle("Model & tool steps", isOn: $showSteps).toggleStyle(.button).font(.caption)
            }
            ForEach(shown) { event in
                CodeEventRow(event: event)
            }
            if shown.isEmpty { Text("Nothing yet.").font(.footnote).foregroundStyle(.secondary) }
        }
    }

    private func composer(_ task: CodeTask) -> some View {
        HStack(alignment: .bottom) {
            TextField(task.awaitsPlanApproval ? "What should change in the plan?"
                      : task.pending?.question != nil ? "Your answer" : "Ask for a follow-up change",
                      text: $reply, axis: .vertical)
                .lineLimit(1...5)
                .textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("code-reply")
            Button {
                let text = reply
                reply = ""
                Task {
                    if task.awaitsPlanApproval { await code.revise(taskID, text: text) }
                    else { await code.message(taskID, text: text) }
                }
            } label: { Image(systemName: "arrow.up.circle.fill").font(.title2) }
            .disabled(reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || code.busy)
            .accessibilityLabel("Send")
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }
}

private struct CodeEventRow: View {
    let event: CodeTaskEvent

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 18)
            if event.kind == "message", let text = event.text {
                MarkdownText(text: text)
            } else {
                Text(event.label)
                    .font(event.isDetail ? .caption : .callout)
                    .foregroundStyle(event.isDetail ? .secondary : .primary)
            }
        }
    }

    private var icon: String {
        switch event.kind {
        case "user": return "person"
        case "message": return "text.bubble"
        case "tool_call", "tool_result": return "wrench.and.screwdriver"
        case "proxy": return "cpu"
        case "permission": return "lock"
        case "test": return "checkmark.seal"
        case "plan": return "checklist"
        case "thought": return "brain"
        default: return "circle.fill"
        }
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
