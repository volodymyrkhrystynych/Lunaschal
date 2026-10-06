import Foundation

// Changes made in the Todo tab, kept on the phone until the server has them.
// The lists on screen are the server's last copy with these laid over it, so
// a tick shows at once whether or not the server can be reached.

/// One change to the daily tasks or to-dos, as the server will be told it.
public enum TodoChange: Codable, Equatable {
    case addTask(id: String, title: String)
    case renameTask(id: String, title: String)
    case reorderTasks([String])
    case deleteTask(id: String)
    /// `day` is the 4am day it was ticked on, so a tick sent after the
    /// rollover still counts for that day.
    case tickTask(id: String, day: String, done: Bool)
    case createTodo(TodoDraft)
    case editTodo(id: String, TodoDraft)
    case setTodo(id: String, done: Bool)
    case moveTodo(id: String, list: String)
    case deleteTodo(id: String)

    /// What a refusal says it was about.
    var subject: String {
        switch self {
        case let .addTask(_, title), let .renameTask(_, title): return "“\(title)”"
        case let .createTodo(draft), let .editTodo(_, draft): return "“\(draft.title)”"
        case .reorderTasks: return "the new order"
        default: return "a change"
        }
    }

    var isDelete: Bool {
        if case .deleteTask = self { return true }
        if case .deleteTodo = self { return true }
        return false
    }
}

public struct TodoOp: Codable, Equatable, Identifiable {
    public let id: String
    public let change: TodoChange
    public let createdAt: Date

    public init(id: String = ULID.make(), change: TodoChange, createdAt: Date = Date()) {
        self.id = id; self.change = change; self.createdAt = createdAt
    }
}

/// The daily tasks and to-dos as the Todo tab shows them.
public struct TodoLists: Codable, Equatable {
    public var tasks: [DailyTask]
    public var todos: [TodoItem]

    public init(tasks: [DailyTask] = [], todos: [TodoItem] = []) {
        self.tasks = tasks.sorted { $0.position < $1.position }; self.todos = todos
    }

    /// These lists with changes not yet on the server laid over them, the
    /// way the server will apply them. `today` is the current 4am day: a
    /// tick for another day doesn't show as today's.
    public func applying(_ ops: [TodoOp], today: String, now: Date = Date()) -> TodoLists {
        var lists = self
        for op in ops { lists.apply(op.change, today: today, now: now) }
        return lists
    }

