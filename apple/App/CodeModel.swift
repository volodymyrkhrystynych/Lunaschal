import Foundation
import LunaschalCore
import SwiftUI
import UserNotifications

/// Where a tapped Code-tab notification should go. The app delegate writes it;
/// CaptureRoot and ChatView read it and navigate.
@MainActor
final class PushRouter: ObservableObject {
    static let shared = PushRouter()
    /// The task a notification was tapped for, until something opens it.
    @Published var openTaskID: String?
    /// Hex APNs token, once iOS has handed one over.
    @Published var deviceToken: String?
}

/// The Code page in Chat: coding tasks run by the server's local agent.
/// Every task lives on the server, so this model is a view over its rows,
/// polled quickly while the agent works and slowly otherwise.
@MainActor
final class CodeModel: ObservableObject {
    @Published private(set) var tasks: [CodeTask] = []
    @Published private(set) var waiting = 0
    @Published private(set) var repos: [CodeRepo] = []
    @Published private(set) var details: [String: CodeTaskDetail] = [:]
    @Published private(set) var loadProblem: String?
    /// The last refused action, said under the button that caused it.
    @Published var notice: String?
    @Published private(set) var busy = false
    /// A dictated clip is with the server, being turned into words.
    @Published private(set) var transcribing = false

    let capture: CaptureModel
    private var fixture: CodeFixture?
    private var registeredToken: String?

    init(capture: CaptureModel) {
        self.capture = capture
        fixture = CodeFixture.ifAsked()
        if let fixture {
            tasks = fixture.tasks
            repos = fixture.repos
            waiting = fixture.tasks.filter { $0.pending != nil }.count
        }
    }

    var needsYou: [CodeTask] {
        tasks.filter { CodeTaskStatus.needsYou.contains($0.status) }
    }
    var working: [CodeTask] { tasks.filter(\.isActive) }
    var finished: [CodeTask] { tasks.filter(\.isClosed) }

    func task(_ id: String) -> CodeTask? { details[id]?.task ?? tasks.first { $0.id == id } }

    // MARK: Loading

    func refresh() async {
        if fixture != nil { return }
        guard let api = capture.chatAPI() else {
            loadProblem = capture.server == nil ? "Connect to your server in Settings to run coding tasks."
                                                : "Sign in again in Settings to run coding tasks."
            return
        }
        do {
            let list = try await api.codeTasks()
            tasks = list.tasks
            waiting = list.waiting
            if repos.isEmpty { repos = (try? await api.codeRepos()) ?? [] }
            loadProblem = nil
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
            loadProblem = "Can't reach your server."
        }
    }

    func poll() async {
        while !Task.isCancelled {
            await refresh()
            do { try await Task.sleep(for: .seconds(CodeTaskStatus.pollInterval(tasks))) } catch { return }
        }
    }

    /// Fetches the task and only the events this phone has not seen yet.
    func loadDetail(_ id: String) async {
        if let fixture {
            if let task = fixture.tasks.first(where: { $0.id == id }) {
                details[id] = CodeTaskDetail(task: task, events: fixture.events(for: id))
            }
            return
        }
        guard let api = capture.chatAPI() else { return }
        let seen = details[id]?.events.last?.seq ?? 0
        do {
            let fresh = try await api.codeTask(id, after: seen)
            details[id] = CodeTaskDetail(task: fresh.task, events: (details[id]?.events ?? []) + fresh.events)
            replace(fresh.task)
        } catch {
            if Task.isCancelled { return }
        }
    }

    func pollDetail(_ id: String) async {
        while !Task.isCancelled {
            await loadDetail(id)
            let active = task(id)?.isActive ?? false
            do { try await Task.sleep(for: .seconds(active ? 3 : 20)) } catch { return }
        }
    }

    func diff(_ id: String) async throws -> CodeDiff {
        if let fixture {
            return CodeDiff(files: task(id)?.diffStat ?? [], commits: ["5555555 Fix the streak"],
                            patch: fixture.patch, truncated: false)
        }
        guard let api = capture.chatAPI() else { throw CaptureError.invalidResponse }
        return try await api.codeDiff(id)
    }

