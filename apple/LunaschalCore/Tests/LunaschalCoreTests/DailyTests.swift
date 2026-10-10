import Foundation
import XCTest
@testable import LunaschalCore

final class DailyTests: XCTestCase {
    private var root: URL!
    private var store: DailyStore!
    private var calendar: Calendar = {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        return calendar
    }()

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try DailyStore(root: root, calendar: calendar)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private func at(_ day: Int, _ hour: Int, _ minute: Int = 0) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 10, day: day, hour: hour, minute: minute))!
    }

    // The cases from parseCalorieEntry's tests in src/lib/lifestyle.test.ts.
    func testACalorieLineSplitsLikeTheDesktops() {
        XCTAssertEqual(CalorieLine.parse("chicken breast and rice, ~600"),
                       CalorieLine(description: "chicken breast and rice", calories: 600))
        XCTAssertEqual(CalorieLine.parse("protein shake 180"), CalorieLine(description: "protein shake", calories: 180))
        XCTAssertEqual(CalorieLine.parse("oats 320 kcal")?.calories, 320)
        XCTAssertEqual(CalorieLine.parse("oats 320cal")?.calories, 320)
        XCTAssertEqual(CalorieLine.parse("Oats 320 KCAL")?.calories, 320)
        XCTAssertEqual(CalorieLine.parse("2 eggs and toast 400"), CalorieLine(description: "2 eggs and toast", calories: 400))
        XCTAssertEqual(CalorieLine.parse("  burrito  -  850  ")?.description, "burrito")
        for nothing in ["protein shake", "600", "~600", ""] { XCTAssertNil(CalorieLine.parse(nothing), nothing) }
    }

    func testTheDayTurnsOverAtFourInTheMorning() {
        XCTAssertEqual(DayKey.of(at(4, 3, 59), calendar: calendar), "2026-10-03")
        XCTAssertEqual(DayKey.of(at(4, 4, 0), calendar: calendar), "2026-10-04")
        XCTAssertEqual(DayKey.of(at(4, 23, 30), calendar: calendar), "2026-10-04")
    }

    func testTheDayIsFixedWhenLoggedNotWhenUploaded() throws {
        let log = try store.logWeight(80.5, now: at(5, 1, 30))
        XCTAssertEqual(log.day, "2026-10-04")
        XCTAssertEqual(try DailyStore(root: root, calendar: calendar).list().first?.day, "2026-10-04")
    }

    func testOutOfRangeInputIsRefusedBeforeItIsQueued() {
        XCTAssertThrowsError(try store.logWeight(0)) { XCTAssertEqual($0 as? DailyError, .invalidWeight) }
        XCTAssertThrowsError(try store.logWeight(.nan)) { XCTAssertEqual($0 as? DailyError, .invalidWeight) }
        XCTAssertThrowsError(try store.logCalories(20001, description: "Feast")) { XCTAssertEqual($0 as? DailyError, .invalidCalories) }
        XCTAssertThrowsError(try store.logCalories(300, description: "  ")) { XCTAssertEqual($0 as? DailyError, .missingDescription) }
        XCTAssertThrowsError(try store.logSelfie(jpeg: Data())) { XCTAssertEqual($0 as? DailyError, .missingImage) }
        XCTAssertEqual(try store.list(), [])
    }

    func testANewerWeightOrSelfieReplacesTheUnsentOne() throws {
        let first = try store.logSelfie(jpeg: Data("one".utf8), now: at(4, 9))
        _ = try store.logWeight(80, now: at(4, 9))
        let second = try store.logSelfie(jpeg: Data("two".utf8), now: at(4, 10))
        let weight = try store.logWeight(79.8, now: at(4, 10))
        // Calories add up rather than replace.
        _ = try store.logCalories(300, description: "Oats", now: at(4, 9))
        _ = try store.logCalories(600, description: "Rice", now: at(4, 12))
        let logs = try store.list()
        XCTAssertEqual(logs.filter { $0.kind == .selfie }.map(\.id), [second.id])
        XCTAssertEqual(logs.filter { $0.kind == .weight }.map(\.id), [weight.id])
        XCTAssertEqual(logs.filter { $0.kind == .calories }.count, 2)
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.imageURL(first).path))
        XCTAssertEqual(try Data(contentsOf: store.imageURL(second)), Data("two".utf8))
    }

    func testTheSummaryLaysUnsentLogsOverTheServersRecord() throws {
        let synced = try store.logCalories(300, description: "Oats", now: at(4, 9))
        var mark = synced; mark.state = .synced; try store.save(mark)
        _ = try store.logCalories(600, description: "Rice", now: at(4, 12))
        _ = try store.logWeight(79.8, now: at(4, 12))
        let server = DailyStatus(day: "2026-10-04", weight: 80.2, selfie: .init(id: ULID.make()),
                                 entries: [.init(id: synced.id, description: "Oats", calories: 300),
                                           .init(id: ULID.make(), description: "Desktop lunch", calories: 500)])
        let summary = DailySummary(day: "2026-10-04", server: server, local: try store.list())
        XCTAssertEqual(summary.entries.map(\.description), ["Oats", "Desktop lunch", "Rice"])
        XCTAssertEqual(summary.entries.map(\.waiting), [false, false, true])
        XCTAssertEqual(summary.total, 1400)
        XCTAssertEqual(summary.weight, 79.8)
        XCTAssertTrue(summary.weightWaiting)
        XCTAssertTrue(summary.hasSelfie)
    }

    func testOfflineTheSummaryIsWhatThisDeviceLogged() throws {
        let synced = try store.logWeight(80, now: at(4, 9))
        var mark = synced; mark.state = .synced; try store.save(mark)
        _ = try store.logCalories(300, description: "Oats", now: at(4, 9))
        _ = try store.logCalories(900, description: "Yesterday", now: at(3, 20))
        let summary = DailySummary(day: "2026-10-04", server: nil, local: try store.list())
        XCTAssertEqual(summary.weight, 80)
        XCTAssertFalse(summary.weightWaiting)
        XCTAssertEqual(summary.total, 300)
        XCTAssertFalse(summary.hasSelfie)
        // A status for another day is not today's.
        let stale = DailySummary(day: "2026-10-04", server: DailyStatus(day: "2026-10-03", weight: 81), local: [])
        XCTAssertNil(stale.weight)
    }

    @MainActor
    func testSyncSendsOldestFirstAndARefusedLogDoesNotBlockTheRest() async throws {
        let weight = try store.logWeight(80, now: at(4, 8))
        let refused = try store.logCalories(300, description: "Oats", now: at(4, 9))
        let selfie = try store.logSelfie(jpeg: Data("jpeg".utf8), now: at(4, 10))
        let transport = FakeDaily(refuse: [refused.id: 400])
        try await DailySync(store: store, now: { self.at(4, 11) }).run(using: transport)
        XCTAssertEqual(transport.sent.map(\.0), [weight.id, refused.id, selfie.id])
        XCTAssertEqual(transport.sent.last?.1, store.imageURL(selfie))
        let states = Dictionary(uniqueKeysWithValues: try store.list().map { ($0.id, $0.state) })
        XCTAssertEqual(states, [weight.id: .synced, refused.id: .failed, selfie.id: .synced])
    }

    @MainActor
    func testAnUnreachableServerStopsThePassAndKeepsEverythingQueued() async throws {
        let first = try store.logWeight(80, now: at(4, 8))
        _ = try store.logCalories(300, description: "Oats", now: at(4, 9))
        let transport = FakeDaily(refuse: [first.id: 503])
        do {
            try await DailySync(store: store).run(using: transport)
            XCTFail("expected the 503 to stop the pass")
        } catch {}
        XCTAssertEqual(transport.sent.count, 1)
        XCTAssertEqual(try store.list().map(\.state), [.pending, .pending])
        XCTAssertNotNil(try store.list().first?.lastError)
    }

    @MainActor
    func testSyncedLogsFromEarlierDaysArePruned() async throws {
        let old = try store.logSelfie(jpeg: Data("old".utf8), now: at(3, 9))
        let today = try store.logWeight(80, now: at(4, 9))
        try await DailySync(store: store, now: { self.at(4, 12) }).run(using: FakeDaily())
        XCTAssertEqual(try store.list().map(\.id), [today.id])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.imageURL(old).path))
    }

    func testAcknowledgementsMustMatchTheDayOrTheEntry() throws {
        let weight = try store.logWeight(80, now: at(4, 9))
        let calories = try store.logCalories(300, description: "Oats", now: at(4, 9))
        let ok = Data(#"{"id":"x","date":"2026-10-04","weight":80}"#.utf8)
        XCTAssertNoThrow(try JournalAPI.validateDailyAcknowledgement(ok, for: weight))
        let wrongDay = Data(#"{"id":"x","date":"2026-10-05"}"#.utf8)
        XCTAssertThrowsError(try JournalAPI.validateDailyAcknowledgement(wrongDay, for: weight))
        let wrongID = Data(#"{"id":"\#(ULID.make())","date":"2026-10-04"}"#.utf8)
        XCTAssertThrowsError(try JournalAPI.validateDailyAcknowledgement(wrongID, for: calories))
        let mine = Data(#"{"id":"\#(calories.id)","date":"2026-10-04"}"#.utf8)
        XCTAssertNoThrow(try JournalAPI.validateDailyAcknowledgement(mine, for: calories))
    }

    func testSpendingAmountsUseExactCents() {
        for (text, cents) in [("15", 1500), ("30.01", 3001), (" 0,10 ", 10), ("1.5", 150), ("1000000", 100000000)] {
            XCTAssertEqual(SpendingAmount.cents(text), cents)
        }
        for text in ["", "0", "-15", "1.001", "NaN", "1e3", "1,000.00", "1000000.01", "9999999999999999999999999"] {
            XCTAssertNil(SpendingAmount.cents(text), text)
        }
    }

    func testSpendingSurvivesRestartAndMergesWithServerWithoutDoubleCounting() throws {
        let first = try store.logSpending(1500, category: " McDonald's ", now: at(5, 1))
        let second = try store.logSpending(3001, category: "Groceries", now: at(5, 2))
        let reopened = try DailyStore(root: root, calendar: calendar)
        XCTAssertEqual(try reopened.list().map(\.day), ["2026-10-04", "2026-10-04"])
        let server = DailyStatus(day: first.day, spending: [.init(id: first.id, category: "McDonald's", amountCents: 1500)])
        let summary = DailySummary(day: first.day, server: server, local: try reopened.list())
        XCTAssertEqual(summary.totalCents, 4501)
        XCTAssertEqual(summary.spending.map(\.id), [first.id, second.id])
        XCTAssertEqual(summary.spending.map(\.waiting), [false, true])
        XCTAssertEqual(DailySummary(day: first.day, server: nil, local: try reopened.list()).totalCents, 4501)
        XCTAssertThrowsError(try store.logSpending(0, category: "Groceries"))
        XCTAssertThrowsError(try store.logSpending(1, category: " "))
        XCTAssertThrowsError(try store.logSpending(1, category: String(repeating: "x", count: 201)))
        let ack = Data(#"{"id":"\#(first.id)","date":"\#(first.day)"}"#.utf8)
        XCTAssertNoThrow(try JournalAPI.validateDailyAcknowledgement(ack, for: first))
        XCTAssertThrowsError(try JournalAPI.validateDailyAcknowledgement(ack, for: second))
    }

    @MainActor
    func testDeletingAnUnsentCalorieEntrySurvivesRestartAndNeverCreatesIt() async throws {
        let log = try store.logCalories(300, description: "Oats", now: at(4, 9))
        try store.delete(id: log.id, kind: .calories, day: log.day)
        let reopened = try DailyStore(root: root, calendar: calendar)
        XCTAssertEqual(DailySummary(day: log.day, server: nil, local: try reopened.list()).total, 0)
        let transport = FakeDaily(refuse: [log.id: 404])
        try await DailySync(store: reopened, now: { self.at(4, 12) }).run(using: transport)
        XCTAssertEqual(transport.logs.map(\.isDeletion), [true])
        XCTAssertEqual(try reopened.list().first?.state, .synced)
    }

    @MainActor
    func testServerOnlyDeletionHidesStaleRowsAndReplaysAfterLostResponse() async throws {
        let id = ULID.make(), day = "2026-10-04"
        let server = DailyStatus(day: day, entries: [.init(id: id, description: "Lunch", calories: 400)])
        try store.delete(id: id, kind: .calories, day: day)
        let transport = FakeDaily(refuse: [id: 503])
        do { try await DailySync(store: store).run(using: transport); XCTFail("offline") } catch {}
        XCTAssertEqual(try store.list().first?.state, .pending)
        XCTAssertEqual(DailySummary(day: day, server: server, local: try store.list()).total, 0)
        let reopened = try DailyStore(root: root, calendar: calendar)
        try await DailySync(store: reopened, now: { self.at(4, 12) }).run(using: FakeDaily(refuse: [id: 404]))
        XCTAssertEqual(DailySummary(day: day, server: server, local: try reopened.list()).total, 0)
    }

    @MainActor
    func testDeletionDuringUploadIsNotOverwrittenByTheReply() async throws {
        for status in [nil, 503] as [Int?] {
            let log = try store.logSpending(1500, category: "Lunch", now: at(4, 9))
            let transport = FakeDaily(refuse: status.map { [log.id: $0] } ?? [:])
            transport.during = { item in
                if item.id == log.id { try self.store.delete(id: item.id, kind: item.kind, day: item.day) }
            }
            do { try await DailySync(store: store, now: { self.at(4, 12) }).run(using: transport) }
            catch { XCTAssertNotNil(status) }
            let pending = try XCTUnwrap(store.list().first { $0.id == log.id })
            XCTAssertTrue(pending.isDeletion)
            XCTAssertEqual(pending.state, .pending)
            let retry = FakeDaily()
            try await DailySync(store: store, now: { self.at(4, 12) }).run(using: retry)
            XCTAssertEqual(retry.logs.map(\.isDeletion), [true])
            let server = DailyStatus(day: log.day, spending: [.init(id: log.id, category: "Lunch", amountCents: 1500)])
            XCTAssertEqual(DailySummary(day: log.day, server: server, local: try store.list()).totalCents, 0)
        }
    }

    @MainActor
    func testDeletionQueuedBehindAnUploadIsReadFreshBeforeSending() async throws {
        _ = try store.logWeight(80, now: at(4, 8))
        let later = try store.logCalories(300, description: "Oats", now: at(4, 9))
        let transport = FakeDaily()
        transport.during = { log in
            if log.kind == .weight { try self.store.delete(id: later.id, kind: .calories, day: later.day) }
        }
        try await DailySync(store: store, now: { self.at(4, 12) }).run(using: transport)
        XCTAssertEqual(transport.logs.map(\.isDeletion), [false, true])
    }

    @MainActor
    func testRefusedDeletionShowsTheServerRowAgainAndAuthenticationKeepsItPending() async throws {
        let id = ULID.make(), day = "2026-10-04"
        let server = DailyStatus(day: day, spending: [.init(id: id, category: "Lunch", amountCents: 1500)])
        try store.delete(id: id, kind: .spending, day: day)
        do { try await DailySync(store: store).run(using: FakeDaily(refuse: [id: 401])); XCTFail("signed out") } catch {}
        XCTAssertEqual(try store.list().first?.state, .pending)
        try await DailySync(store: store).run(using: FakeDaily(refuse: [id: 400]))
        XCTAssertEqual(try store.list().first?.state, .failed)
        XCTAssertEqual(DailySummary(day: day, server: server, local: try store.list()).totalCents, 1500)
        XCTAssertThrowsError(try store.delete(id: ULID.make(), kind: .weight, day: day))
    }

    func testOldDailyManifestsWithoutSpendingOrDeletionStillDecode() throws {
        let log = try store.logCalories(300, description: "Oats", now: at(4, 9))
        let data = try JSONEncoder().encode(log)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertNil(json["deleted"])
        let restored = try JSONDecoder().decode(DailyLog.self, from: data)
        XCTAssertFalse(restored.isDeletion)
        XCTAssertNil(restored.amountCents)
        XCTAssertEqual(restored, log)
    }

    func testTheSelfieUploadCarriesItsDay() throws {
        let selfie = try store.logSelfie(jpeg: Data("jpeg-bytes".utf8), now: at(5, 2))
        let body = try SelfieMultipart(log: selfie, image: store.imageURL(selfie))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = String(decoding: try Data(contentsOf: body.url), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"date\"\r\n\r\n2026-10-04\r\n"))
        XCTAssertTrue(text.contains("name=\"image\"; filename=\"selfie.jpg\"\r\nContent-Type: image/jpeg"))
        XCTAssertTrue(text.contains("jpeg-bytes"))
    }
}

private final class FakeDaily: DailyTransport {
    var sent: [(String, URL?)] = []
    var logs: [DailyLog] = []
    var during: ((DailyLog) throws -> Void)?
    let refuse: [String: Int]
    init(refuse: [String: Int] = [:]) { self.refuse = refuse }

    func sendDaily(_ log: DailyLog, image: URL?) async throws {
        sent.append((log.id, image))
        logs.append(log)
        try during?(log)
        if let status = refuse[log.id] { throw HTTPFailure(status: status) }
    }

    func dailyStatus(day: String) async throws -> DailyStatus { DailyStatus(day: day) }
}
