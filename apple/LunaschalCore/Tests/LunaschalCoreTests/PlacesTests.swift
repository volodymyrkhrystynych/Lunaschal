import XCTest
@testable import LunaschalCore

final class PlacesTests: XCTestCase {
    private func period(_ id: String, _ role: PlaceRole = .home, place: String = "flat", from: String = "2026-01-01",
                        to: String? = nil, radius: Int = 150) -> PlaceRolePeriod {
        PlaceRolePeriod(id: id, role: role, placeId: place, placeName: place.capitalized, latitude: 43.6,
                        longitude: -79.4, radiusM: radius, startedOn: from, endedOn: to)
    }

    // MARK: roles

    func testAPeriodIsHalfOpen() {
        let p = period("a", to: "2026-03-01")
        XCTAssertFalse(p.isActive(on: "2025-12-31"))
        XCTAssertTrue(p.isActive(on: "2026-01-01"))
        XCTAssertTrue(p.isActive(on: "2026-02-28"))
        XCTAssertFalse(p.isActive(on: "2026-03-01"))
        XCTAssertTrue(period("b").isActive(on: "2099-01-01"))
        XCTAssertEqual(p.range, "2026-01-01 → 2026-03-01")
        XCTAssertEqual(period("b").range, "since 2026-01-01")
    }

    func testTheRoleOnADayFollowsAMove() {
        let snapshot = PlacesSnapshot(periods: [
            period("old", place: "oldflat", to: "2026-03-01"),
            period("new", place: "newflat", from: "2026-03-01"),
            period("w", .work, place: "office"),
        ])
        XCTAssertEqual(snapshot.home(on: "2026-02-01")?.placeId, "oldflat")
        XCTAssertEqual(snapshot.home(on: "2026-05-01")?.placeId, "newflat")
        XCTAssertNil(snapshot.role(of: "oldflat", on: "2026-05-01"))
        XCTAssertEqual(snapshot.role(of: "office", on: "2026-05-01")?.role, .work)
        XCTAssertEqual(snapshot.history(.home).map(\.id), ["new", "old"])
    }

    func testThePlacesShapeDecodesFromTheServer() throws {
        let place = try JSONDecoder().decode(SavedPlace.self, from: Data("""
        {"id":"01J","name":"Home","notes":"","latitude":43.6,"longitude":-79.4,"radiusM":150,
         "createdAt":"2026-01-01T00:00:00+00:00","updatedAt":"2026-01-01T00:00:00+00:00"}
        """.utf8))
        XCTAssertTrue(place.isLocated)
        let unlocated = try JSONDecoder().decode(SavedPlace.self, from: Data("""
        {"id":"02J","name":"Somewhere","notes":"x","latitude":null,"longitude":null,"radiusM":150}
        """.utf8))
        XCTAssertFalse(unlocated.isLocated)
        let roles = try JSONDecoder().decode([PlaceRolePeriod].self, from: Data("""
        [{"id":"r","role":"work","placeId":"01J","placeName":"Office","latitude":1,"longitude":2,
          "radiusM":150,"startedOn":"2026-01-01","endedOn":null}]
        """.utf8))
        XCTAssertEqual(roles.first?.role, .work)
    }

    func testTheSnapshotSurvivesOnDisk() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("places.json")
        addTeardownBlock { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let cache = PlacesCache(url: url)
        XCTAssertEqual(cache.load(), PlacesSnapshot())
        let snapshot = PlacesSnapshot(places: [SavedPlace(id: "p", name: "Home", latitude: 1, longitude: 2)],
                                      periods: [period("a")])
        cache.save(snapshot)
        XCTAssertEqual(cache.load(), snapshot)
    }

    // MARK: geofences

    func testTheCurrentHomeAndEveryCurrentWorkAreMonitored() {
        let snapshot = PlacesSnapshot(periods: [
            period("old", place: "oldflat", to: "2026-03-01"),
            period("new", place: "newflat", from: "2026-03-01", radius: 40),
            period("w1", .work, place: "office"),
            period("w2", .work, place: "cowork", to: "2026-04-01"),
        ])
        let regions = RegionPlan.regions(snapshot, today: "2026-05-01")
        XCTAssertEqual(regions.map(\.placeID), ["newflat", "office"])
        // A tiny saved radius is widened to what iOS can actually tell apart.
        XCTAssertEqual(regions.first?.radius, RegionPlan.minRadius)
    }

    func testAnEntryAndItsExitAreOneTrailPoint() throws {
        let office = MonitoredRegion(placeID: "01JOFFICEPLACEID0000000000", name: "Office", role: .work,
                                     latitude: 43.65, longitude: -79.38, radius: 150)
        var ledger = RegionLedger()
        let entry = ledger.enter(office, at: 1_783_000_000)
        // iOS repeating the entry after a relaunch keeps the first arrival.
        XCTAssertEqual(ledger.enter(office, at: 1_783_000_500), entry)
        let exit = try XCTUnwrap(ledger.exit(office, at: 1_783_030_000))
        XCTAssertEqual(exit.id, entry.id)
        XCTAssertEqual(exit.kind, .region)
        XCTAssertEqual(exit.placeId, office.placeID)
        XCTAssertEqual(exit.ts, 1_783_000_000)
        XCTAssertEqual(exit.departed, 1_783_030_000)
        // The server's id rule.
        XCTAssertNotNil(entry.id.range(of: "^[A-Za-z0-9_-]{8,64}$", options: .regularExpression))
        // An exit with no entry is iOS reporting the starting state.
        XCTAssertNil(ledger.exit(office, at: 1_783_040_000))
    }

