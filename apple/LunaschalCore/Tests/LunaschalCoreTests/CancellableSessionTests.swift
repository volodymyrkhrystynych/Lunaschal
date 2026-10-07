import Foundation
import XCTest
@testable import LunaschalCore

final class CancellableSessionTests: XCTestCase {
    private func assertCancelled(_ call: () async throws -> Void, file: StaticString = #filePath, line: UInt = #line) async {
        do {
            try await call()
            XCTFail("expected URLError(.cancelled)", file: file, line: line)
        } catch {
            XCTAssertEqual((error as? URLError)?.code, .cancelled, "\(error)", file: file, line: line)
        }
    }

    // Build 4's TestFlight crashes: a sync pass `try?`s past a cancelled
    // request and makes the next one. That call must throw, not abort.
    func testRequestsAfterCancelThrowInsteadOfCrashing() async throws {
        let api = try JournalAPI(server: URL(string: "https://host.example")!, token: "t", allowCellular: true)
        api.cancel()
        XCTAssertTrue(api.session.isCancelled)
        await assertCancelled { _ = try await api.weatherToday() }
        await assertCancelled { _ = try await api.dailyStatus(day: "2026-10-06") }
        await assertCancelled { _ = try await api.recentExercises() }
        // A second cancel, as signOut() → cancelSync() + pauseLibrary() can do, is harmless.
        api.cancel()
        await assertCancelled { _ = try await api.weatherToday() }
    }

    func testEveryRequestShapeIsGuarded() async throws {
        let session = CancellableSession(configuration: .ephemeral, delegate: nil)
        session.cancel()
        let request = URLRequest(url: URL(string: "https://host.example/api")!)
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data().write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }
        await assertCancelled { _ = try await session.data(for: request) }
        await assertCancelled { _ = try await session.upload(for: request, fromFile: file) }
        await assertCancelled { _ = try await session.download(for: request) }
        #if canImport(Darwin)
        await assertCancelled { _ = try await session.bytes(for: request) }
        #endif
    }

    func testAFreshSessionIsNotCancelled() {
        XCTAssertFalse(CancellableSession(configuration: .ephemeral, delegate: nil).isCancelled)
    }
}
