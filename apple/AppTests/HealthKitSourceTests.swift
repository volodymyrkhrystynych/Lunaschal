import XCTest
import HealthKit
import LunaschalCore
@testable import Lunaschal

/// The parts of the HealthKit adapter that need HealthKit but not permission:
/// the catalog's units, and the anchored read against an empty store.
final class HealthKitSourceTests: XCTestCase {
    /// A unit HealthKit rejects for its type silently drops that type from
    /// every sync. This is the only place that would notice.
    func testEveryCatalogUnitFitsItsType() {
        for (identifier, unit) in HealthKitSource.quantityCatalog {
            XCTAssertTrue(HKQuantityType(identifier).is(compatibleWith: unit),
                          "\(identifier.rawValue) cannot be read in \(unit.unitString)")
        }
        let source = HealthKitSource()
        XCTAssertEqual(source.streams.count,
                       HealthKitSource.quantityCatalog.count + HealthKitSource.categoryCatalog.count + 1)
        XCTAssertEqual(source.streams.last, HealthKitSource.workoutStream)
    }

    func testTheCatalogHasNoDuplicates() {
        let quantities = HealthKitSource.quantityCatalog.map(\.0.rawValue)
        XCTAssertEqual(Set(quantities).count, quantities.count)
        let categories = HealthKitSource.categoryCatalog.map(\.rawValue)
        XCTAssertEqual(Set(categories).count, categories.count)
    }

    func testSleepAndExerciseAreInTheCatalog() {
        let source = HealthKitSource()
        for stream in ["HKCategoryTypeIdentifierSleepAnalysis", "HKQuantityTypeIdentifierAppleExerciseTime",
                       "HKQuantityTypeIdentifierStepCount", "HKWorkoutType"] {
            XCTAssertTrue(source.streams.contains(stream), stream)
        }
    }

    func testWorkoutNamesAreHealthKitsOwnCamelCase() {
        XCTAssertEqual(HealthKitSource.name(of: .highIntensityIntervalTraining), "highIntensityIntervalTraining")
        XCTAssertEqual(HealthKitSource.name(of: .walking), "walking")
    }
}
