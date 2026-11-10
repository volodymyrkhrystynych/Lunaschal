import Combine
import Foundation
import LunaschalCore
import SwiftUI
import WidgetKit

/// The Todo tab: the desktop Lifestyle tab's daily tasks and to-do lists.
/// Every change goes into the sync outbox and shows at once; the lists on
/// screen are the server's last copy with the queued changes laid over it,
/// so the tab and its badge work the same offline.
@MainActor
final class TodoModel: ObservableObject {
    /// The server's lists as last fetched, kept on disk.
    @Published private(set) var server = TodoLists()
    /// Why the lists couldn't be fetched, while offline or signed out: a
    /// word or two for the title bar, and what it means.
    @Published private(set) var loadProblem: (short: String, detail: String)?
    /// Spoken to-dos the server has but hasn't put on the list yet: being
    /// transcribed, or failed. As of the last fetch.
    @Published private(set) var serverRecordings: [ServerTodoRecording] = []

    let capture: CaptureModel
    private let cacheURL: URL
    /// Changes the server has taken since the copy above was fetched,
    /// with when they left the queue: still laid over it until a fetch
    /// started after they were sent comes back.
    private var sent: [(op: TodoOp, at: Date)] = []
    private var lastQueue: [TodoOp] = []
    /// When `server` was fetched: the cache file's age until the first fetch.
    private var fetchedAt: Date
    private var watching: AnyCancellable?
    private var refusalWatch: AnyCancellable?
    private var recordingWatch: AnyCancellable?

    init(capture: CaptureModel) {
        self.capture = capture
        cacheURL = capture.store.root.appendingPathComponent("todos.cache")
        server = (try? JSONDecoder().decode(TodoLists.self, from: Data(contentsOf: cacheURL))) ?? TodoLists()
        fetchedAt = (try? cacheURL.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            ?? .distantPast
        lastQueue = capture.todoQueue
        watching = capture.$todoQueue.sink { [weak self] queue in self?.queueChanged(queue) }
        refusalWatch = capture.$todoRefusals.sink { [weak self] _ in self?.objectWillChange.send() }
        recordingWatch = capture.$todoRecordingQueue.sink { [weak self] _ in self?.objectWillChange.send() }
    }

    /// What the tab shows.
    var lists: TodoLists { server.applying(sent.map(\.op) + capture.todoQueue, today: DayKey.of(Date())) }
    var tasks: [DailyTask] { lists.tasks }
    var goals: CurrentGoals? { lists.goals }
    /// Yesterday's review, once it has been written.
    var yesterday: DayReview? {
        lists.review.flatMap { $0.day == HabitStreaks.adding(-1, to: DayKey.of(Date())) ? $0 : nil }
    }
    var todos: [TodoItem] { lists.todos }
    var waiting: Int { capture.todoQueue.count }
    /// Spoken to-dos still on this phone.
    var localRecordings: [TodoRecording] { capture.todoRecordingQueue }
    /// A spoken to-do is on its way: worth fetching again soon.
    var awaitingRecordings: Bool {
        capture.todoRecordingQueue.contains { $0.state == .pending } || serverRecordings.contains { !$0.failed }
    }
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
        // The publisher fires before `todoQueue` takes the new value.
        publishToWidgets(pending: queue)
    }

    func refresh() async {
        guard let api = capture.chatAPI() else {
            loadProblem = capture.server == nil
                ? ("No server", "Not connected to a server. Changes stay on this phone until you connect in Settings.")
                : ("Signed out", "Signed out. Changes stay on this phone until you sign in again in Settings.")
            return
        }
        let started = Date()
        do {
            async let fetchedTasks = api.dailyTasks()
            async let fetchedTodos = api.todos()
            // A server from before goals answers 404; the lists still load.
            async let fetchedGoals = try? api.currentGoals()
            async let fetchedReviews = try? api.coachReviews(days: 1)
            // A server from before spoken to-dos answers 404: nothing pending.
            async let fetchedRecordings = try? api.todoRecordings()
            let (tasks, todos) = try await (fetchedTasks, fetchedTodos)
            let yesterday = HabitStreaks.adding(-1, to: DayKey.of(Date()))
            server = TodoLists(tasks: tasks, todos: todos, goals: await fetchedGoals ?? server.goals,
                               review: await fetchedReviews?.review(of: yesterday))
            serverRecordings = await fetchedRecordings ?? []
            fetchedAt = started
            sent.removeAll { $0.at < started }
            loadProblem = nil
            if let data = try? JSONEncoder().encode(server) { try? data.write(to: cacheURL, options: .atomic) }
            publishToWidgets()
        } catch {
            if Task.isCancelled || (error as? URLError)?.code == .cancelled { return }
            loadProblem = ("Offline", "Can't reach your server. Changes are saved on this phone and sent when it's back.")
        }
    }

