import Foundation
import XCTest
@testable import LunaschalCore

/// A HealthKit stand-in: each stream is a list of objects, and an anchor is
/// simply how many of them have been handed out.
private final class FakeSource: HealthSource {
    var objects: [String: [HealthSampleRecord]] = [:]
    var totals: [HealthDailyTotal] = []
    var dailyRequests: [(String, String)] = []
    var streams: [String] { objects.keys.sorted() }

    func page(stream: String, anchor: Data?, limit: Int) async throws -> HealthPage {
        let offset = anchor.flatMap { Int(String(decoding: $0, as: UTF8.self)) } ?? 0
        let all = objects[stream] ?? []
        let slice = Array(all[min(offset, all.count)..<min(offset + limit, all.count)])
        return HealthPage(samples: slice, anchor: Data(String(offset + slice.count).utf8))
    }

    func dailyTotals(first: String, last: String) async throws -> [HealthDailyTotal] {
        dailyRequests.append((first, last))
        return totals.filter { $0.date >= first && $0.date <= last }
    }
}

private final class FakeTransport: HealthTransport {
    var batches: [HealthBatch] = []
    var failOn: Int?
    var lie = false

    func sendHealth(_ batch: HealthBatch) async throws -> HealthAck {
        if failOn == batches.count { throw URLError(.notConnectedToInternet) }
        batches.append(batch)
        return HealthAck(samples: batch.samples.count + (lie ? 1 : 0), workouts: batch.workouts.count,
                         deleted: batch.deleted.count, daily: batch.daily.count, rejectedCount: 0)
    }
}

private func samples(_ n: Int, type: String = "HKQuantityTypeIdentifierHeartRate") -> [HealthSampleRecord] {
    (0..<n).map {
        HealthSampleRecord(uuid: UUID().uuidString, type: type, kind: .quantity,
                           start: Double(1_790_000_000 + $0), end: Double(1_790_000_000 + $0),
                           value: 60, unit: "count/min")
    }
}

