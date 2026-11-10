import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Daily tasks and to-dos (the desktop's /api/tasks routes)

extension JournalAPI: TodoTransport {
    /// Ticks and removals are logged on the server, and that log is read as
    /// the user being awake, so those carry when they were made.
    public func send(_ change: TodoChange, capturedAt: Date) async throws {
        switch change {
        case let .addTask(id, title): try await addDailyTask(id: id, title)
        case let .renameTask(id, title): try await renameDailyTask(id, to: title)
        case let .setTaskCue(id, cue): try await setDailyTaskCue(id, cue)
        case let .reorderTasks(order): try await reorderDailyTasks(order)
        case let .deleteTask(id): try await deleteDailyTask(id, at: capturedAt)
        case let .tickTask(id, day, done): try await setDailyTask(id, day: day, done: done, at: capturedAt)
        case let .createTodo(draft): try await createTodo(draft)
        case let .editTodo(id, draft): try await editTodo(id, draft)
        case let .setTodo(id, done): try await setTodo(id, done: done, at: capturedAt)
        case let .moveTodo(id, list): try await moveTodo(id, to: list)
        case let .deleteTodo(id): try await deleteTodo(id, at: capturedAt)
        case let .createGoal(id, horizon, title, details):
            if horizon == .overarching { try await setOverarchingGoal(id: id, details ?? title, at: capturedAt) }
            else { try await createGoal(id: id, horizon: horizon, title: title, details: details, at: capturedAt) }
        case let .editGoal(id, title, details): try await editGoal(id, title: title, details: details, at: capturedAt)
        case let .setOverarching(id, text): try await setOverarchingGoal(id: id, text, at: capturedAt)
        case let .setGoalStatus(id, action): try await setGoalStatus(id, action, at: capturedAt)
        }
    }

    /// `?capturedAt=` — a query parameter, since a delete has no body.
    static func capturedAtQuery(_ date: Date?) -> [String: String] {
        date.map { ["capturedAt": ISO8601DateFormatter().string(from: $0)] } ?? [:]
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

    /// Sent as null to clear it.
    public func setDailyTaskCue(_ id: String, _ cue: String?) async throws {
        try await todoCall("api/tasks/\(try Self.pathID(id))", method: "PATCH", CueBody(cue: cue))
    }

    struct CueBody: Encodable {
        let cue: String?
        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(cue, forKey: .cue)
        }
        enum CodingKeys: String, CodingKey { case cue }
    }

    public func reorderDailyTasks(_ order: [String]) async throws {
        try await todoCall("api/tasks/reorder", method: "POST", ["order": order])
    }

    public func deleteDailyTask(_ id: String, at: Date? = nil) async throws {
        try await todoCall("api/tasks/\(try Self.pathID(id))", method: "DELETE", query: Self.capturedAtQuery(at))
    }

    /// The completion for `day`, the 4am day it was ticked on.
    public func setDailyTask(_ id: String, day: String, done: Bool, at: Date? = nil) async throws {
        try await todoCall("api/tasks/\(try Self.pathID(id))/complete", method: done ? "POST" : "DELETE",
                           query: ["date": day].merging(Self.capturedAtQuery(at)) { first, _ in first })
    }

    public func todos() async throws -> [TodoItem] {
        try JSONDecoder().decode([TodoItem].self, from: await get("api/tasks/todos", [:]))
    }

    /// The draft's `id` makes a repeated create a no-op on the server.
    public func createTodo(_ draft: TodoDraft) async throws {
        try await todoCall("api/tasks/todos", method: "POST", draft)
    }

    /// A spoken sentence made into one to-do by the server's model, with no
    /// confirm step. `id` is minted by the phone: a replay returns the row
    /// already made, and `TodoDictation.fallbackDraft` reuses it, so a reply
    /// lost on the way back can never leave two to-dos.
    public func createTodo(fromSpeech words: String, id: String) async throws {
        try await todoCall("api/tasks/todos/transcribe", method: "POST",
                           TodoDictation.Body(text: words, id: try Self.pathID(id)))
    }

    /// Replaces every field the form sets; `id` is ignored.
    public func editTodo(_ id: String, _ draft: TodoDraft) async throws {
        var fields = draft
        fields.id = nil
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", fields)
    }

    /// Completing a repeating to-do moves it to its next due date instead.
    public func setTodo(_ id: String, done: Bool, at: Date? = nil) async throws {
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", query: Self.capturedAtQuery(at),
                           ["done": done])
    }

    public func moveTodo(_ id: String, to list: String) async throws {
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "PATCH", ["list": list])
    }

    public func deleteTodo(_ id: String, at: Date? = nil) async throws {
        try await todoCall("api/tasks/todos/\(try Self.pathID(id))", method: "DELETE", query: Self.capturedAtQuery(at))
    }

    // MARK: Day reviews (/api/coach)

    public func coachReviews(days: Int = 7) async throws -> CoachReviews {
        try JSONDecoder().decode(CoachReviews.self, from: await get("api/coach/reviews", ["days": String(days)]))
    }

    // MARK: Goals (/api/goals)
    //
    // Each of these leaves a journal entry on the server, dated `capturedAt`:
    // a goal set on the train belongs to the train ride, not to the sync.

    public func currentGoals() async throws -> CurrentGoals {
        try JSONDecoder().decode(CurrentGoals.self, from: await get("api/goals/current", [:]))
    }

    public func createGoal(id: String, horizon: GoalHorizon, title: String, details: String?,
                           at: Date? = nil) async throws {
        try await todoCall("api/goals", method: "POST", query: Self.capturedAtQuery(at),
                           GoalBody(id: try Self.pathID(id), horizon: horizon.rawValue, title: title, details: details))
    }

    public func editGoal(_ id: String, title: String, details: String?, at: Date? = nil) async throws {
        try await todoCall("api/goals/\(try Self.pathID(id))", method: "PATCH", query: Self.capturedAtQuery(at),
                           GoalBody(title: title, details: details))
    }

    public func setOverarchingGoal(id: String, _ text: String, at: Date? = nil) async throws {
        try await todoCall("api/goals/overarching", method: "PUT", query: Self.capturedAtQuery(at),
                           ["id": try Self.pathID(id), "text": text])
    }

    public func setGoalStatus(_ id: String, _ action: String, at: Date? = nil) async throws {
        guard ["end", "cancel", "redeem"].contains(action) else { throw CaptureError.invalidResponse }
        try await todoCall("api/goals/\(try Self.pathID(id))/\(action)", method: "POST", query: Self.capturedAtQuery(at))
    }

    /// `details` is sent as null, not left out, so clearing it clears it.
    struct GoalBody: Encodable {
        var id: String?
        var horizon: String?
        var title: String
        var details: String?

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encodeIfPresent(id, forKey: .id)
            try c.encodeIfPresent(horizon, forKey: .horizon)
            try c.encode(title, forKey: .title)
            try c.encode(details, forKey: .details)
        }

        enum CodingKeys: String, CodingKey { case id, horizon, title, details }
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
