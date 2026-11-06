import AppIntents
import LunaschalCore
import SwiftUI
import WidgetKit

// The iPhone's Home Screen widgets: Habits (the daily tasks, ticked from the
// Home Screen) and To-Do (the open to-dos, due ones first). The lists come
// from the snapshot the app keeps in the App Group, refreshed from the server
// here when the app's sign-in copy is there; a tap is queued in the group,
// shown at once, and sent from here, or by the app the next time it runs.
// The rules live in LunaschalCore's TodoWidget.swift, where they are tested.

@main
struct LunaschalWidgets: WidgetBundle {
    var body: some Widget {
        HabitsWidget()
        TodosWidget()
    }
}

// MARK: - The shared files

enum WidgetFiles {
    /// Nil without the App Group, which is every unsigned simulator build.
    static func store() -> TodoWidgetStore? {
        SharedSignIn.container().flatMap { try? TodoWidgetStore(container: $0) }
    }

    /// The app's session, if it has handed one over.
    static func api() -> JournalAPI? {
        guard let session = SharedSignIn.read() else { return nil }
        return try? JournalAPI(server: session.server, token: session.token, allowCellular: true)
    }

    /// A widget gets seconds, not minutes: past this the copy it has is drawn.
    static func syncBriefly(_ store: TodoWidgetStore) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await store.sync(using: api()) }
            group.addTask { try? await Task.sleep(for: .seconds(8)) }
            await group.next()
            group.cancelAll()
        }
    }
}

// MARK: - Timeline

struct TodoEntry: TimelineEntry {
    let date: Date
    let lists: TodoLists
    /// False without the App Group: the widget says to open the app.
    let available: Bool
}

struct TodoProvider: TimelineProvider {
    func placeholder(in context: Context) -> TodoEntry { TodoEntry(date: Date(), lists: .sample, available: true) }

    func getSnapshot(in context: Context, completion: @escaping (TodoEntry) -> Void) {
        if context.isPreview { return completion(placeholder(in: context)) }
        let store = WidgetFiles.store()
        completion(TodoEntry(date: Date(), lists: store?.display(at: Date()) ?? TodoLists(), available: store != nil))
    }

    /// Now, and the next 4am, when habits untick and tomorrow's to-dos come
    /// due; asked again within the half hour for what changed elsewhere.
    func getTimeline(in context: Context, completion: @escaping (Timeline<TodoEntry>) -> Void) {
        Task {
            let now = Date()
            guard let store = WidgetFiles.store() else {
                return completion(Timeline(entries: [TodoEntry(date: now, lists: TodoLists(), available: false)],
                                           policy: .never))
            }
            await WidgetFiles.syncBriefly(store)
            let rollover = TodoWidgetContent.nextRollover(after: now)
            let entries = [now, rollover].map { TodoEntry(date: $0, lists: store.display(at: $0), available: true) }
            completion(Timeline(entries: entries, policy: .after(min(rollover, now.addingTimeInterval(30 * 60)))))
        }
    }
}

// MARK: - Taps

struct ToggleHabitIntent: AppIntent {
    static let title: LocalizedStringResource = "Tick Habit"
    static let isDiscoverable = false

    @Parameter(title: "Habit") var id: String

    init() {}
    init(id: String) { self.id = id }

    func perform() async throws -> some IntentResult {
        guard let store = WidgetFiles.store() else { return .result() }
        let now = Date()
        if let change = TodoWidgetContent.toggle(habit: id, in: store.display(at: now), at: now) {
            try store.queue(change, now: now)
            await WidgetFiles.syncBriefly(store)
        }
        return .result()
    }
}

struct CompleteTodoIntent: AppIntent {
    static let title: LocalizedStringResource = "Complete To-Do"
    static let isDiscoverable = false

    @Parameter(title: "To-do") var id: String

    init() {}
    init(id: String) { self.id = id }

    func perform() async throws -> some IntentResult {
        guard let store = WidgetFiles.store() else { return .result() }
        let now = Date()
        if let change = TodoWidgetContent.complete(todo: id, in: store.display(at: now)) {
            try store.queue(change, now: now)
            await WidgetFiles.syncBriefly(store)
        }
        return .result()
    }
}

// MARK: - Habits

