import Combine
import Foundation
import LunaschalCore
import SwiftUI

/// The Todo tab: the desktop Lifestyle tab's daily tasks and to-do lists.
/// Every change goes into the sync outbox and shows at once; the lists on
/// screen are the server's last copy with the queued changes laid over it,
/// so the tab and its badge work the same offline.
@MainActor
final class TodoModel: ObservableObject {
    /// The server's lists as last fetched, kept on disk.
    @Published private(set) var server = TodoLists()
    /// Why the lists couldn't be fetched, while offline or signed out.
    @Published private(set) var loadProblem: String?

    let capture: CaptureModel
    private let cacheURL: URL
    /// Changes the server has taken since the copy above was fetched,
    /// with when they left the queue: still laid over it until a fetch
    /// started after they were sent comes back.
    private var sent: [(op: TodoOp, at: Date)] = []
    private var lastQueue: [TodoOp] = []
    private var watching: AnyCancellable?
    private var refusalWatch: AnyCancellable?

    init(capture: CaptureModel) {
        self.capture = capture
        cacheURL = capture.store.root.appendingPathComponent("todos.cache")
        server = (try? JSONDecoder().decode(TodoLists.self, from: Data(contentsOf: cacheURL))) ?? TodoLists()
        lastQueue = capture.todoQueue
        watching = capture.$todoQueue.sink { [weak self] queue in self?.queueChanged(queue) }
        refusalWatch = capture.$todoRefusals.sink { [weak self] _ in self?.objectWillChange.send() }
    }

    /// What the tab shows.
    var lists: TodoLists { server.applying(sent.map(\.op) + capture.todoQueue, today: DayKey.of(Date())) }
    var tasks: [DailyTask] { lists.tasks }
    var todos: [TodoItem] { lists.todos }
    var waiting: Int { capture.todoQueue.count }
    var refusals: [String] { capture.todoRefusals }

    /// The tab's badge: to-dos due today or overdue.
    var dueCount: Int { TodoRules.dueCount(todos) }

    func active(on list: String) -> [TodoItem] { TodoRules.active(TodoRules.on(list, todos)) }

    func clearRefusals() { capture.todoRefusals = [] }

    private func queueChanged(_ queue: [TodoOp]) {
        let now = Date()
        let left = Set(queue.map(\.id))
        sent += lastQueue.filter { !left.contains($0.id) }.map { ($0, now) }
        lastQueue = queue
        objectWillChange.send()
    }

    func refresh() async {
        guard let api = capture.chatAPI() else {
            loadProblem = capture.server == nil
                ? "Not connected to a server. Changes stay on this phone until you connect in Settings."
                : "Signed out. Changes stay on this phone until you sign in again in Settings."
            return
        }
        let started = Date()
        do {
            async let fetchedTasks = api.dailyTasks()
            async let fetchedTodos = api.todos()
            let (tasks, todos) = try await (fetchedTasks, fetchedTodos)
            server = TodoLists(tasks: tasks, todos: todos)
            sent.removeAll { $0.at < started }
            loadProblem = nil
            if let data = try? JSONEncoder().encode(server) { try? data.write(to: cacheURL, options: .atomic) }
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
            loadProblem = "Offline. Changes are saved on this phone and sent when your server is back."
        }
    }

    private func queue(_ change: TodoChange) { capture.queueTodo(change) }

    // MARK: Daily tasks

    func toggle(_ task: DailyTask) {
        queue(.tickTask(id: task.id, day: DayKey.of(Date()), done: !task.done))
    }

    func addTask(_ title: String) -> Bool {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, tasks.count < DailyTask.limit else { return false }
        queue(.addTask(id: ULID.make(), title: trimmed))
        return true
    }

    func rename(_ task: DailyTask, to title: String) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != task.title else { return }
        queue(.renameTask(id: task.id, title: trimmed))
    }

    func moveTasks(from source: IndexSet, to destination: Int) {
        var order = tasks
        order.move(fromOffsets: source, toOffset: destination)
        queue(.reorderTasks(order.map(\.id)))
    }

    func delete(_ task: DailyTask) { queue(.deleteTask(id: task.id)) }

    // MARK: To-dos

    func toggle(_ todo: TodoItem) { queue(.setTodo(id: todo.id, done: !todo.done)) }

    func create(_ draft: TodoDraft) {
        var draft = draft
        draft.id = ULID.make()
        queue(.createTodo(draft))
    }

    func edit(_ todo: TodoItem, _ draft: TodoDraft) { queue(.editTodo(id: todo.id, draft)) }

    func move(_ todo: TodoItem) { queue(.moveTodo(id: todo.id, list: todo.isArchived ? "todo" : "archive")) }

    func delete(_ todo: TodoItem) { queue(.deleteTodo(id: todo.id)) }
}
