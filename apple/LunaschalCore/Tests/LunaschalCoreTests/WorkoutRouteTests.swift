import XCTest
@testable import LunaschalCore

final class WorkoutRouteTests: XCTestCase {
    private let uuid = "6F9619FF-8B86-D011-B42D-00C04FC964FF"

    private func fix(_ ts: Double, lat: Double, accuracy: Double? = 4, speed: Double? = 3) -> WorkoutRoute.Fix {
        .init(ts: ts, latitude: lat, longitude: -79.38, accuracy: accuracy, altitude: 80, speed: speed)
    }

    func testASecondByWatchRouteIsThinnedLikeTheGPS() {
        // A fix a second at 3 m/s: every fourth one is far enough to keep.
        let fixes = (0..<60).map { fix(1_790_000_000 + Double($0), lat: 43.65 + Double($0) * 0.000027) }
        let points = WorkoutRoute(uuid: uuid, fixes: fixes, activity: .running).points
        XCTAssertTrue((14...16).contains(points.count), "\(points.count)")
        XCTAssertTrue(points.allSatisfy { $0.kind == .fine && $0.activity == .running })
        XCTAssertEqual(points.first?.id, "wr-\(uuid)-0")
    }

    func testIdsAreTheFixesPlaceInTheRouteWhateverOrderHealthKitGaveThem() {
        let a = fix(10, lat: 43.65), b = fix(20, lat: 43.651), c = fix(30, lat: 43.652)
        let shuffled = WorkoutRoute(uuid: uuid, fixes: [c, a, b]).points.map(\.id)
        XCTAssertEqual(shuffled, WorkoutRoute(uuid: uuid, fixes: [a, b, c]).points.map(\.id))
        XCTAssertEqual(shuffled, (0..<3).map { WorkoutRoute.pointID(route: uuid, index: $0) })
        XCTAssertLessThanOrEqual(shuffled[0].count, 64)
    }

    func testBlurryFixesAndInvalidSpeedsAreDropped() {
        let points = WorkoutRoute(uuid: uuid, fixes: [
            fix(0, lat: 43.65), fix(10, lat: 43.651, accuracy: 120), fix(20, lat: 43.652, speed: -1),
        ]).points
        XCTAssertEqual(points.map(\.id), ["wr-\(uuid)-0", "wr-\(uuid)-2"])
        XCTAssertNil(points[1].speed)
    }

    func testAWalkLeftRunningOnATrainIsNotAWalk() {
        func activities(_ activity: TrailPolicy.Activity) -> [TrailPolicy.Activity?] {
            WorkoutRoute(uuid: uuid, fixes: [
                fix(0, lat: 43.65, speed: 1.4), fix(10, lat: 43.652, speed: 25), fix(20, lat: 43.654, speed: 8),
                fix(30, lat: 43.656, speed: nil),
            ], activity: activity).points.map(\.activity)
        }
        XCTAssertEqual(activities(.walking), [.walking, nil, .walking, .walking])
        XCTAssertEqual(activities(.running), [.running, nil, .running, .running])
        // Downhill on a bike is still a bike.
        XCTAssertEqual(activities(.cycling), [.cycling, .cycling, .cycling, .cycling])
    }

    func testOnlyWorkoutsThatSayHowYouMovedCarryAnActivity() {
        XCTAssertEqual(WorkoutRoute.activity(forWorkout: "hiking"), .walking)
        XCTAssertEqual(WorkoutRoute.activity(forWorkout: "running"), .running)
        XCTAssertEqual(WorkoutRoute.activity(forWorkout: "cycling"), .cycling)
        XCTAssertNil(WorkoutRoute.activity(forWorkout: "downhillSkiing"))
    }
}