    // MARK: Actions

    func create(prompt: String, repoID: String?, mode: CodeTaskMode = .plan) async -> Bool {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return false }
        if var fixture {
            _ = fixture.add(prompt: text, mode: mode)
            self.fixture = fixture
            tasks = fixture.tasks
            return true
        }
        return await act { api in try await api.createCodeTask(repoID: repoID, prompt: text, mode: mode) }
    }

    /// An answered question becomes a change; `text` says which, if anything was typed.
    func planFromAnswer(_ id: String, text: String) async {
        await act(id, "make-plan") { try await $0.planFromCodeAnswer(id, text: text) }
    }

    func approve(_ id: String) async { await act(id, "approve") { try await $0.approveCodePlan(id) } }
    func revise(_ id: String, text: String) async {
        await act(id, "revise") { try await $0.reviseCodePlan(id, text: text) }
    }
    func message(_ id: String, text: String) async {
        await act(id, "message") { try await $0.sendCodeMessage(id, text: text) }
    }
    func resume(_ id: String) async { await act(id, "continue") { try await $0.continueCodeTask(id) } }
    func cancel(_ id: String) async { await act(id, "cancel") { try await $0.cancelCodeTask(id) } }
    func discard(_ id: String) async { await act(id, "discard") { try await $0.discardCodeTask(id) } }
    func push(_ id: String) async { await act(id, "push") { try await $0.pushCodeTask(id) } }

    @discardableResult
    private func act(_ id: String? = nil, _ action: String = "",
                     _ call: (JournalAPI) async throws -> CodeTask) async -> Bool {
        notice = nil
        if var fixture, let id {
            if let updated = fixture.apply(action, to: id) {
                self.fixture = fixture
                replace(updated)
                tasks = fixture.tasks
            }
            return true
        }
        guard let api = capture.chatAPI() else { notice = "Connect to your server first."; return false }
        busy = true
        defer { busy = false }
        do {
            let task = try await call(api)
            replace(task)
            if !tasks.contains(where: { $0.id == task.id }) { tasks.insert(task, at: 0) }
            if let id { await loadDetail(id) }
            return true
        } catch {
            notice = error.localizedDescription
            return false
        }
    }

    private func replace(_ task: CodeTask) {
        if let i = tasks.firstIndex(where: { $0.id == task.id }) { tasks[i] = task }
        if let detail = details[task.id] { details[task.id] = CodeTaskDetail(task: task, events: detail.events) }
    }

    // MARK: Dictation

    /// The words in a dictated clip, for a text field. Deletes the clip either way.
    func transcribe(_ clip: URL) async -> String? {
        defer { try? FileManager.default.removeItem(at: clip) }
        guard fixture == nil else { return nil }
        guard let api = capture.chatAPI() else { notice = "Connect to your server first."; return nil }
        notice = nil
        transcribing = true
        defer { transcribing = false }
        do {
            let words = try await api.transcribe(clip: clip)
            if words.isEmpty { notice = "No speech was heard in that recording." }
            return words.isEmpty ? nil : words
        } catch {
            notice = "Couldn't transcribe that: \(error.localizedDescription)"
            return nil
        }
    }

    // MARK: Push

    /// Asks once, the first time the Code page is opened: this is the screen
    /// where a notification earns its keep, and asking at launch would not say why.
    func askForNotifications() async {
        guard fixture == nil else { return }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        if settings.authorizationStatus == .notDetermined {
            guard (try? await center.requestAuthorization(options: [.alert, .sound, .badge])) == true else { return }
        } else if settings.authorizationStatus != .authorized && settings.authorizationStatus != .provisional {
            return
        }
        UIApplication.shared.registerForRemoteNotifications()
    }

    /// Hands the APNs token to the server — once per token per launch.
    func upload(token: String) async {
        guard token != registeredToken, let api = capture.chatAPI() else { return }
        #if DEBUG
        let sandbox = true
        #else
        let sandbox = false
        #endif
        if (try? await api.registerPushDevice(token: token, sandbox: sandbox)) != nil { registeredToken = token }
    }
}
