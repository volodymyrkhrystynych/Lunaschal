import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Daily tasks and to-dos (the desktop's /api/tasks routes)

extension JournalAPI: TodoTransport {
    public func send(_ change: TodoChange) async throws {
        switch change {
        case let .addTask(id, title): try await addDailyTask(id: id, title)
        case let .renameTask(id, title): try await renameDailyTask(id, to: title)
        case let .reorderTasks(order): try await reorderDailyTasks(order)
        case let .deleteTask(id): try await deleteDailyTask(id)
        case let .tickTask(id, day, done): try await setDailyTask(id, day: day, done: done)
        case let .createTodo(draft): try await createTodo(draft)
        case let .editTodo(id, draft): try await editTodo(id, draft)
        case let .setTodo(id, done): try await setTodo(id, done: done)
        case let .moveTodo(id, list): try await moveTodo(id, to: list)
        case let .deleteTodo(id): try await deleteTodo(id)
        }
    }

    public func dailyTasks() async throws -> [DailyTask] {
        try JSONDecoder().decode([DailyTask].self, from: await get("api/tasks", [:]))
    }

    /// The id makes a repeated create a no-op on the server.
    public func addDailyTask(id: String, _ title: String) async throws {
        try await todoCall("api/tasks", method: "POST", ["id": try Self.pathID(id), "title": title])
    }

    public func renameDailyTask(_ id: String, to title: String) async throws {
        try await todoCall("api/tasks/\(try Self.pathID(id))", method: "PATCH", ["title": title])
    }

    public func reorderDailyTasks(_ order: [String]) async throws {
        try await todoCall("api/tasks/reorder", method: "POST", ["order": order])
    }

    public func deleteDailyTask(_ id: String) async throws {
        try await todoCall("api/tasks/\(try Self.pathID(id))", method: "DELETE")
    }

    /// The completion for `day`, the 4am day it was ticked on.
    public func setDailyTask(_ id: String, day: String, done: Bool) async throws {
        try await todoCall("api/tasks/\(try Self.pathID(id))/complete", method: done ? "POST" : "DELETE",
                           query: ["date": day])
    }

    public func todos() async throws -> [TodoItem] {
        try JSONDecoder().decode([TodoItem].self, from: await get("api/tasks/todos", [:]))
    }

    /// The draft's `id` makes a repeated create a no-op on the server.
    public func createTodo(_ draft: TodoDraft) async throws {
        try await todoCall("api/tasks/todos", method: "POST", draft)
    }

    /// Replaces every field the form sets; `id` is ignored.
    public func editTodo(_ id: String, _ draft: TodoDraft) async throws {
        var fields = draft
        fields.id = nil
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", fields)
    }

    /// Completing a repeating to-do moves it to its next due date instead.
    public func setTodo(_ id: String, done: Bool) async throws {
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", ["done": done])
    }

    public func moveTodo(_ id: String, to list: String) async throws {
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", ["list": list])
    }

    public func deleteTodo(_ id: String) async throws {
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "DELETE")
    }

    private func todoCall(_ path: String, method: String, query: [String: String] = [:]) async throws {
        try await todoCall(path, method: method, query: query, String?.none)
    }

    /// A 4xx other than sign-in or rate limiting is the server's final word
    /// and comes back as `TodoRefusal`; the rest are worth trying again.
    private func todoCall<Body: Encodable>(_ path: String, method: String, query: [String: String] = [:],
                                           _ body: Body?) async throws {
        var req = request(path, method: method)
        if !query.isEmpty {
            var parts = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)!
            parts.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
            req.url = parts.url
        }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let failure = HTTPFailure(status: http.statusCode)
            if (400..<500).contains(http.statusCode), ![401, 403].contains(http.statusCode), !failure.retryAutomatically {
                throw TodoRefusal(status: http.statusCode, message: Self.errorMessage(data) ?? "HTTP \(http.statusCode)")
            }
            throw failure
        }
    }
}
