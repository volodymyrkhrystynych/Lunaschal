import LunaschalCore
import SwiftUI

/// The Todo tab, as the desktop's Lifestyle tab lays it out: the overarching
/// goal and this week's, month's, quarter's and year's goals; up to ten daily
/// habits, each with its streak and last seven days; then the To-Do and
/// Archive lists. Edit reorders and deletes the habits; a to-do opens in a
/// form, and swipes to archive or delete. A goal opens in an editor, and
/// every goal change leaves a journal entry on the server.
/// Changes show at once and reach the server through the sync outbox.
struct TodoView: View {
    @ObservedObject var todo: TodoModel
    @State private var list = "todo"
    @State private var newTask = ""
    @State private var creating = false
    @State private var editing: TodoItem?
    @State private var renaming: DailyTask?
    @State private var renameText = ""
    @State private var cueing: DailyTask?
    @State private var cueText = ""
    @State private var explaining = false
    @State private var goalEditing: GoalEditing?
    @State private var expanded: Set<GoalHorizon> = [.week]
    @StateObject private var dictation: Dictation

    init(todo: TodoModel) {
        self.todo = todo
        _dictation = StateObject(wrappedValue: Dictation(capture: todo.capture) { [weak todo] in
            todo?.capture.chatAPI() != nil
        })
    }

