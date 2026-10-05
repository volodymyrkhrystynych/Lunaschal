import SwiftUI
import LunaschalCore

// MARK: Confirm cards

/// The delegate's confirm cards for one reply (DelegateProposals.tsx). Each is
/// editable, because its values are a model's reading of a sentence; the
/// server checks whatever is accepted the same way either way.
struct ProposalCards: View {
    @ObservedObject var chat: ChatModel
    let messageID: String
    let proposals: [ChatProposal]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if proposals.contains(where: { $0.reconstructionDay != nil }) {
                Text("Suggested events from yesterday").font(.subheadline.bold())
            }
            ForEach(proposals) { proposal in
                if proposal.isPending {
                    PendingCard(chat: chat, messageID: messageID, proposal: proposal)
                } else {
                    Text(proposal.resolvedLabel).font(.subheadline).foregroundStyle(.secondary)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(.quaternary))
                        .accessibilityIdentifier("chat-card-resolved")
                }
            }
        }
    }
}

private struct PendingCard: View {
    @ObservedObject var chat: ChatModel
    let messageID: String
    let proposal: ChatProposal
    // Seeded once from what was staged; nothing is sent until accept.
    @State private var data: [String: JSONValue] = [:]
    @State private var seeded = false
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(proposal.headline).font(.subheadline.bold())
            if let evidence = proposal.evidence { Text(evidence).font(.caption).foregroundStyle(.secondary) }
            if !proposal.sources.isEmpty {
                DisclosureGroup("Supporting entries") {
                    ForEach(proposal.sources, id: \.id) { Text("\($0.label) · \($0.recordedAt)").frame(maxWidth: .infinity, alignment: .leading) }
                }
                .font(.caption).foregroundStyle(.secondary)
            }
            ProposalForm(kind: proposal.kind, data: $data, reconstructed: proposal.reconstructionDay != nil)
            HStack {
                Spacer()
                Button("Dismiss") { resolve(accept: false) }.disabled(busy)
                Button(busy ? "Saving…" : proposal.acceptLabel) { resolve(accept: true) }
                    .buttonStyle(.borderedProminent).disabled(busy)
                    .accessibilityIdentifier("chat-card-accept")
            }
            if let error { Text(error).font(.caption).foregroundStyle(.red) }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
        .onAppear { if !seeded { data = proposal.data; seeded = true } }
    }

    private func resolve(accept: Bool) {
        busy = true
        Task {
            // A card that failed validation stays open with the edits intact.
            error = await chat.resolve(messageID: messageID, proposal: proposal, accept: accept, data: data)
            busy = false
        }
    }
}

private struct ProposalForm: View {
    let kind: String
    @Binding var data: [String: JSONValue]
    let reconstructed: Bool

    var body: some View {
        switch kind {
        case "calendar": calendar
        case "calorie":
            field("Description", "description")
            numberField("Calories", "calories")
        case "food":
            field("Dish", "dish")
            field("Place", "place")
            numberField("Calories", "calories")
            numberField("Rating (1–5)", "rating")
            TextField("What you said about it", text: text("notes"), axis: .vertical).lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
            // Neither is editable: both come off the message when it's accepted.
            Text("Your photo and exactly what you said are saved with it.").font(.caption).foregroundStyle(.secondary)
        case "recipe":
            field("Title", "title")
            TextField("Content", text: text("content"), axis: .vertical).lineLimit(4...12).textFieldStyle(.roundedBorder)
            TextField("Tags (comma, separated)", text: tags).textFieldStyle(.roundedBorder).font(.caption)
        case "recipe_link":
            Text("\(data["dish"]?.text ?? "") → \(data["recipeTitle"]?.text ?? "")").font(.subheadline.weight(.medium))
        case "flashcards":
            field("Topic", "topic")
            Text("I'll generate atomic cards and queue them for your approval in the Learning tab.")
                .font(.caption).foregroundStyle(.secondary)
        default:
            EmptyView()
        }
    }