    func testAnUnmonitoredRegionDropsItsOpenStay() {
        let home = MonitoredRegion(placeID: "home-place", name: "Home", role: .home, latitude: 1, longitude: 2, radius: 100)
        var ledger = RegionLedger()
        _ = ledger.enter(home, at: 10)
        ledger.keepOnly([])
        XCTAssertTrue(ledger.open.isEmpty)
    }

    func testTrailPointsCarryActivityButNotUnknown() throws {
        let point = TrailPoint(id: "fix-1", ts: 1, latitude: 1, longitude: 2, kind: .fine, activity: .cycling)
        let json = try XCTUnwrap(String(data: JSONEncoder().encode(point), encoding: .utf8))
        XCTAssertTrue(json.contains("\"activity\":\"cycling\""))
        XCTAssertFalse(json.contains("placeId"))
        XCTAssertNil(TrailPoint(id: "fix-2", ts: 1, latitude: 1, longitude: 2, kind: .fine, activity: .unknown).activity)
        // A point queued before the fields existed still decodes.
        let old = try JSONDecoder().decode(TrailPoint.self, from: Data(
            #"{"id":"fix-3","ts":1,"latitude":1,"longitude":2,"kind":"coarse"}"#.utf8))
        XCTAssertNil(old.activity)
    }

    // MARK: the day card

    func testTheWhereaboutsLineMatchesTheWeb() throws {
        let utc = try XCTUnwrap(TimeZone(identifier: "UTC"))
        let w = DayWhereabouts(
            home: .init(seconds: 9000, lastArrival: "2026-07-08T18:40:00+00:00", here: true),
            work: [.init(name: "Office", seconds: 7 * 3600 + 20 * 60, firstArrival: nil, lastDeparture: nil),
                   .init(name: "Cowork", seconds: 3600, firstArrival: nil, lastDeparture: nil)])
        XCTAssertEqual(Whereabouts.line(w, zone: utc), "7h 20m at Office · 1h at Cowork · home from 18:40")
        let left = DayWhereabouts(home: .init(seconds: 4 * 3600, lastArrival: nil, here: false), work: [])
        XCTAssertEqual(Whereabouts.line(left, zone: utc), "4h at home")
        XCTAssertNil(Whereabouts.line(nil))
        XCTAssertNil(Whereabouts.line(DayWhereabouts(home: nil, work: [])))
    }

    func testACommuteReadsAsTimeTargetModeAndDistance() {
        let trip = Commute(direction: "toWork", from: "Home", to: "Office", departed: nil, arrived: nil,
                           seconds: 1800, meters: 2140, mode: "cycling", modeSource: "motion")
        XCTAssertEqual(Whereabouts.commuteLine(trip), "30m to Office by bike (2.1 km)")
        let back = Commute(direction: "toHome", from: "Office", to: "Home", departed: nil, arrived: nil,
                           seconds: 2700, meters: 14800, mode: "automotive", modeSource: "speed")
        XCTAssertEqual(Whereabouts.commuteLine(back), "45m home by car or transit (15 km)")
    }

    func testTheJournalDaysAndPatternsDecode() throws {
        let days = try JSONDecoder().decode([LocationDay].self, from: Data("""
        [{"date":"2026-07-08","archivedAt":"2026-07-09T07:59:59+00:00","pointCount":3,"placeCount":1,
          "entryCount":0,"imageUrl":"/api/location/days/2026-07-08/map.png?v=abc","whereabouts":null,"commutes":[]},
         {"date":"2026-07-07","archivedAt":"2026-07-08T07:59:59+00:00","pointCount":3,"placeCount":1,
          "entryCount":0,"imageUrl":"/api/location/days/2026-07-07/map.png?v=abd"}]
        """.utf8))
        XCTAssertEqual(days.map(\.id), ["2026-07-08", "2026-07-07"])
        XCTAssertNil(days[1].commutes)
        let patterns = try JSONDecoder().decode(LocationPatterns.self, from: Data("""
        {"weeks":[{"weekStart":"2026-07-06","observedDays":2,"officeDays":1,"workSeconds":30600,"homeSeconds":1,
          "avgArrival":"09:00","avgDeparture":"17:30",
          "commute":{"toWork":{"count":1,"avgSeconds":1800},"toHome":{"count":0,"avgSeconds":null},"modes":{"cycling":1}}}],
         "weekdays":[{"weekday":"Mon","observedDays":0,"officeDays":0}],
         "commute":{"toWork":{"count":1,"avgSeconds":1800},"toHome":{"count":0,"avgSeconds":null},"modes":{}},
         "observedDays":2,"officeDays":1,"days":56,"since":"2026-05-14"}
        """.utf8))
        XCTAssertEqual(patterns.weeks.first?.commute.toWork.avgSeconds, 1800)
        XCTAssertNil(patterns.weeks.first?.commute.toHome.avgSeconds)
    }
}

final class DayKeyPickerTests: XCTestCase {
    func testAPickedDateIsTheCalendarDateWithNoRollover() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try XCTUnwrap(TimeZone(identifier: "America/Toronto"))
        let early = try XCTUnwrap(calendar.date(from: DateComponents(year: 2026, month: 3, day: 3, hour: 2)))
        XCTAssertEqual(DayKey.picked(early, calendar: calendar), "2026-03-03")
        XCTAssertEqual(DayKey.of(early, calendar: calendar), "2026-03-02")
        let noon = try XCTUnwrap(DayKey.date("2026-03-03", calendar: calendar))
        XCTAssertEqual(DayKey.picked(noon, calendar: calendar), "2026-03-03")
        XCTAssertNil(DayKey.date("nonsense"))
    }
}