@MainActor
final class HealthSyncTests: XCTestCase {
    // Set up and torn down outside the main actor (Linux XCTest's setUp is
    // nonisolated), strictly before and after each test touches them.
    nonisolated(unsafe) private var root: URL!
    nonisolated(unsafe) private var store: HealthStateStore!
    private var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        return calendar
    }()
    private lazy var now = calendar.date(from: DateComponents(year: 2026, month: 10, day: 6, hour: 9))!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try HealthStateStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func sync() -> HealthSync { HealthSync(store: store, now: { self.now }, calendar: calendar) }

    func testAStreamIsDrainedInPagesAndNotReadAgain() async throws {
        let source = FakeSource()
        source.objects["HKQuantityTypeIdentifierHeartRate"] = samples(HealthSync.pageLimit * 2 + 5)
        let transport = FakeTransport()
        try await sync().run(source: source, transport: transport)
        XCTAssertEqual(transport.batches.filter { !$0.samples.isEmpty }.map(\.samples.count),
                       [HealthSync.pageLimit, HealthSync.pageLimit, 5])

        transport.batches = []
        try await sync().run(source: source, transport: transport)
        XCTAssertTrue(transport.batches.allSatisfy { $0.samples.isEmpty }, "a second pass resends nothing")
        XCTAssertEqual(store.status().sent, HealthSync.pageLimit * 2 + 5)
    }

    func testTheAnchorMovesOnlyAfterTheServerAcknowledges() async throws {
        let source = FakeSource()
        source.objects["HKQuantityTypeIdentifierHeartRate"] = samples(HealthSync.pageLimit + 10)
        let transport = FakeTransport()
        transport.failOn = 1
        do {
            try await sync().run(source: source, transport: transport)
            XCTFail("expected the offline error")
        } catch {}
        XCTAssertEqual(store.status().lastError, URLError(.notConnectedToInternet).localizedDescription)

        // The first page landed and stays landed; only the unacknowledged one
        // is read and sent again.
        transport.failOn = nil
        transport.batches = []
        try await sync().run(source: source, transport: transport)
        XCTAssertEqual(transport.batches.first?.samples.count, 10)
        XCTAssertNil(store.status().lastError)
    }

    func testAReplyThatDoesNotAddUpLeavesTheAnchorAlone() async throws {
        let source = FakeSource()
        source.objects["HKQuantityTypeIdentifierStepCount"] = samples(3)
        let transport = FakeTransport()
        transport.lie = true
        do {
            try await sync().run(source: source, transport: transport)
            XCTFail("expected unaccounted")
        } catch { XCTAssertEqual(error as? HealthSyncError, .unaccounted) }
        XCTAssertNil(store.anchor("HKQuantityTypeIdentifierStepCount"))
    }

    func testDailyTotalsReachBackOnceThenOnlyTheLastFewDays() async throws {
        let source = FakeSource()
        source.totals = [HealthDailyTotal(date: "2026-10-05", type: "HKQuantityTypeIdentifierStepCount",
                                          value: 9000, unit: "count")]
        let transport = FakeTransport()
        try await sync().run(source: source, transport: transport)
        XCTAssertEqual(source.dailyRequests.last?.1, "2026-10-06")
        XCTAssertEqual(source.dailyRequests.last?.0, HealthSync.day("2026-10-06", minus: HealthSync.historyDays))
        XCTAssertEqual(transport.batches.last?.daily.count, 1)
        XCTAssertEqual(store.status().dailyThrough, "2026-10-06")

        // The next morning, yesterday and the day before are recomputed too.
        now = calendar.date(from: DateComponents(year: 2026, month: 10, day: 7, hour: 9))!
        try await sync().run(source: source, transport: transport)
        XCTAssertEqual(source.dailyRequests.last?.0, "2026-10-04")
        XCTAssertEqual(source.dailyRequests.last?.1, "2026-10-07")
    }

    func testDailyTotalsUseTheFourAmDay() async throws {
        now = calendar.date(from: DateComponents(year: 2026, month: 10, day: 7, hour: 2))!
        let source = FakeSource()
        try await sync().run(source: source, transport: FakeTransport())
        XCTAssertEqual(store.status().dailyThrough, "2026-10-06")
    }

    func testDayArithmeticCrossesMonthsAndLeapDays() {
        XCTAssertEqual(HealthSync.day("2026-03-01", minus: 1), "2026-02-28")
        XCTAssertEqual(HealthSync.day("2028-03-01", minus: 1), "2028-02-29")
        XCTAssertEqual(HealthSync.day("2026-01-01", minus: 365), "2025-01-01")
    }

    func testResetStartsOver() async throws {
        let source = FakeSource()
        source.objects["HKQuantityTypeIdentifierHeartRate"] = samples(4)
        let transport = FakeTransport()
        try await sync().run(source: source, transport: transport)
        try store.reset()
        transport.batches = []
        try await sync().run(source: source, transport: transport)
        XCTAssertEqual(transport.batches.first?.samples.count, 4)
    }

    func testABackgroundPassIsDueByAge() {
        var status = HealthStateStore.Status()
        XCTAssertTrue(HealthSync.isDue(status, now: now))
        status.lastSuccess = now.addingTimeInterval(-3600)
        XCTAssertFalse(HealthSync.isDue(status, now: now))
        status.lastSuccess = now.addingTimeInterval(-4 * 3600)
        XCTAssertTrue(HealthSync.isDue(status, now: now))
    }

    func testTheBatchEncodesAsTheServerReadsIt() throws {
        let batch = HealthBatch(
            samples: [HealthSampleRecord(uuid: "A", type: "HKCategoryTypeIdentifierSleepAnalysis", kind: .category,
                                         start: 1.5, end: 2, value: 3, unit: nil)],
            workouts: [HealthWorkoutRecord(uuid: "B", activityType: 37, activityName: "running",
                                           start: 1, end: 2, duration: 1, energy: 10)],
            deleted: ["C"])
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(batch)) as! [String: Any]
        let sample = (json["samples"] as! [[String: Any]])[0]
        XCTAssertEqual(sample["kind"] as? String, "category")
        XCTAssertEqual(sample["start"] as? Double, 1.5)
        XCTAssertEqual((json["workouts"] as! [[String: Any]])[0]["activityType"] as? Int, 37)
        XCTAssertEqual(json["deleted"] as? [String], ["C"])
        XCTAssertEqual((json["daily"] as? [Any])?.count, 0)
    }
}
