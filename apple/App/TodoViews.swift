import LunaschalCore
import SwiftUI

/// The Todo tab, as the desktop's TasksSection lays it out: up to four daily
/// tasks above the To-Do and Archive lists. Edit reorders and deletes the
/// daily tasks; a to-do opens in a form, and swipes to archive or delete.
/// Changes show at once and reach the server through the sync outbox.
struct TodoView: View {
    @ObservedObject var todo: TodoModel
    @State private var list = "todo"
    @State private var newTask = ""
    @State private var creating = false
    @State private var editing: TodoItem?
    @State private var renaming: DailyTask?
    @State private var renameText = ""
    @State private var explaining = false

    var body: some View {
        List {
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
        .task { await todo.refresh() }
        .sheet(isPresented: $creating) {
            TodoEditor(heading: "New to-do", draft: TodoDraft()) { todo.create($0) }
        }
        .sheet(item: $editing) { item in
            TodoEditor(heading: "To-do", draft: TodoDraft(item), onSave: { todo.edit(item, $0) },
                       onDelete: { todo.delete(item) })
        }
        .alert("Rename daily task", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } }),
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

    private var dailySection: some View {
        Section {
            ForEach(todo.tasks) { task in
                DailyTaskRow(task: task) { todo.toggle(task) }
                    .contextMenu {
                        Button("Rename", systemImage: "pencil") { renameText = task.title; renaming = task }
                        Button("Delete", systemImage: "trash", role: .destructive) { todo.delete(task) }
                    }
            }
            .onMove { source, destination in todo.moveTasks(from: source, to: destination) }
            .onDelete { offsets in
                offsets.map { todo.tasks[$0] }.forEach(todo.delete)
            }
            if todo.tasks.count < DailyTask.limit {
                HStack {
                    TextField("Add a daily task", text: $newTask)
                        .submitLabel(.done)
                        .onSubmit(addTask)
                        .accessibilityIdentifier("todo-daily-input")
                    Button("Add", action: addTask)
                        .disabled(newTask.trimmingCharacters(in: .whitespaces).isEmpty)
                        .accessibilityIdentifier("todo-daily-add")
                }
            }
        } header: {
            Text("Daily tasks")
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
                Button { creating = true } label: { Label("Add to-do", systemImage: "plus") }
                    .accessibilityIdentifier("todo-add")
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

private struct DailyTaskRow: View {
    let task: DailyTask
    let toggle: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            CheckButton(done: task.done, id: "todo-daily-check", action: toggle)
            Text(task.title)
                .strikethrough(task.done)
                .foregroundStyle(task.done ? .secondary : .primary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(task.done ? .isSelected : [])
        .accessibilityAction(named: task.done ? "Not done" : "Done", toggle)
        .accessibilityIdentifier("todo-daily-row")
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