    @ViewBuilder private var calendar: some View {
        let allDay = data["allDay"]?.bool == true
        field("Title", "title")
        TextField("More information…", text: text("description")).textFieldStyle(.roundedBorder).font(.caption)
        if data["location"] != nil { field("Location", "location") }
        DatePicker("Date", selection: date("date"), displayedComponents: .date)
        // All day is the whole day, not merely untimed, so it clears the clocks.
        if !reconstructed {
            Toggle("All day", isOn: Binding(get: { allDay }, set: { on in
                data["allDay"] = .bool(on)
                if on { data["time"] = .null; data["endTime"] = .null }
            }))
        }
        if !allDay {
            TimeField(label: "From", value: clock("time"))
            TimeField(label: "To", value: clock("endTime"))
        }
        TextField("Tags (comma, separated)", text: tags).textFieldStyle(.roundedBorder).font(.caption)
        // The calendar's six colour categories, ticked by hand: a suggested
        // event has no description yet for the classifier to read.
        ScrollView(.horizontal, showsIndicators: false) {
            HStack {
                ForEach(EventCategories.all, id: \.id) { category in
                    let on = categories.contains(category.id)
                    Button(category.label) {
                        let next = on ? categories.filter { $0 != category.id } : categories + [category.id]
                        data["categoryTags"] = .array(next.map(JSONValue.string))
                    }
                    .buttonStyle(.bordered).tint(on ? .accentColor : .secondary).font(.caption)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
        }
    }

    private var categories: [String] { data["categoryTags"]?.array?.compactMap(\.string) ?? [] }

    private func field(_ label: String, _ key: String) -> some View {
        TextField(label, text: text(key)).textFieldStyle(.roundedBorder).accessibilityLabel(label)
    }

    private func numberField(_ label: String, _ key: String) -> some View {
        LabeledContent(label) {
            TextField("—", text: Binding(get: { data[key]?.text ?? "" }, set: { value in
                data[key] = Double(value.trimmingCharacters(in: .whitespaces)).map(JSONValue.number) ?? .null
            }))
            .keyboardType(.numberPad).multilineTextAlignment(.trailing).frame(maxWidth: 100)
            .accessibilityLabel(label)
        }
    }

    private func text(_ key: String) -> Binding<String> {
        Binding(get: { data[key]?.text ?? "" }, set: { data[key] = .string($0) })
    }

    private var tags: Binding<String> {
        Binding(get: { (data["tags"]?.array ?? []).compactMap(\.string).joined(separator: ", ") }, set: { value in
            data["tags"] = .array(value.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }.map(JSONValue.string))
        })
    }

    private static let dayFormat: DateFormatter = {
        let format = DateFormatter()
        format.calendar = Calendar(identifier: .gregorian)
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "yyyy-MM-dd"
        return format
    }()

    private func date(_ key: String) -> Binding<Date> {
        Binding(get: { data[key]?.string.flatMap(Self.dayFormat.date(from:)) ?? Date() },
                set: { data[key] = .string(Self.dayFormat.string(from: $0)) })
    }

    /// "HH:mm", or nil when no time is set.
    private func clock(_ key: String) -> Binding<String?> {
        Binding(get: { data[key]?.string.flatMap { $0.isEmpty ? nil : $0 } },
                set: { data[key] = $0.map(JSONValue.string) ?? .null })
    }
}

/// An optional "HH:mm" time: a wheel when set, an Add button when not.
private struct TimeField: View {
    let label: String
    @Binding var value: String?

    private static let format: DateFormatter = {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "HH:mm"
        return format
    }()

    var body: some View {
        if let value, let time = Self.format.date(from: String(value.prefix(5))) {
            HStack {
                DatePicker(label, selection: Binding(get: { time }, set: { self.value = Self.format.string(from: $0) }),
                           displayedComponents: .hourAndMinute)
                Button { self.value = nil } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.borderless).foregroundStyle(.secondary).accessibilityLabel("Clear \(label)")
            }
        } else {
            LabeledContent(label) {
                Button("Add time") { self.value = "09:00" }.buttonStyle(.borderless)
            }
        }
    }
}

// MARK: Today's to-dos

/// The bar above the composer: today's to-dos the assistant or the morning
/// briefing wrote, to tick, rename, dismiss or send to the permanent list.
struct ChatTodoBar: View {
    @ObservedObject var chat: ChatModel
    @State private var expanded = false
    @State private var promoting: ChatTodo?

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            Button { withAnimation { expanded.toggle() } } label: {
                HStack {
                    Text(ChatTodo.summary(chat.todos))
                    Spacer()
                    Image(systemName: "chevron.down").rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .font(.subheadline).foregroundStyle(.secondary)
                .padding(.horizontal).padding(.vertical, 8)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("chat-todos")
            if expanded {
                ScrollView {
                    VStack(spacing: 6) {
                        if chat.todos.isEmpty {
                            Text("Nothing added yet today.").font(.caption).foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        ForEach(chat.todos) { todo in
                            TodoRow(chat: chat, todo: todo) { promoting = todo }
                        }
                    }
                    .padding(.horizontal).padding(.bottom, 8)
                }
                .frame(maxHeight: 220)
            }
        }
        .background(.bar)
        .sheet(item: $promoting) { todo in PromoteTodo(chat: chat, todo: todo) }
    }
}

private struct TodoRow: View {
    @ObservedObject var chat: ChatModel
    let todo: ChatTodo
    let promote: () -> Void
    @State private var title = ""
    @FocusState private var editing: Bool

