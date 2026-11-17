import LunaschalCore
import XCTest
@testable import Lunaschal

/// Refreshes overlap: Daily, the Context page, sync and the foreground all ask.
/// One that finishes late must not bring back a prompt answered meanwhile.
@MainActor
final class JournalContextModelTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    /// Answers each fetch with the list it held when the fetch began, but only
    /// once `release` is called for it — so the test decides the finishing order.
    private actor Server: JournalContextTransport {
        var prompts: [JournalContextPrompt]
        private var waiting: [CheckedContinuation<Void, Never>] = []
        init(_ prompts: [JournalContextPrompt]) { self.prompts = prompts }

        func journalContextPrompts() async -> [JournalContextPrompt] {
            let snapshot = prompts
            await withCheckedContinuation { waiting.append($0) }
            return snapshot
        }
        func resolveJournalContext(_ id: String, resolution: JournalContextResolution) {
            prompts.removeAll { $0.id == id }
        }
        var pending: Int { waiting.count }
        func release(_ index: Int) { waiting[index].resume() }
    }

    private func prompt(_ id: String) throws -> JournalContextPrompt {
        try JSONDecoder().decode(JournalContextPrompt.self, from: Data(
            "{\"id\":\"\(id)\",\"createdAt\":\"2026-11-16T12:00:00Z\",\"attachments\":[{\"id\":\"a\(id)\",\"kind\":\"image\",\"name\":\"x\",\"position\":0}]}".utf8))
    }

    private func until(_ server: Server, has count: Int) async {
        while await server.pending < count { await Task.yield() }
    }

    func testALateFetchCannotResurrectADismissedPrompt() async throws {
        let first = try prompt("01FIRST"), second = try prompt("01SECOND")
        let server = Server([first, second])
        let model = JournalContextModel(capture: try CaptureModel(store: CaptureStore(root: root)), transport: server)
        // Settle on the full list.
        let initial = Task { await model.refresh() }
        await until(server, has: 1); await server.release(0); await initial.value
        XCTAssertEqual(model.prompts.map(\.id), [first.id, second.id])

        // A fetch starts, then the user dismisses before it answers.
        let stale = Task { await model.refresh() }
        await until(server, has: 2)
        let problem = await model.dismiss(first)
        XCTAssertNil(problem)
        XCTAssertEqual(model.prompts.map(\.id), [second.id])
        await server.release(1); await stale.value
        XCTAssertEqual(model.prompts.map(\.id), [second.id], "the late answer predates the dismissal")
    }

    func testAnOlderFetchFinishingLastDoesNotOverwriteANewerOne() async throws {
        let first = try prompt("01FIRST"), second = try prompt("01SECOND")
        let server = Server([first, second])
        let model = JournalContextModel(capture: try CaptureModel(store: CaptureStore(root: root)), transport: server)
        let older = Task { await model.refresh() }
        await until(server, has: 1)
        await server.resolveJournalContext(first.id, resolution: .init(action: .dismiss))  // answered elsewhere
        let newer = Task { await model.refresh() }
        await until(server, has: 2)
        await server.release(1); await newer.value
        XCTAssertEqual(model.prompts.map(\.id), [second.id])
        await server.release(0); await older.value
        XCTAssertEqual(model.prompts.map(\.id), [second.id])
    }
}
