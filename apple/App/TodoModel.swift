import Foundation
import LunaschalCore
import SwiftUI

/// The Todo tab: the desktop Lifestyle tab's daily tasks and to-do lists.
/// Changes need the server, since a daily task is ticked off for the
/// server's day and replaying that later could land it on the wrong one. The
/// last lists seen are kept on disk, so the tab and its badge still show
/// what's due while offline.
@MainActor
final class TodoModel: ObservableObject {
    @Published private(set) var tasks: [DailyTask] = []
    @Published private(set) var todos: [TodoItem] = []
    /// Why the lists couldn't be fetched, while offline or signed out.
    @Published private(set) var loadProblem: String?
    /// A refused change, said under the lists.
    @Published var notice: String?

    let capture: CaptureModel
    private let cacheURL: URL

    private struct Cache: Codable { var tasks: [DailyTask]; var todos: [TodoItem] }

    init(capture: CaptureModel) {
        self.capture = capture
        cacheURL = capture.store.root.appendingPathComponent("todos.cache")
        if let cache = try? JSONDecoder().decode(Cache.self, from: Data(contentsOf: cacheURL)) {
            tasks = cache.tasks; todos = cache.todos
        }
    }

    /// The tab's badge: to-dos due today or overdue.
    var dueCount: Int { TodoRules.dueCount(todos) }

    func active(on list: String) -> [TodoItem] { TodoRules.active(TodoRules.on(list, todos)) }

    func refresh() async {
        guard let api = capture.chatAPI() else {
            loadProblem = capture.server == nil ? "Connect to your server in Settings to see your to-dos."
                                                : "Sign in again in Settings to see your to-dos."
            return
        }
        do {
            async let fetchedTasks = api.dailyTasks()
            async let fetchedTodos = api.todos()
            let (newTasks, newTodos) = try await (fetchedTasks, fetchedTodos)
            tasks = newTasks.sorted { $0.position < $1.position }
            todos = newTodos
            loadProblem = nil
            if let data = try? JSONEncoder().encode(Cache(tasks: tasks, todos: todos)) {
                try? data.write(to: cacheURL, options: .atomic)
            }
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
            loadProblem = "Offline — showing your lists as last seen."
        }
    }

    // MARK: Daily tasks

    func toggle(_ task: DailyTask) async {
        await call({ self.update(task.id) { $0.done.toggle() } }) { try await $0.setDailyTask(task.id, done: !task.done) }
    }

    func addTask(_ title: String) async -> Bool {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        return await call { try await $0.addDailyTask(trimmed) }
    }

    func rename(_ task: DailyTask, to title: String) async {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != task.title else { return }
        await call({ self.update(task.id) { $0.title = trimmed } }) { try await $0.renameDailyTask(task.id, to: trimmed) }
    }

    func moveTasks(from source: IndexSet, to destination: Int) async {
        var order = tasks
        order.move(fromOffsets: source, toOffset: destination)
        await call({ self.tasks = order.enumerated().map { var task = $0.element; task.position = $0.offset + 1; return task } }) {
            try await $0.reorderDailyTasks(order.map(\.id))
        }
    }

    func delete(_ task: DailyTask) async {
        await call({ self.tasks.removeAll { $0.id == task.id } }) { try await $0.deleteDailyTask(task.id) }
    }

    private func update(_ id: String, _ change: (inout DailyTask) -> Void) {
        guard let index = tasks.firstIndex(where: { $0.id == id }) else { return }
        change(&tasks[index])
    }

    // MARK: To-dos

    func toggle(_ todo: TodoItem) async {
        // A repeating one comes back with its next due date, so only a
        // one-off is ticked off here; the refetch shows the rest.
        await call({ if todo.repeatInterval == nil { self.updateTodo(todo.id) { $0.done.toggle() } } }) {
            try await $0.setTodo(todo.id, done: !todo.done)
        }
    }

    func create(_ draft: TodoDraft) async -> Bool {
        var draft = draft
        draft.id = ULID.make()
        return await call { try await $0.createTodo(draft) }
    }

    func edit(_ todo: TodoItem, _ draft: TodoDraft) async -> Bool {
        await call { try await $0.editTodo(todo.id, draft) }
    }

    func move(_ todo: TodoItem) async {
        let list = todo.isArchived ? "todo" : "archive"
        await call({ self.updateTodo(todo.id) { $0.list = list } }) { try await $0.moveTodo(todo.id, to: list) }
    }

    func delete(_ todo: TodoItem) async {
        await call({ self.todos.removeAll { $0.id == todo.id } }) { try await $0.deleteTodo(todo.id) }
    }

    private func updateTodo(_ id: String, _ change: (inout TodoItem) -> Void) {
        guard let index = todos.firstIndex(where: { $0.id == id }) else { return }
        change(&todos[index])
    }

    /// Shows `local` at once, makes the change, then shows the server's
    /// lists. When the server can't be reached, the lists go back to how
    /// they were, since nothing was changed.
    @discardableResult
    private func call(_ local: () -> Void = {}, _ change: (JournalAPI) async throws -> Void) async -> Bool {
        guard let api = capture.chatAPI() else {
            notice = "Connect to your server to change your to-dos."
            return false
        }
        let before = (tasks, todos)
        local()
        var ok = true
        do { try await change(api); notice = nil } catch { notice = error.localizedDescription; ok = false }
        await refresh()
        if !ok, loadProblem != nil { (tasks, todos) = before }
        return ok
    }
}