    var body: some View {
        List {
            if let goals = todo.goals { goalsSection(goals) }
            dailySection
            todoSection
            if !todo.refusals.isEmpty {
                Section {
                    ForEach(todo.refusals, id: \.self) { Text($0).foregroundStyle(.red) }
                    Button("Dismiss") { todo.clearRefusals() }
                }
                .accessibilityIdentifier("todo-refusals")
            }
        }
        .navigationTitle("Todo")
        // The connection, the title and Edit share one line, leaving the
        // screen to the lists.
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { status }
            ToolbarItem(placement: .topBarTrailing) { EditButton() }
        }
        .alert(statusTitle, isPresented: $explaining) {
            Button("OK", role: .cancel) {}
        } message: { Text(statusDetail) }
        .refreshable {
            todo.capture.requestSync(manual: true)
            await todo.refresh()
        }
        // Fresh while it's on screen; a pass that touches to-dos refreshes it too.
        .task {
            while !Task.isCancelled {
                await todo.refresh()
                do { try await Task.sleep(for: .seconds(120)) } catch { return }
            }
        }
        .onDisappear { dictation.cancel() }
        .sheet(isPresented: $creating) {
            TodoEditor(heading: "New to-do", draft: TodoDraft()) { todo.create($0) }
        }
        .sheet(item: $editing) { item in
            TodoEditor(heading: "To-do", draft: TodoDraft(item), onSave: { todo.edit(item, $0) },
                       onDelete: { todo.delete(item) })
        }
        .sheet(item: $goalEditing) { editing in
            GoalEditor(editing: editing) { title, details in
                switch editing {
                case .overarching: todo.setOverarching(details)
                case let .new(horizon): todo.addGoal(horizon, title: title, details: details)
                case let .existing(goal): todo.edit(goal, title: title, details: details)
                }
            } onEnd: {
                if case let .existing(goal) = editing { todo.end(goal) }
            }
        }
        .alert("When will you do it?", isPresented: Binding(get: { cueing != nil }, set: { if !$0 { cueing = nil } }),
               presenting: cueing) { task in
            TextField("After my morning coffee", text: $cueText)
            Button("Save") { todo.setCue(task, cueText) }
            Button("Cancel", role: .cancel) {}
        } message: { task in
            Text("A cue ties “\(task.title)” to something you already do. Habits with one stick better.")
        }
        .alert("Rename habit", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } }),
               presenting: renaming) { task in
            TextField("Title", text: $renameText)
            Button("Save") { todo.rename(task, to: renameText) }
            Button("Cancel", role: .cancel) {}
        }
    }

    /// Whether the lists are the server's, and what's waiting to reach it.
    private var status: some View {
        // An HStack rather than a Label: the toolbar shows a Label's icon alone.
        Button { explaining = true } label: {
            HStack(spacing: 5) {
                Image(systemName: todo.loadProblem != nil ? "wifi.slash"
                      : todo.waiting > 0 ? "arrow.triangle.2.circlepath" : "checkmark.icloud")
                Text(statusTitle).lineLimit(1)
            }
            .font(.subheadline)
            .foregroundStyle(todo.loadProblem != nil ? .orange : .secondary)
            .fixedSize()
        }
        .accessibilityIdentifier("todo-status")
    }

    private var statusTitle: String {
        let waiting = todo.waiting > 0 ? "\(todo.waiting) waiting" : nil
        if let problem = todo.loadProblem { return [problem.short, waiting].compactMap { $0 }.joined(separator: " · ") }
        return waiting ?? "Synced"
    }

    private var statusDetail: String {
        let waiting = todo.waiting == 0 ? nil
            : todo.waiting == 1 ? "1 change is waiting to sync." : "\(todo.waiting) changes are waiting to sync."
        let state = todo.loadProblem?.detail ?? (waiting == nil ? "Your lists match your server." : nil)
        return [state, waiting].compactMap { $0 }.joined(separator: "\n\n")
    }

    private func goalsSection(_ goals: CurrentGoals) -> some View {
        Section {
            Button { goalEditing = .overarching(goals.overarching) } label: {
                if let goal = goals.overarching {
                    Text(goal.text).foregroundStyle(.primary).lineLimit(4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    Label("Set your overarching goal", systemImage: "scope")
                }
            }
            .accessibilityIdentifier("goal-overarching")
            ForEach(goals.horizons) { period in
                DisclosureGroup(isExpanded: Binding(
                    get: { expanded.contains(period.horizon) },
                    set: { if $0 { expanded.insert(period.horizon) } else { expanded.remove(period.horizon) } }
                )) {
                    ForEach(period.goals) { goal in
                        GoalRow(goal: goal) { goalEditing = .existing(goal) }
                            .swipeActions(edge: .trailing) {
                                Button("End", systemImage: "flag.checkered") { todo.end(goal) }.tint(.indigo)
                            }
                    }
                    Button { goalEditing = .new(period.horizon) } label: {
                        Label("Add a goal", systemImage: "plus")
                    }
                    .accessibilityIdentifier("goal-add-\(period.horizon.rawValue)")
                } label: {
                    HStack {
                        Text(period.horizon.heading)
                        Spacer()
                        if period.unset {
                            Text("Not set").font(.caption).foregroundStyle(.orange)
                        } else {
                            Text(period.label).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .accessibilityIdentifier("goal-period-\(period.horizon.rawValue)")
            }
        } header: {
            Text("Goals")
        }
    }

    private var dailySection: some View {
        Section {
            ForEach(todo.tasks) { task in
                DailyTaskRow(task: task, today: DayKey.of(Date())) { todo.toggle(task) }
                    .contextMenu {
                        Button("Rename", systemImage: "pencil") { renameText = task.title; renaming = task }
                        Button(task.cue == nil ? "Add a cue" : "Change cue", systemImage: "arrow.turn.down.right") {
                            cueText = task.cue ?? ""; cueing = task
                        }
                        Button("Delete", systemImage: "trash", role: .destructive) { todo.delete(task) }
                    }
            }
            .onMove { source, destination in todo.moveTasks(from: source, to: destination) }
            .onDelete { offsets in
                offsets.map { todo.tasks[$0] }.forEach(todo.delete)
            }
            if todo.tasks.count < DailyTask.limit {
                HStack {
                    TextField("Add a habit", text: $newTask)
                        .submitLabel(.done)
                        .onSubmit(addTask)
                        .accessibilityIdentifier("todo-daily-input")
                    Button("Add", action: addTask)
                        .disabled(newTask.trimmingCharacters(in: .whitespaces).isEmpty)
                        .accessibilityIdentifier("todo-daily-add")
                }
            }
        } header: {
            HStack {
                Text("Habits")
                Spacer()
                if let review = todo.yesterday, review.rating != nil || !review.items.isEmpty {
                    Text("Yesterday \(review.stars.isEmpty ? review.tally : review.stars)")
                        .foregroundStyle(.orange)
                        .accessibilityLabel("Yesterday: \(review.rating.map { "\($0) of 5" } ?? review.tally)")
                        .accessibilityIdentifier("todo-yesterday")
                }
            }
        } footer: {
            if todo.tasks.isEmpty { Text("Up to \(DailyTask.limit), ordered by importance. They reset at 4am.") }
        }
    }

    private var todoSection: some View {
        let items = todo.active(on: list)
        return Section {
            Picker("List", selection: $list) {
                Text(label("To-Do", count: todo.active(on: "todo").count)).tag("todo")
                Text(label("Archive", count: todo.active(on: "archive").count)).tag("archive")
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("todo-list-picker")
            if list == "todo" {
                // Borderless, or the row is one button and a tap on the mic opens the form.
                HStack {
                    Button { creating = true } label: { Label("Add to-do", systemImage: "plus") }
                        .accessibilityIdentifier("todo-add")
                    Spacer()
                    TodoMicButton(dictation: dictation, todo: todo)
                }
                .buttonStyle(.borderless)
                if let notice = todo.dictationNotice ?? dictation.error {
                    Text(notice).font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("todo-dictation-notice")
                }
            }
            ForEach(items) { item in
                TodoRow(todo: item, toggle: { todo.toggle(item) }, open: { editing = item })
                    .swipeActions(edge: .trailing) {
                        Button("Delete", systemImage: "trash", role: .destructive) { todo.delete(item) }
                        Button(item.isArchived ? "To-Do" : "Archive",
                               systemImage: item.isArchived ? "tray.and.arrow.up" : "archivebox") {
                            todo.move(item)
                        }.tint(.indigo)
                    }
            }
            if items.isEmpty {
                Text("Nothing on the list.").foregroundStyle(.secondary)
            }
        } header: {
            Text("To-Do")
        }
    }

    private func label(_ name: String, count: Int) -> String { count > 0 ? "\(name) \(count)" : name }

    private func addTask() {
        let title = newTask
        if todo.addTask(title) { newTask = "" }
    }
}

/// Say a to-do: tap to record, tap again to stop, and it is on the list.
private struct TodoMicButton: View {
    @ObservedObject var dictation: Dictation
    @ObservedObject var todo: TodoModel

    var body: some View {
        Button {
            Task {
                await dictation.toggle { clip in
                    Task { @MainActor in await todo.dictate(clip) }
                }
            }
        } label: {
            Group {
                if dictation.status == .starting || todo.dictating {
                    ProgressView()
                } else {
                    Image(systemName: dictation.status == .recording ? "stop.circle.fill" : "mic")
                        .foregroundStyle(dictation.status == .recording ? Color.red : Color.accentColor)
                }
            }
            .font(.title3)
            .frame(width: 36, height: 36)
        }
        .disabled(dictation.status == .starting || todo.dictating)
        .accessibilityLabel(dictation.status == .recording ? "Stop and add to-do" : "Dictate a to-do")
        .accessibilityIdentifier("todo-dictate")
    }
}

/// What the goal editor sheet is open on.
private enum GoalEditing: Identifiable {
    case overarching(Goal?)
    case new(GoalHorizon)
    case existing(Goal)

    var id: String {
        switch self {
        case .overarching: return "overarching"
        case let .new(horizon): return "new-\(horizon.rawValue)"
        case let .existing(goal): return goal.id
        }
    }
}

private struct GoalRow: View {
    let goal: Goal
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: 3) {
                Text(goal.title).foregroundStyle(.primary)
                if let details = goal.details, !details.isEmpty {
                    Text(details).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                if let streak = goal.streakLine {
                    Label(goal.reward.map { "\(streak) · \($0)" } ?? streak, systemImage: "flame")
                        .font(.caption).foregroundStyle(.orange).labelStyle(CompactLabel())
                }
                if goal.overdue {
                    Text("\(goal.periodLabel) is over — the review will grade it.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("goal-row")
    }
}

/// One editor for every goal: the overarching goal is a block of text; a
/// period goal is a title with optional details. A daily rule and prize are
/// set up in chat, where they can be talked through — they can't change later.
private struct GoalEditor: View {
    let editing: GoalEditing
    let onSave: (_ title: String, _ details: String) -> Void
    let onEnd: () -> Void
    @State private var title = ""
    @State private var details = ""
    @Environment(\.dismiss) private var dismiss

    private var isOverarching: Bool { if case .overarching = editing { return true } else { return false } }

    private var heading: String {
        switch editing {
        case .overarching: return "Overarching goal"
        case let .new(horizon): return "New goal · \(horizon.heading.lowercased())"
        case let .existing(goal): return goal.periodLabel
        }
    }

    private var canSave: Bool {
        !(isOverarching ? details : title).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                if isOverarching {
                    Section {
                        TextEditor(text: $details)
                            .frame(minHeight: 160)
                            .accessibilityIdentifier("goal-editor-text")
                    } footer: {
                        Text("What everything else is in service of. Saving writes a journal entry, so the old goal stays on record.")
                    }
                } else {
                    Section {
                        TextField("Goal", text: $title)
                            .accessibilityIdentifier("goal-editor-title")
                        TextField("Details", text: $details, axis: .vertical)
                            .lineLimit(1...6)
                    } footer: {
                        Text("Want a daily streak with a prize? Ask in Chat — the rule can't change once it's set.")
                    }
                    if case let .existing(goal) = editing {
                        if let criteria = goal.criteria {
                            Section("Daily rule") {
                                Text(criteria)
                                if let streak = goal.streakLine { Text(streak).foregroundStyle(.secondary) }
                                if let reward = goal.reward { Text("Prize: \(reward)").foregroundStyle(.secondary) }
                            }
                        }
                        Section {
                            Button("End goal") { onEnd(); dismiss() }
                        }
                    }
                }
            }
            .navigationTitle(heading)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(title, details); dismiss() }
                        .disabled(!canSave)
                        .accessibilityIdentifier("goal-editor-save")
                }
            }
            .onAppear {
                switch editing {
                case let .overarching(goal): details = goal?.text ?? ""
                case .new: break
                case let .existing(goal): title = goal.title; details = goal.details ?? ""
                }
            }
        }
    }
}

private struct DailyTaskRow: View {
    let task: DailyTask
    let today: String
    let toggle: () -> Void

    var body: some View {
        let streak = task.streak(today: today)
        HStack(spacing: 12) {
            CheckButton(done: task.done, id: "todo-daily-check", action: toggle)
            VStack(alignment: .leading, spacing: 1) {
                Text(task.title)
                    .strikethrough(task.done)
                    .foregroundStyle(task.done ? .secondary : .primary)
                if let cue = task.cue {
                    Text(cue).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            HabitWeekDots(week: task.week(today: today),
                          excused: HabitStreaks.days(endingAt: today, count: 7).map(task.isExcused(on:)))
            if streak > 0 {
                // A run whose grace day is spent: missing today too would end it.
                let risk = task.atRisk(today: today)
                Label("\(streak)", systemImage: risk ? "flame" : "flame.fill")
                    .labelStyle(CompactLabel())
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(risk ? .red : .orange)
                    .accessibilityIdentifier("todo-daily-streak")
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(streak > 0 ? "\(streak) day streak, best \(task.best(today: today))"
                            + (task.atRisk(today: today) ? ", missed yesterday" : "") : "")
        .accessibilityAddTraits(task.done ? .isSelected : [])
        .accessibilityAction(named: task.done ? "Not done" : "Done", toggle)
        .accessibilityIdentifier("todo-daily-row")
    }
}

/// The last seven days, oldest first, today last. An excused day is a ring:
/// it neither counted nor broke the streak.
private struct HabitWeekDots: View {
    let week: [Bool]
    var excused: [Bool] = []

    var body: some View {
        HStack(spacing: 3) {
            ForEach(Array(week.enumerated()), id: \.offset) { index, done in
                if !done, excused.indices.contains(index), excused[index] {
                    Circle().strokeBorder(Color.secondary.opacity(0.6), lineWidth: 1).frame(width: 6, height: 6)
                } else {
                    Circle()
                        .fill(done ? Color.accentColor : Color.secondary.opacity(0.25))
                        .frame(width: 6, height: 6)
                }
            }
        }
        .accessibilityHidden(true)
    }
}

private struct TodoRow: View {
    let todo: TodoItem
    let toggle: () -> Void
    let open: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            CheckButton(done: todo.done, id: "todo-check", action: toggle)
            Button(action: open) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(todo.title).foregroundStyle(.primary)
                    if let notes = todo.notes, !notes.isEmpty {
                        Text(notes).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    details
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("todo-row")
        }
    }

    @ViewBuilder private var details: some View {
        let due = TodoRules.dueLabel(todo)
        let flag = TodoRules.priorityFlag(todo.priority)
        let repeating = TodoRules.repeatLabel(todo.repeatInterval, todo.repeatUnit)
        if due != nil || flag != nil || repeating != nil {
            HStack(spacing: 8) {
                if let flag {
                    Label(flag.label, systemImage: "flag.fill")
                        .foregroundStyle(Self.color(todo.priority ?? 3))
                        .accessibilityLabel(flag.title)
                }
                if let due {
                    Label(due.label, systemImage: "calendar")
                        .foregroundStyle(due.overdue ? .red : .secondary)
                        .accessibilityLabel(due.overdue ? "Overdue, \(due.label)" : "Due \(due.label)")
                }
                if let repeating { Label(repeating, systemImage: "repeat").foregroundStyle(.secondary) }
            }
            .font(.caption)
            .labelStyle(CompactLabel())
        }
    }

    static func color(_ priority: Int) -> Color {
        switch priority {
        case 5: return .red
        case 4: return .orange
        case 2: return .cyan
        default: return .secondary
        }
    }
}

private struct CompactLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) { configuration.icon.imageScale(.small); configuration.title }
    }
}

private struct CheckButton: View {
    let done: Bool
    let id: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .font(.title3)
                .foregroundStyle(done ? Color.accentColor : .secondary)
        }
        .buttonStyle(.borderless)
        .accessibilityLabel(done ? "Done" : "Not done")
        .accessibilityIdentifier(id)
    }
}

