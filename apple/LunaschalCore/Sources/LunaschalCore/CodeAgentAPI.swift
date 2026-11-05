import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// MARK: Code tab (the server's /api/code and /api/push routes)

extension JournalAPI {
    public func codeTasks() async throws -> CodeTaskList {
        try JSONDecoder().decode(CodeTaskList.self, from: await get("api/code/tasks", [:]))
    }

    /// The task and the events after `after` — the poll's shape.
    public func codeTask(_ id: String, after: Int = 0) async throws -> CodeTaskDetail {
        try JSONDecoder().decode(CodeTaskDetail.self, from: await get(
            "api/code/tasks/\(try Self.pathID(id))", after > 0 ? ["after": String(after)] : [:]))
    }

    public func codeDiff(_ id: String) async throws -> CodeDiff {
        let (data, response) = try await session.data(for: request("api/code/tasks/\(try Self.pathID(id))/diff"))
        try checkChat(data, response)
        return try JSONDecoder().decode(CodeDiff.self, from: data)
    }

    public func codeRepos() async throws -> [CodeRepo] {
        try JSONDecoder().decode([CodeRepo].self, from: await get("api/repos", [:]))
    }

    /// A nil repo means the server's default repository.
    public func createCodeTask(repoID: String?, prompt: String, harness: String? = nil) async throws -> CodeTask {
        struct Body: Encodable { let repoId: String?; let prompt: String; let harness: String? }
        return try JSONDecoder().decode(CodeTask.self, from: await chatJSON(
            "api/code/tasks", Body(repoId: repoID, prompt: prompt, harness: harness)))
    }

    public func approveCodePlan(_ id: String) async throws -> CodeTask {
        try await codeAction(id, "plan", ["approve": JSONValue.bool(true)])
    }

    public func reviseCodePlan(_ id: String, text: String) async throws -> CodeTask {
        try await codeAction(id, "plan", ["text": JSONValue.string(text)])
    }

    /// An answer to the agent's question, or a follow-up once it has finished.
    public func sendCodeMessage(_ id: String, text: String) async throws -> CodeTask {
        try await codeAction(id, "messages", ["text": JSONValue.string(text)])
    }

    public func continueCodeTask(_ id: String) async throws -> CodeTask { try await codeAction(id, "continue") }
    public func cancelCodeTask(_ id: String) async throws -> CodeTask { try await codeAction(id, "cancel") }
    public func discardCodeTask(_ id: String) async throws -> CodeTask { try await codeAction(id, "discard") }
    /// Pushes `agent/<id>` as the machine account and opens the pull request.
    public func pushCodeTask(_ id: String) async throws -> CodeTask { try await codeAction(id, "push") }

    /// Registers this device for APNs. Returns whether the server can send.
    public func registerPushDevice(token: String, sandbox: Bool) async throws -> Bool {
        struct Body: Encodable { let token: String; let environment: String; let platform = "ios" }
        struct Reply: Decodable { let configured: Bool }
        return try JSONDecoder().decode(Reply.self, from: await chatJSON(
            "api/push/devices", Body(token: token, environment: sandbox ? "sandbox" : "production"))).configured
    }

    private func codeAction(_ id: String, _ action: String,
                            _ body: [String: JSONValue] = [:]) async throws -> CodeTask {
        try JSONDecoder().decode(CodeTask.self, from: await chatJSON(
            "api/code/tasks/\(try Self.pathID(id))/\(action)", body))
    }
}
