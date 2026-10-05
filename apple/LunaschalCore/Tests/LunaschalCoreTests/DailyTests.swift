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
    let refuse: [String: Int]
    init(refuse: [String: Int] = [:]) { self.refuse = refuse }

    func sendDaily(_ log: DailyLog, image: URL?) async throws {
        sent.append((log.id, image))
        if let status = refuse[log.id] { throw HTTPFailure(status: status) }
    }

    func dailyStatus(day: String) async throws -> DailyStatus { DailyStatus(day: day) }
}