/// The desktop's TodoForm, plus the list it's on when editing.
private struct TodoEditor: View {
    let heading: String
    @State var draft: TodoDraft
    let onSave: (TodoDraft) -> Void
    var onDelete: (() -> Void)?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Title", text: $draft.title)
                        .accessibilityIdentifier("todo-editor-title")
                    TextField("More information", text: $draft.notes, axis: .vertical)
                        .lineLimit(1...5)
                }
                Section {
                    Toggle("Due date", isOn: Binding(get: { draft.due != nil },
                                                     set: { draft.due = $0 ? (draft.due ?? Date()) : nil }))
                    if let due = draft.due {
                        DatePicker("Due", selection: Binding(get: { due }, set: { draft.due = $0 }), displayedComponents: .date)
                            .accessibilityIdentifier("todo-due-date")
                    }
                    Toggle("Repeat", isOn: Binding(get: { draft.repeatInterval != nil },
                                                   set: { draft.repeatInterval = $0 ? (draft.repeatInterval ?? 1) : nil }))
                    if let interval = draft.repeatInterval {
                        Stepper("Every \(interval)", value: Binding(get: { interval }, set: { draft.repeatInterval = $0 }), in: 1...365)
                        Picker("Unit", selection: $draft.repeatUnit) {
                            ForEach(TodoDraft.units, id: \.self) { Text(interval == 1 ? $0 : "\($0)s").tag($0) }
                        }
                        .pickerStyle(.segmented)
                    }
                }
                Section {
                    Picker("Priority", selection: $draft.priority) {
                        ForEach((1...5).reversed(), id: \.self) { Text(ChatTodo.priorities[$0] ?? "\($0)").tag($0) }
                    }
                    if onDelete != nil {
                        Picker("List", selection: $draft.list) {
                            Text("To-Do").tag("todo")
                            Text("Archive").tag("archive")
                        }
                    }
                }
                if let onDelete {
                    Section {
                        Button("Delete to-do", role: .destructive) {
                            onDelete(); dismiss()
                        }
                    }
                }
            }
            .navigationTitle(heading)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { onSave(draft); dismiss() }
                    .disabled(draft.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("todo-editor-save")
                }
            }
        }
    }
}