    var body: some View {
        HStack(spacing: 10) {
            Button { Task { await chat.toggle(todo) } } label: {
                Image(systemName: todo.done ? "checkmark.square.fill" : "square").font(.title3)
            }
            .buttonStyle(.borderless)
            .accessibilityLabel(todo.done ? "Mark not done" : "Mark done")
            VStack(alignment: .leading, spacing: 2) {
                TextField("Title", text: $title)
                    .focused($editing)
                    .strikethrough(todo.done)
                    .foregroundStyle(todo.done ? .secondary : .primary)
                    .submitLabel(.done)
                    .onSubmit { Task { await chat.rename(todo, to: title) } }
                    .onChange(of: editing) { _, now in if !now { Task { await chat.rename(todo, to: title) } } }
                if let notes = todo.notes, !notes.isEmpty {
                    Text(notes).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Button("→ Permanent", action: promote).font(.caption2).buttonStyle(.bordered)
            Button { Task { await chat.dismiss(todo) } } label: { Image(systemName: "xmark") }
                .buttonStyle(.borderless).foregroundStyle(.secondary).accessibilityLabel("Dismiss")
        }
        .padding(8)
        .background(Color(.secondarySystemBackground).opacity(todo.done ? 0.5 : 1), in: RoundedRectangle(cornerRadius: 10))
        .onAppear { title = todo.title }
        .onChange(of: todo.title) { _, value in title = value }
    }
}

/// "Save and send to permanent": the permanent list's fields, then the chat
/// to-do is replaced by a real one.
private struct PromoteTodo: View {
    @ObservedObject var chat: ChatModel
    let todo: ChatTodo
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var notes = ""
    @State private var hasDue = false
    @State private var due = Date()
    @State private var priority = 3
    @State private var list = "todo"
    @State private var repeats = false
    @State private var every = 1
    @State private var unit = "week"
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                TextField("Title…", text: $title)
                TextField("More information…", text: $notes, axis: .vertical)
                Toggle("Due date", isOn: $hasDue)
                if hasDue { DatePicker("Due", selection: $due, displayedComponents: .date) }
                Picker("List", selection: $list) {
                    Text("To-do").tag("todo")
                    Text("Archive").tag("archive")
                }
                Toggle("Repeats", isOn: $repeats)
                if repeats {
                    Stepper("Every \(every)", value: $every, in: 1...365)
                    Picker("Unit", selection: $unit) {
                        Text("days").tag("day"); Text("weeks").tag("week"); Text("months").tag("month")
                    }
                    .pickerStyle(.segmented)
                }
                Picker("Priority", selection: $priority) {
                    ForEach(1...5, id: \.self) { Text("\($0) — \(ChatTodo.priorities[$0] ?? "")").tag($0) }
                }
            }
            .navigationTitle("Send to permanent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Saving…" : "Save") {
                        saving = true
                        Task {
                            let trimmedNotes = notes.trimmingCharacters(in: .whitespacesAndNewlines)
                            let done = await chat.promote(todo, TodoPromotion(
                                title: title.trimmingCharacters(in: .whitespacesAndNewlines),
                                notes: trimmedNotes.isEmpty ? nil : trimmedNotes,
                                due: hasDue ? TodoPromotion.dueSeconds(due) : nil, priority: priority, list: list,
                                repeatInterval: repeats ? every : nil, repeatUnit: repeats ? unit : nil))
                            saving = false
                            if done { dismiss() }
                        }
                    }
                    .disabled(saving || title.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .onAppear {
                title = todo.title
                notes = todo.notes ?? ""
                priority = todo.priority
                if let iso = todo.due, let date = ISO8601DateFormatter().date(from: iso) { hasDue = true; due = date }
            }
        }
    }
}
