import Foundation
import XCTest
@testable import LunaschalCore

final class SleepWeekTests: XCTestCase {
    func testSleepResponseKeepsMissingReadingsDistinctFromZeroInterruptions() throws {
        let data = Data(#"""
        {"days":[
          {"date":"2026-10-08","start":null,"end":null,"asleepMinutes":null,"awakeMinutes":null,"interruptions":null,"efficiency":null,"unobservedMinutes":null},
          {"date":"2026-10-09","start":1791504000,"end":1791532800,"asleepMinutes":465,"awakeMinutes":15,"interruptions":1,"efficiency":96.9,"unobservedMinutes":0},
          {"date":"2026-10-10","start":1791590400,"end":1791619200,"asleepMinutes":480,"awakeMinutes":0,"interruptions":0,"efficiency":100,"unobservedMinutes":0}
        ]}
        """#.utf8)
        let week = try JSONDecoder().decode(SleepWeek.self, from: data)
        XCTAssertNil(week.days[0].asleepMinutes)
        XCTAssertNil(week.days[0].interruptions)
        XCTAssertEqual(week.days[1].asleepMinutes, 465)
        XCTAssertEqual(week.days[1].efficiency, 96.9)
        XCTAssertEqual(week.days[2].interruptions, 0)
        XCTAssertEqual(try JSONDecoder().decode(SleepWeek.self, from: JSONEncoder().encode(week)), week)
        let rolled = week.ending(on: "2026-10-11")
        XCTAssertEqual(rolled.count, 7)
        XCTAssertEqual(rolled.first?.date, "2026-10-05")
        XCTAssertEqual(rolled.last?.date, "2026-10-11")
        XCTAssertNil(rolled.last?.asleepMinutes)
        XCTAssertEqual(rolled[5].asleepMinutes, 480)
        XCTAssertTrue(week.ending(on: "2026-10-20").allSatisfy { $0.asleepMinutes == nil })
    }
}
