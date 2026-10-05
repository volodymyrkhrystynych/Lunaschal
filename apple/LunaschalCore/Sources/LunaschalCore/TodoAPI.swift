import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Daily tasks and to-dos (the desktop's /api/tasks routes)

extension JournalAPI {
    public func dailyTasks() async throws -> [DailyTask] {
        try JSONDecoder().decode([DailyTask].self, from: await get("api/tasks", [:]))
    }

    public func addDailyTask(_ title: String) async throws {
        _ = try await chatJSON("api/tasks", ["title": title])
    }

    public func renameDailyTask(_ id: String, to title: String) async throws {
        try await sendJSON("api/tasks/\(try Self.pathID(id))", method: "PATCH", ["title": title])
    }

    public func reorderDailyTasks(_ order: [String]) async throws {
        _ = try await chatJSON("api/tasks/reorder", ["order": order])
    }

    public func deleteDailyTask(_ id: String) async throws {
        try await send("api/tasks/\(try Self.pathID(id))", method: "DELETE")
    }

    /// Today's completion, on the server's 4am day.
    public func setDailyTask(_ id: String, done: Bool) async throws {
        try await send("api/tasks/\(try Self.pathID(id))/complete", method: done ? "POST" : "DELETE")
    }

    public func todos() async throws -> [TodoItem] {
        try JSONDecoder().decode([TodoItem].self, from: await get("api/tasks/todos", [:]))
    }

    /// The draft's `id` makes a repeated create a no-op on the server.
    public func createTodo(_ draft: TodoDraft) async throws {
        _ = try await chatJSON("api/tasks/todos", draft)
    }

    /// Replaces every field the form sets; `id` is ignored.
    public func editTodo(_ id: String, _ draft: TodoDraft) async throws {
        var fields = draft
        fields.id = nil
        try await sendJSON("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", fields)
    }

    /// Completing a repeating to-do moves it to its next due date instead.
    public func setTodo(_ id: String, done: Bool) async throws {
        try await sendJSON("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", ["done": done])
    }

    public func moveTodo(_ id: String, to list: String) async throws {
        try await sendJSON("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", ["list": list])
    }

    public func deleteTodo(_ id: String) async throws {
        try await send("api/tasks/todos/\(try Self.pathID(id))", method: "DELETE")
    }

    private func sendJSON<Body: Encodable>(_ path: String, method: String, _ body: Body) async throws {
        var req = request(path, method: method)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        let (data, response) = try await session.data(for: req)
        try checkChat(data, response)
    }
}