    private func queue(_ change: TodoChange) { capture.queueTodo(change) }

    /// Hands the Home Screen widgets this view of the lists, and redraws
    /// them if it changed what they show.
    func publishToWidgets(pending: [TodoOp]? = nil) {
        guard let container = SharedSignIn.container() else { return }
        let (fetched, asOf, sentOps, pending) = (server, fetchedAt, sent.map(\.op), pending ?? capture.todoQueue)
        let changed = (try? TodoWidgetSnapshot.update(at: TodoWidgetFiles.snapshot(in: container)) {
            $0 = $0.merging(app: fetched, asOf: asOf, sent: sentOps, pending: pending, now: Date())
        }) ?? false
        if changed { WidgetCenter.shared.reloadAllTimelines() }
    }

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

    /// "After coffee": the if-then half of the habit. Empty clears it.
    func setCue(_ task: DailyTask, _ cue: String) {
        let trimmed = cue.trimmingCharacters(in: .whitespacesAndNewlines)
        let value = trimmed.isEmpty ? nil : trimmed
        guard value != task.cue else { return }
        queue(.setTaskCue(id: task.id, cue: value))
    }

    func moveTasks(from source: IndexSet, to destination: Int) {
        var order = tasks
        order.move(fromOffsets: source, toOffset: destination)
        queue(.reorderTasks(order.map(\.id)))
    }

    func delete(_ task: DailyTask) { queue(.deleteTask(id: task.id)) }

    // MARK: Goals

    /// Replacing the overarching goal leaves a journal entry on the server
    /// quoting the old one, so an unchanged save sends nothing.
    func setOverarching(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != goals?.overarching?.text else { return }
        queue(.setOverarching(id: ULID.make(), text: trimmed))
    }

    func addGoal(_ horizon: GoalHorizon, title: String, details: String? = nil) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        queue(.createGoal(id: ULID.make(), horizon: horizon, title: trimmed, details: Self.clean(details)))
    }

    func edit(_ goal: Goal, title: String, details: String?) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let notes = Self.clean(details)
        guard !trimmed.isEmpty, trimmed != goal.title || notes != goal.details else { return }
        queue(.editGoal(id: goal.id, title: trimmed, details: notes))
    }

    func end(_ goal: Goal) { queue(.setGoalStatus(id: goal.id, action: "end")) }

    private static func clean(_ text: String?) -> String? {
        let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed
    }

    // MARK: To-dos

    func toggle(_ todo: TodoItem) { queue(.setTodo(id: todo.id, done: !todo.done)) }

    func create(_ draft: TodoDraft) {
        var draft = draft
        draft.id = ULID.make()
        queue(.createTodo(draft))
    }

    func edit(_ todo: TodoItem, _ draft: TodoDraft) { queue(.editTodo(id: todo.id, draft)) }

    /// The mic beside "Add to-do": the clip is queued and sent, and the
    /// server transcribes it and makes the to-do. Nothing here waits for that.
    func dictated(_ clip: URL) { capture.queueTodoRecording(clip) }

    func discard(_ recording: TodoRecording) { capture.discard(recording) }

    /// Drops one the server failed on, clip and all.
    func dismiss(_ recording: ServerTodoRecording) async {
        guard let api = capture.chatAPI() else { return }
        do {
            try await api.dismissTodoRecording(recording.id)
            serverRecordings.removeAll { $0.id == recording.id }
        } catch {
            capture.message = error.localizedDescription
        }
    }

    func move(_ todo: TodoItem) { queue(.moveTodo(id: todo.id, list: todo.isArchived ? "todo" : "archive")) }

    func delete(_ todo: TodoItem) { queue(.deleteTodo(id: todo.id)) }
}