    mutating func apply(_ change: TodoChange, today: String, now: Date) {
        switch change {
        case let .addTask(id, title):
            guard !tasks.contains(where: { $0.id == id }) else { return }
            tasks.append(DailyTask(id: id, title: title, position: tasks.count + 1, done: false))
        case let .renameTask(id, title):
            updateTask(id) { $0.title = title }
        case let .reorderTasks(order):
            let rank = Dictionary(order.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
            tasks = tasks.enumerated().sorted {
                (rank[$0.element.id] ?? order.count + $0.offset) < (rank[$1.element.id] ?? order.count + $1.offset)
            }.map(\.element)
            renumber()
        case let .deleteTask(id):
            tasks.removeAll { $0.id == id }
            renumber()
        case let .tickTask(id, day, done):
            if day == today { updateTask(id) { $0.done = done } }
        case let .createTodo(draft):
            guard let id = draft.id, !todos.contains(where: { $0.id == id }) else { return }
            var item = TodoItem(id: id, title: draft.trimmedTitle, createdAt: TodoRules.iso(now))
            item.fill(from: draft)
            todos.append(item)
        case let .editTodo(id, draft):
            updateTodo(id) { $0.fill(from: draft) }
        case let .setTodo(id, done):
            updateTodo(id) { todo in
                if done, let interval = todo.repeatInterval, let unit = todo.repeatUnit {
                    // A repeating one moves to its next occurrence instead.
                    let next = TodoRules.nextDue(todo.dueDate, interval: interval, unit: unit, now: now)
                    todo.due = TodoRules.iso(next)
                    todo.done = false
                    todo.completedAt = nil
                } else {
                    todo.done = done
                    todo.completedAt = done ? (todo.completedAt ?? TodoRules.iso(now)) : nil
                }
            }
        case let .moveTodo(id, list):
            updateTodo(id) { $0.list = list }
        case let .deleteTodo(id):
            todos.removeAll { $0.id == id }
        }
    }

    private mutating func updateTask(_ id: String, _ change: (inout DailyTask) -> Void) {
        guard let index = tasks.firstIndex(where: { $0.id == id }) else { return }
        change(&tasks[index])
    }

    private mutating func updateTodo(_ id: String, _ change: (inout TodoItem) -> Void) {
        guard let index = todos.firstIndex(where: { $0.id == id }) else { return }
        change(&todos[index])
    }

    private mutating func renumber() {
        for index in tasks.indices { tasks[index].position = index + 1 }
    }
}

extension TodoItem {
    mutating func fill(from draft: TodoDraft) {
        title = draft.trimmedTitle
        let trimmedNotes = draft.notes.trimmingCharacters(in: .whitespacesAndNewlines)
        notes = trimmedNotes.isEmpty ? nil : trimmedNotes
        due = draft.due.map { TodoRules.iso(Date(timeIntervalSince1970: TimeInterval(TodoPromotion.dueSeconds($0)))) }
        if let interval = draft.repeatInterval, interval >= 1 {
            repeatInterval = interval; repeatUnit = draft.repeatUnit
        } else {
            repeatInterval = nil; repeatUnit = nil
        }
        priority = draft.priority
        list = draft.list
    }
}

/// The queue on disk, oldest first.
public final class TodoOutbox {
    public let root: URL
    private var file: URL { root.appendingPathComponent("outbox.json") }

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [TodoOp] {
        guard FileManager.default.fileExists(atPath: file.path) else { return [] }
        return try JSONDecoder().decode([TodoOp].self, from: Data(contentsOf: file))
    }

    @discardableResult
    public func append(_ change: TodoChange, now: Date = Date()) throws -> TodoOp {
        let op = TodoOp(change: change, createdAt: now)
        try write(try list() + [op])
        return op
    }

    public func remove(_ op: TodoOp) throws {
        try write(try list().filter { $0.id != op.id })
    }

    private func write(_ ops: [TodoOp]) throws {
        try JSONEncoder().encode(ops).write(to: file, options: .atomic)
    }
}

public protocol TodoTransport {
    /// `capturedAt` is when the change was made on the phone, which the
    /// server stamps it with rather than the moment it synced.
    func send(_ change: TodoChange, capturedAt: Date) async throws
}

/// The server turned a change down for good: a full list, a bad field, a
/// row someone already deleted. Sending it again would get the same answer.
public struct TodoRefusal: LocalizedError, Equatable {
    public let status: Int
    public let message: String
    public init(status: Int, message: String) { self.status = status; self.message = message }
    public var errorDescription: String? { message }
}

/// Sends the queue in order. A refused change is dropped and reported, and
/// the next one goes; anything else (offline, signed out, a server error)
/// stops the pass with the rest still queued.
public final class TodoSync {
    private let outbox: TodoOutbox

    public init(outbox: TodoOutbox) { self.outbox = outbox }

    /// What was refused, worded for the Todo tab.
    public func run(using transport: TodoTransport) async throws -> [String] {
        var refused: [String] = []
        for op in try outbox.list() {
            try Task.checkCancellation()
            do {
                try await transport.send(op.change, capturedAt: op.createdAt)
            } catch let refusal as TodoRefusal {
                // Deleting something already gone is what was wanted.
                if !(refusal.status == 404 && op.change.isDelete) {
                    refused.append("The server didn't take \(op.change.subject): \(refusal.message)")
                }
            }
            try outbox.remove(op)
        }
        return refused
    }
}
