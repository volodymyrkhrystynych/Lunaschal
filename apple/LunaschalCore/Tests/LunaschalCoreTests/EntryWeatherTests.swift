import Foundation
import XCTest
@testable import LunaschalCore

/// The weather on an entry: where the location comes from, and how it reads.
final class EntryWeatherTests: XCTestCase {
    private var root: URL!
    private var store: CaptureStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try CaptureStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    private let windy = EntryWeather(hourTs: 1_790_000_000, weatherCode: 3, temperatureC: 3.2, apparentC: -2.6,
                                     windKmh: 34.4, gustKmh: 58, windy: true, isDay: true)

    // The same cases, and the same strings, as src/lib/entryWeather.test.ts.
    func testTheSummaryReadsLikeTheDesktops() {
        XCTAssertEqual(windy.summary, "☁️ Overcast 3°C · feels -3°C · windy 34 km/h, gusts 58 · sun up")
        let calm = EntryWeather(hourTs: windy.hourTs, weatherCode: 3, temperatureC: 3.2, apparentC: -2.6,
                                windKmh: 8, gustKmh: 15, windy: false, isDay: true)
        XCTAssertTrue(calm.summary.contains("wind 8 km/h · "))
        let night = EntryWeather(hourTs: windy.hourTs, weatherCode: 0, temperatureC: 3.2, isDay: false)
        XCTAssertTrue(night.summary.hasSuffix("sun down"))
        XCTAssertTrue("🌑🌒🌓🌔🌕🌖🌗🌘".contains(night.summary.first!))
        XCTAssertEqual(EntryWeather(hourTs: windy.hourTs, weatherCode: 3, temperatureC: 3.2).summary, "☁️ Overcast 3°C")
    }

    func testRoundingHalvesUpAsJavaScriptDoes() {
        XCTAssertEqual(EntryWeather.degrees(-2.5), "-2°C")
        XCTAssertEqual(EntryWeather.degrees(2.5), "3°C")
        XCTAssertEqual(EntryWeather.degrees(-2.6), "-3°C")
    }

    func testTheStoredColumnParsesAndJunkDoesNot() {
        let json = #"{"hourTs": 1790000000, "weatherCode": 3, "temperatureC": 3.2, "apparentC": -2.6, "humidityPct": 70,"#
            + #" "windKmh": 34.4, "gustKmh": 58, "windy": true, "isDay": true, "latitude": 43.6, "longitude": -79.4}"#
        XCTAssertEqual(EntryWeather.parse(json), windy)
        XCTAssertNil(EntryWeather.parse(nil))
        XCTAssertNil(EntryWeather.parse("not json"))
    }

    func testAForecastHourReadsTheSameWay() {
        var hour = WeatherDay.Hour(id: "h", dayKey: "2026-10-04", hourTs: Date(timeIntervalSince1970: 1_790_000_000),
                                   weatherCode: 3, temperatureC: 3.2)
        hour.apparentC = -2.6; hour.windKmh = 34.4; hour.gustKmh = 58; hour.isDay = true
        XCTAssertEqual(EntryWeather(hour, in: WeatherDay(hours: [hour])).summary, windy.summary)
        // An hour synced before is_day existed falls back to the sun times.
        var older = hour; older.isDay = nil
        let dark = WeatherDay(hours: [older], sunriseTs: older.hourTs.addingTimeInterval(3600),
                              sunsetTs: older.hourTs.addingTimeInterval(7200))
        XCTAssertEqual(EntryWeather(older, in: dark).isDay, false)
        XCTAssertNil(EntryWeather(older, in: WeatherDay(hours: [older])).isDay)
    }

    func testTheSaveLocationGoesToTheServerWithTheEntry() throws {
        let capture = try store.commitDraft(text: "By the lake", youtubeURLs: [], location: (44.25, -78.5))
        XCTAssertEqual(try store.load(capture.id).latitude, 44.25)
        _ = try store.stageFile(data: Data("jpeg".utf8), name: "Meal.jpg", contentType: "image/jpeg")
        let meal = try store.commitDraft(text: "Ramen", youtubeURLs: [], kind: .food, location: (45.5, -73.6))
        let body = try FoodMultipart(capture: meal, files: meal.files.map(store.fileURL))
        defer { try? FileManager.default.removeItem(at: body.url) }
        let text = String(decoding: try Data(contentsOf: body.url), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"latitude\"\r\n\r\n45.5\r\n"))
        XCTAssertTrue(text.contains("name=\"longitude\"\r\n\r\n-73.6\r\n"))
    }

    func testAnUnlocatedSaveSendsNoCoordinates() throws {
        let meal = try store.commitDraft(text: "Toast", youtubeURLs: [], kind: .food)
        XCTAssertNil(meal.latitude)
        let body = try FoodMultipart(capture: meal, files: [])
        defer { try? FileManager.default.removeItem(at: body.url) }
        XCTAssertFalse(String(decoding: try Data(contentsOf: body.url), as: UTF8.self).contains("latitude"))
    }

    func testOlderManifestsHaveNoLocationOrWeather() throws {
        let saved = try store.commitDraft(text: "Before weather", youtubeURLs: [])
        var json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(saved)) as! [String: Any]
        for key in ["latitude", "longitude", "weather"] { json.removeValue(forKey: key) }
        let decoded = try JSONDecoder().decode(Capture.self, from: JSONSerialization.data(withJSONObject: json))
        XCTAssertNil(decoded.latitude)
        XCTAssertNil(decoded.entryWeather)
    }

    @MainActor
    func testAMealsWeatherIsReadBackUntilTheServerHasIt() async throws {
        let meal = try store.commitDraft(text: "Ramen", youtubeURLs: [], kind: .food)
        let server = MealServer()
        let sync = CaptureSync(store: store)
        try await sync.run(using: server)
        XCTAssertNil(try store.load(meal.id).weather)
        server.weather = #"{"hourTs": 1790000000, "weatherCode": 2, "temperatureC": 14}"#
        try await sync.run(using: server)
        XCTAssertEqual(try store.load(meal.id).entryWeather?.temperatureC, 14)
        // Once it has weather it is not asked again.
        let asked = server.asked
        try await sync.run(using: server)
        XCTAssertEqual(server.asked, asked)
    }
}

private final class MealServer: JournalTransport {
    var weather: String?
    var asked = 0
    func send(_ capture: Capture, audioURL: URL?) async throws {}
    func fetch(_ id: String) async throws -> JournalSnapshot { throw HTTPFailure(status: 404) }
    func foodWeather(_ id: String) async throws -> String? { asked += 1; return weather }
}