struct HabitsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: TodoWidgetFiles.habitsKind, provider: TodoProvider()) { entry in
            HabitsWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
                .widgetURL(TodoWidgetFiles.openURL)
        }
        .configurationDisplayName("Habits")
        .description("Tick off today's habits and keep your streaks going.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct HabitsWidgetView: View {
    let entry: TodoEntry
    @Environment(\.widgetFamily) private var family

    private var rows: [HabitWidgetRow] { TodoWidgetContent.habits(entry.lists, at: entry.date) }

    private var capacity: Int {
        switch family {
        case .systemSmall: return 3
        case .systemMedium: return 4
        default: return DailyTask.limit
        }
    }

    var body: some View {
        if !entry.available {
            OpenAppMessage()
        } else if rows.isEmpty {
            EmptyMessage(symbol: "flame", text: "Add habits in the Todo tab.")
        } else {
            VStack(alignment: .leading, spacing: family == .systemSmall ? 6 : 8) {
                header
                ForEach(rows.prefix(capacity)) { row in HabitRowView(row: row, showsWeek: family != .systemSmall) }
                if rows.count > capacity {
                    Text("+\(rows.count - capacity) more").font(.caption2).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var header: some View {
        let done = rows.filter(\.done).count
        return HStack {
            Text("Habits").font(.headline)
            Spacer()
            Text("\(done)/\(rows.count)")
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(done == rows.count ? Color.green : .secondary)
        }
    }
}

struct HabitRowView: View {
    let row: HabitWidgetRow
    let showsWeek: Bool

    var body: some View {
        HStack(spacing: 8) {
            Button(intent: ToggleHabitIntent(id: row.id)) {
                Image(systemName: row.done ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(row.done ? Color.accentColor : .secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(row.done ? "Done, \(row.title)" : "Not done, \(row.title)")
            Text(row.title)
                .font(.subheadline)
                .lineLimit(1)
                .strikethrough(row.done)
                .foregroundStyle(row.done ? .secondary : .primary)
            Spacer(minLength: 4)
            if showsWeek {
                HStack(spacing: 2) {
                    // Today is the last dot, and the check button already says it.
                    ForEach(Array(row.week.dropLast().enumerated()), id: \.offset) { index, done in
                        // An excused day is a ring: it neither counted nor broke the streak.
                        if !done, row.excused.indices.contains(index), row.excused[index] {
                            Circle().strokeBorder(Color.secondary.opacity(0.6), lineWidth: 1).frame(width: 5, height: 5)
                        } else {
                            Circle()
                                .fill(done ? Color.accentColor : Color.secondary.opacity(0.25))
                                .frame(width: 5, height: 5)
                        }
                    }
                }
                .accessibilityHidden(true)
            }
            if row.streak > 0 {
                Label("\(row.streak)", systemImage: row.atRisk ? "flame" : "flame.fill")
                    .labelStyle(.titleAndIcon)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(row.atRisk ? .red : .orange)
                    .accessibilityLabel("\(row.streak) day streak")
            }
        }
    }
}

// MARK: - To-Do

struct TodosWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: TodoWidgetFiles.todosKind, provider: TodoProvider()) { entry in
            TodosWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
                .widgetURL(TodoWidgetFiles.openURL)
        }
        .configurationDisplayName("To-Do")
        .description("Your open to-dos, with what's due first.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct TodosWidgetView: View {
    let entry: TodoEntry
    @Environment(\.widgetFamily) private var family

    private var capacity: Int {
        switch family {
        case .systemSmall: return 3
        case .systemMedium: return 4
        default: return 9
        }
    }

    var body: some View {
        let content = TodoWidgetContent.todos(entry.lists, at: entry.date)
        if !entry.available {
            OpenAppMessage()
        } else {
            VStack(alignment: .leading, spacing: family == .systemSmall ? 6 : 8) {
                HStack {
                    Text("To-Do").font(.headline)
                    Spacer()
                    if content.due > 0 {
                        Text("\(content.due) due")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(.red)
                    }
                }
                if content.items.isEmpty {
                    Spacer(minLength: 0)
                    EmptyMessage(symbol: "checkmark.circle", text: "Nothing on the list.")
                } else {
                    ForEach(content.items.prefix(capacity)) { item in
                        TodoRowView(todo: item, now: entry.date, compact: family == .systemSmall)
                    }
                    if content.items.count > capacity {
                        Text("+\(content.items.count - capacity) more").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct TodoRowView: View {
    let todo: TodoItem
    let now: Date
    let compact: Bool

    var body: some View {
        let due = TodoRules.dueLabel(todo, now: now)
        HStack(spacing: 8) {
            Button(intent: CompleteTodoIntent(id: todo.id)) {
                Image(systemName: "circle").font(.title3).foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Complete \(todo.title)")
            Text(todo.title).font(.subheadline).lineLimit(1)
            Spacer(minLength: 4)
            if let due, !compact {
                Text(due.label)
                    .font(.caption2)
                    .foregroundStyle(due.overdue ? .red : .secondary)
            }
        }
    }
}

// MARK: - Messages

struct OpenAppMessage: View {
    var body: some View {
        EmptyMessage(symbol: "iphone", text: "Open Lunaschal and sign in to see your lists here.")
    }
}

struct EmptyMessage: View {
    let symbol: String
    let text: String

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: symbol).font(.title2).foregroundStyle(.secondary)
            Text(text).font(.caption).multilineTextAlignment(.center).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

extension TodoLists {
    /// The widget gallery's preview.
    static var sample: TodoLists {
        let today = DayKey.of(Date())
        let days = HabitStreaks.days(endingAt: today, count: 7)
        return TodoLists(
            tasks: [
                DailyTask(id: "1", title: "Read 20 minutes", position: 1, done: true, history: days),
                DailyTask(id: "2", title: "Stretch", position: 2, done: false, history: Array(days.dropLast().suffix(3))),
                DailyTask(id: "3", title: "Drink water", position: 3, done: true, history: [days[1], days[4], today]),
            ],
            todos: [
                TodoItem(id: "a", title: "Renew passport", due: TodoRules.iso(Date())),
                TodoItem(id: "b", title: "Book flight"),
                TodoItem(id: "c", title: "Call the dentist"),
            ])
    }
}
