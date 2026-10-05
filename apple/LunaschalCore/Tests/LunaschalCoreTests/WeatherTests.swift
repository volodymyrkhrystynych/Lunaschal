import Foundation
import XCTest
@testable import LunaschalCore

/// Mirrors src/lib/weather.test.ts, plus the server payload's shape.
final class WeatherTests: XCTestCase {
    private let fullMoon = MoonPhase(index: 4, name: "Full moon", emoji: "🌕")

    private func iso(_ text: String) -> Date { ISO8601DateFormatter().date(from: text)! }

    func testCodesMapToLabelsAndUnknownIsNotAnError() {
        for (code, label) in [(0, "Clear"), (2, "Partly cloudy"), (45, "Fog"), (61, "Light rain"),
                              (71, "Light snow"), (95, "Thunderstorm")] {
            XCTAssertEqual(WeatherCondition.describe(code).label, label)
        }
        XCTAssertEqual(WeatherCondition.describe(12345), WeatherCondition(label: "Unknown", icon: "❔"))
    }

    func testNightShowsTheMoonOnlyWhenTheSkyIsClearEnough() {
        XCTAssertEqual(WeatherCondition.describe(0, night: true, moon: fullMoon).icon, "🌕")
        XCTAssertEqual(WeatherCondition.describe(1, night: true, moon: fullMoon).icon, "🌕")
        XCTAssertEqual(WeatherCondition.describe(2, night: true, moon: fullMoon).icon, "🌕☁️")
        for code in [3, 45, 61, 71, 95] {
            XCTAssertEqual(WeatherCondition.describe(code, night: true, moon: fullMoon), WeatherCondition.describe(code))
        }
    }

    func testMoonPhaseFollowsTheSynodicMonth() {
        let reference = MoonPhase.referenceNewMoon
        let month = MoonPhase.synodicMonthDays * 86_400
        XCTAssertEqual(MoonPhase.at(reference).name, "New moon")
        XCTAssertEqual(MoonPhase.at(reference.addingTimeInterval(month * 0.55)).name, "Full moon")
        XCTAssertEqual(MoonPhase.at(reference.addingTimeInterval(month * 1.05)).name, "New moon")
    }

    func testNightIsOutsideSunriseToSunsetAndNeverWithoutSunTimes() {
        let day = WeatherDay(hours: [], sunriseTs: iso("2026-08-17T10:00:00Z"), sunsetTs: iso("2026-08-17T22:00:00Z"))
        XCTAssertFalse(day.isNight(iso("2026-08-17T14:00:00Z")))
        XCTAssertTrue(day.isNight(iso("2026-08-17T05:00:00Z")))
        XCTAssertTrue(day.isNight(iso("2026-08-17T23:00:00Z")))
        XCTAssertFalse(WeatherDay(hours: []).isNight(iso("2026-08-17T23:00:00Z")))
        XCTAssertFalse(WeatherDay(hours: [], sunriseTs: iso("2026-08-17T10:00:00Z")).isNight(iso("2026-08-17T23:00:00Z")))
    }

    func testTheCurrentHourIsTheLatestThatHasStarted() {
        func hour(_ text: String) -> WeatherDay.Hour {
            .init(id: text, dayKey: "2026-08-17", hourTs: iso(text), weatherCode: 0, temperatureC: 20)
        }
        XCTAssertNil(WeatherDay(hours: []).currentHourIndex())
        let day = WeatherDay(hours: [hour("2026-08-17T10:00:00Z"), hour("2026-08-17T11:00:00Z"), hour("2026-08-17T12:00:00Z")])
        XCTAssertEqual(day.currentHourIndex(now: iso("2026-08-17T11:30:00Z")), 1)
        XCTAssertEqual(day.currentHourIndex(now: iso("2026-08-17T05:00:00Z")), 0)
    }

    func testTheServersPayloadDecodes() throws {
        // row_to_dict's shape: offset timestamps, the flag as 0/1, extra fields ignored.
        let json = """
        {"hours": [{"id": "01H", "dayKey": "2026-10-04", "hourTs": "2026-10-04T14:00:00+00:00",
                    "weatherCode": 2, "temperatureC": 13.6, "wetBulbC": null, "humidityPct": 71,
                    "isActual": 1, "latitude": 43.6, "longitude": -79.4, "locationSource": "default"}],
         "location": {"latitude": 43.6, "longitude": -79.4, "source": "default"},
         "sunriseTs": "2026-10-04T11:14:00+00:00", "sunsetTs": "2026-10-04T22:50:00+00:00"}
        """
        let day = try WeatherDay.decode(Data(json.utf8))
        XCTAssertEqual(day.hours.first?.hourTs, iso("2026-10-04T14:00:00Z"))
        XCTAssertEqual(day.hours.first?.isActual, true)
        XCTAssertNil(day.hours.first?.wetBulbC)
        XCTAssertEqual(day.location?.source, "default")
        XCTAssertEqual(day.sunsetTs, iso("2026-10-04T22:50:00Z"))
        XCTAssertTrue(day.isFor(day: "2026-10-04"))
        XCTAssertFalse(day.isFor(day: "2026-10-05"))
        // Cached to disk with the default encoder, it must read back the same way.
        XCTAssertEqual(try JSONDecoder().decode(WeatherDay.self, from: JSONEncoder().encode(day)), day)

        let empty = try WeatherDay.decode(Data(#"{"hours": [], "location": null, "sunriseTs": null, "sunsetTs": null}"#.utf8))
        XCTAssertEqual(empty, WeatherDay(hours: []))
    }
}
