import Foundation
import HealthKit
import LunaschalCore

/// `HealthSource` over the phone's HealthKit store. The Watch writes into the
/// same store (it syncs to the phone on its own), so reading here is how Watch
/// sleep, workouts and exercise minutes reach the server.
///
/// "Everything readable" is a hand list rather than an enumeration: HealthKit
/// offers no list of its identifiers, and each quantity needs a unit to read
/// it in. Units are typed constructors, never `HKUnit(from:)` strings -- a bad
/// string raises an Objective-C exception, which Swift cannot catch -- and each
/// is still checked against its type before use, so a unit that turns out wrong
/// drops that one type instead of the app. Left out on purpose: clinical
/// records, ECGs, audiograms and heartbeat series need separate entitlements or
/// queries and are not samples in this shape.
final class HealthKitSource: HealthSource {
    static var isAvailable: Bool { HKHealthStore.isHealthDataAvailable() }

    static let workoutStream = "HKWorkoutType"

    private let store = HKHealthStore()
    private let quantities: [String: (HKQuantityType, HKUnit)]
    private let categories: [String: HKCategoryType]
    private let calendar: Calendar

    init(calendar: Calendar = .current) {
        self.calendar = calendar
        var quantities: [String: (HKQuantityType, HKUnit)] = [:]
        for (identifier, unit) in Self.quantityCatalog {
            let type = HKQuantityType(identifier)
            if type.is(compatibleWith: unit) { quantities[identifier.rawValue] = (type, unit) }
        }
        self.quantities = quantities
        categories = Dictionary(uniqueKeysWithValues: Self.categoryCatalog.map {
            ($0.rawValue, HKCategoryType($0))
        })
    }

    var streams: [String] { quantities.keys.sorted() + categories.keys.sorted() + [Self.workoutStream] }

    private var readTypes: Set<HKObjectType> {
        var types = Set<HKObjectType>(quantities.values.map(\.0))
        types.formUnion(categories.values.map { $0 as HKObjectType })
        types.insert(HKWorkoutType.workoutType())
        return types
    }

    /// Shows the Health permission sheet the first time. HealthKit never says
    /// whether reading was allowed -- a refused type just reads as empty -- so
    /// success here means only that the question was asked.
    func requestAuthorization() async throws {
        try await store.requestAuthorization(toShare: [], read: readTypes)
    }

    // MARK: Anchored pages

    func page(stream: String, anchor: Data?, limit: Int) async throws -> HealthPage {
        let type: HKSampleType
        if stream == Self.workoutStream { type = HKWorkoutType.workoutType() }
        else if let quantity = quantities[stream] { type = quantity.0 }
        else if let category = categories[stream] { type = category }
        else { return HealthPage(anchor: anchor) }

        let previous = try anchor.flatMap {
            try NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0)
        }
        let (added, deleted, next) = try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<([HKSample], [HKDeletedObject], HKQueryAnchor?), Error>) in
            let query = HKAnchoredObjectQuery(type: type, predicate: nil, anchor: previous, limit: limit) {
                _, added, deleted, next, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume(returning: (added ?? [], deleted ?? [], next)) }
            }
            store.execute(query)
        }
        var page = HealthPage(
            deleted: deleted.map(\.uuid.uuidString),
            anchor: try next.map { try NSKeyedArchiver.archivedData(withRootObject: $0, requiringSecureCoding: true) })
        for sample in added {
            if let workout = sample as? HKWorkout {
                page.workouts.append(record(workout))
            } else if let record = record(sample, stream: stream) {
                page.samples.append(record)
            }
        }
        return page
    }

    private func record(_ sample: HKSample, stream: String) -> HealthSampleRecord? {
        let common = (uuid: sample.uuid.uuidString, start: sample.startDate.timeIntervalSince1970,
                      end: sample.endDate.timeIntervalSince1970)
        if let quantity = sample as? HKQuantitySample, let unit = quantities[stream]?.1 {
            // A series sample for a type whose unit the catalog has wrong would
            // raise; the compatibility check at init already removed those.
            return HealthSampleRecord(
                uuid: common.uuid, type: stream, kind: .quantity, start: common.start, end: common.end,
                value: quantity.quantity.doubleValue(for: unit), unit: unit.unitString,
                source: sample.sourceRevision.source.name,
                sourceBundle: sample.sourceRevision.source.bundleIdentifier,
                device: Self.describe(sample.device), metadata: Self.flatten(sample.metadata))
        }
        if let category = sample as? HKCategorySample {
            return HealthSampleRecord(
                uuid: common.uuid, type: stream, kind: .category, start: common.start, end: common.end,
                value: Double(category.value), unit: nil,
                source: sample.sourceRevision.source.name,
                sourceBundle: sample.sourceRevision.source.bundleIdentifier,
                device: Self.describe(sample.device), metadata: Self.flatten(sample.metadata))
        }
        return nil
    }

    private func record(_ workout: HKWorkout) -> HealthWorkoutRecord {
        let energy = workout.statistics(for: HKQuantityType(.activeEnergyBurned))?
            .sumQuantity()?.doubleValue(for: .kilocalorie())
        // Whichever distance the activity recorded: walking/running, cycling,
        // swimming, or one of the newer sports.
        let distanceTypes: [HKQuantityTypeIdentifier] = [
            .distanceWalkingRunning, .distanceCycling, .distanceSwimming, .distanceWheelchair,
            .distanceDownhillSnowSports, .distanceRowing, .distancePaddleSports,
            .distanceCrossCountrySkiing, .distanceSkatingSports,
        ]
        let distance = distanceTypes.lazy.compactMap {
            workout.statistics(for: HKQuantityType($0))?.sumQuantity()?.doubleValue(for: .meter())
        }.first
        return HealthWorkoutRecord(
            uuid: workout.uuid.uuidString, activityType: Int(workout.workoutActivityType.rawValue),
            activityName: Self.name(of: workout.workoutActivityType),
            start: workout.startDate.timeIntervalSince1970, end: workout.endDate.timeIntervalSince1970,
            duration: workout.duration, energy: energy, distance: distance,
            source: workout.sourceRevision.source.name,
            sourceBundle: workout.sourceRevision.source.bundleIdentifier,
            metadata: Self.flatten(workout.metadata))
    }

    // MARK: Daily totals

    /// One statistics query per cumulative type, bucketed on the app's 4am
    /// day. HealthKit's sum merges overlapping sources (the phone and the Watch
    /// both counting one walk); summing raw samples on the server cannot.
    func dailyTotals(first: String, last: String) async throws -> [HealthDailyTotal] {
        guard let start = dayStart(first), let lastStart = dayStart(last),
              let end = calendar.date(byAdding: .day, value: 1, to: lastStart) else { return [] }
        var totals: [HealthDailyTotal] = []
        for (identifier, (type, unit)) in quantities.sorted(by: { $0.key < $1.key })
        where type.aggregationStyle == .cumulative {
            try Task.checkCancellation()
            let collection = try await withCheckedThrowingContinuation {
                (continuation: CheckedContinuation<HKStatisticsCollection?, Error>) in
                let query = HKStatisticsCollectionQuery(
                    quantityType: type,
                    quantitySamplePredicate: HKQuery.predicateForSamples(withStart: start, end: end),
                    options: .cumulativeSum, anchorDate: start, intervalComponents: DateComponents(day: 1))
                query.initialResultsHandler = { _, collection, error in
                    if let error {
                        // No data and no permission read the same; neither is a failure.
                        if (error as? HKError)?.code == .errorNoData { continuation.resume(returning: nil) }
                        else { continuation.resume(throwing: error) }
                    } else { continuation.resume(returning: collection) }
                }
                store.execute(query)
            }
            collection?.enumerateStatistics(from: start, to: end) { statistics, _ in
                guard let sum = statistics.sumQuantity() else { return }
                totals.append(HealthDailyTotal(
                    date: DayKey.of(statistics.startDate, calendar: self.calendar), type: identifier,
                    value: sum.doubleValue(for: unit), unit: unit.unitString))
            }
        }
        return totals
    }

    /// 04:00 local on a day key's calendar date.
    private func dayStart(_ key: String) -> Date? {
        let parts = key.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2],
                                                  hour: DayKey.rolloverHour))
    }

    // MARK: Helpers

    private static func describe(_ device: HKDevice?) -> String? {
        guard let device else { return nil }
        let parts = [device.name, device.model, device.hardwareVersion, device.softwareVersion].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// HealthKit metadata values are strings, numbers, dates or quantities.
    /// Strings all round keeps the JSON flat for analysis.
    private static func flatten(_ metadata: [String: Any]?) -> [String: String]? {
        guard let metadata, !metadata.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        return metadata.mapValues { value in
            switch value {
            case let string as String: return string
            case let number as NSNumber: return number.stringValue
            case let date as Date: return iso.string(from: date)
            default: return String(describing: value)
            }
        }
    }

    static func name(of type: HKWorkoutActivityType) -> String {
        switch type {
        case .walking: return "walking"
        case .running: return "running"
        case .cycling: return "cycling"
        case .hiking: return "hiking"
        case .swimming: return "swimming"
        case .traditionalStrengthTraining: return "traditionalStrengthTraining"
        case .functionalStrengthTraining: return "functionalStrengthTraining"
        case .highIntensityIntervalTraining: return "highIntensityIntervalTraining"
        case .coreTraining: return "coreTraining"
        case .crossTraining: return "crossTraining"
        case .mixedCardio: return "mixedCardio"
        case .elliptical: return "elliptical"
        case .rowing: return "rowing"
        case .stairClimbing: return "stairClimbing"
        case .stairs: return "stairs"
        case .stepTraining: return "stepTraining"
        case .yoga: return "yoga"
        case .pilates: return "pilates"
        case .flexibility: return "flexibility"
        case .cooldown: return "cooldown"
        case .dance: return "dance"
        case .cardioDance: return "cardioDance"
        case .mindAndBody: return "mindAndBody"
        case .martialArts: return "martialArts"
        case .boxing: return "boxing"
        case .kickboxing: return "kickboxing"
        case .climbing: return "climbing"
        case .tennis: return "tennis"
        case .tableTennis: return "tableTennis"
        case .badminton: return "badminton"
        case .basketball: return "basketball"
        case .soccer: return "soccer"
        case .volleyball: return "volleyball"
        case .hockey: return "hockey"
        case .skatingSports: return "skatingSports"
        case .downhillSkiing: return "downhillSkiing"
        case .crossCountrySkiing: return "crossCountrySkiing"
        case .snowboarding: return "snowboarding"
        case .paddleSports: return "paddleSports"
        case .surfingSports: return "surfingSports"
        case .waterSports: return "waterSports"
        case .golf: return "golf"
        case .play: return "play"
        case .fitnessGaming: return "fitnessGaming"
        case .wheelchairWalkPace: return "wheelchairWalkPace"
        case .wheelchairRunPace: return "wheelchairRunPace"
        case .handCycling: return "handCycling"
        case .other: return "other"
        default: return "activity\(type.rawValue)"
        }
    }

    // MARK: Catalog

    private static let percent = HKUnit.percent()
    private static let bpm = HKUnit.count().unitDivided(by: .minute())
    private static let speed = HKUnit.meter().unitDivided(by: .second())
    private static let dBA = HKUnit.decibelAWeightedSoundPressureLevel()

    static let quantityCatalog: [(HKQuantityTypeIdentifier, HKUnit)] = [
        // Activity
        (.stepCount, .count()), (.distanceWalkingRunning, .meter()), (.distanceCycling, .meter()),
        (.distanceWheelchair, .meter()), (.distanceSwimming, .meter()), (.distanceDownhillSnowSports, .meter()),
        (.distanceRowing, .meter()), (.distancePaddleSports, .meter()), (.distanceCrossCountrySkiing, .meter()),
        (.distanceSkatingSports, .meter()),
        (.basalEnergyBurned, .kilocalorie()), (.activeEnergyBurned, .kilocalorie()),
        (.flightsClimbed, .count()), (.appleExerciseTime, .minute()), (.appleMoveTime, .minute()),
        (.appleStandTime, .minute()), (.pushCount, .count()), (.swimmingStrokeCount, .count()),
        (.nikeFuel, .count()), (.timeInDaylight, .minute()),
        (.vo2Max, HKUnit.literUnit(with: .milli).unitDivided(by: HKUnit.gramUnit(with: .kilo).unitMultiplied(by: .minute()))),
        (.physicalEffort, HKUnit.kilocalorie().unitDivided(by: HKUnit.gramUnit(with: .kilo).unitMultiplied(by: .hour()))),
        (.workoutEffortScore, .appleEffortScore()), (.estimatedWorkoutEffortScore, .appleEffortScore()),
        // Mobility and running/cycling form
        (.walkingSpeed, speed), (.walkingStepLength, .meter()), (.walkingDoubleSupportPercentage, percent),
        (.walkingAsymmetryPercentage, percent), (.appleWalkingSteadiness, percent),
        (.sixMinuteWalkTestDistance, .meter()), (.stairAscentSpeed, speed), (.stairDescentSpeed, speed),
        (.runningSpeed, speed), (.runningPower, .watt()), (.runningStrideLength, .meter()),
        (.runningVerticalOscillation, .meter()), (.runningGroundContactTime, .secondUnit(with: .milli)),
        (.cyclingSpeed, speed), (.cyclingPower, .watt()), (.cyclingFunctionalThresholdPower, .watt()),
        (.cyclingCadence, bpm), (.rowingSpeed, speed), (.paddleSportsSpeed, speed),
        (.crossCountrySkiingSpeed, speed),
        // Heart and vitals
        (.heartRate, bpm), (.restingHeartRate, bpm), (.walkingHeartRateAverage, bpm),
        (.heartRateVariabilitySDNN, .secondUnit(with: .milli)), (.heartRateRecoveryOneMinute, bpm),
        (.atrialFibrillationBurden, percent), (.oxygenSaturation, percent), (.respiratoryRate, bpm),
        (.bodyTemperature, .degreeCelsius()), (.basalBodyTemperature, .degreeCelsius()),
        (.appleSleepingWristTemperature, .degreeCelsius()), (.appleSleepingBreathingDisturbances, .count()),
        (.bloodPressureSystolic, .millimeterOfMercury()), (.bloodPressureDiastolic, .millimeterOfMercury()),
        (.bloodGlucose, HKUnit.gramUnit(with: .milli).unitDivided(by: .literUnit(with: .deci))),
        (.peripheralPerfusionIndex, percent), (.electrodermalActivity, .siemen()),
        (.forcedVitalCapacity, .liter()), (.forcedExpiratoryVolume1, .liter()),
        (.peakExpiratoryFlowRate, HKUnit.liter().unitDivided(by: .minute())),
        (.inhalerUsage, .count()), (.insulinDelivery, .internationalUnit()),
        (.numberOfTimesFallen, .count()), (.bloodAlcoholContent, percent),
        (.numberOfAlcoholicBeverages, .count()),
        // Body
        (.bodyMass, .gramUnit(with: .kilo)), (.leanBodyMass, .gramUnit(with: .kilo)),
        (.bodyMassIndex, .count()), (.bodyFatPercentage, percent), (.height, .meter()),
        (.waistCircumference, .meter()),
        // Environment
        (.environmentalAudioExposure, dBA), (.headphoneAudioExposure, dBA),
        (.environmentalSoundReduction, dBA), (.uvExposure, .count()),
        (.underwaterDepth, .meter()), (.waterTemperature, .degreeCelsius()),
        // Nutrition
        (.dietaryEnergyConsumed, .kilocalorie()), (.dietaryProtein, .gram()),
        (.dietaryCarbohydrates, .gram()), (.dietaryFatTotal, .gram()), (.dietaryFatSaturated, .gram()),
        (.dietarySugar, .gram()), (.dietaryFiber, .gram()), (.dietaryCholesterol, .gramUnit(with: .milli)),
        (.dietarySodium, .gramUnit(with: .milli)), (.dietaryPotassium, .gramUnit(with: .milli)),
        (.dietaryCalcium, .gramUnit(with: .milli)), (.dietaryIron, .gramUnit(with: .milli)),
        (.dietaryVitaminC, .gramUnit(with: .milli)), (.dietaryVitaminD, .gramUnit(with: .micro)),
        (.dietaryCaffeine, .gramUnit(with: .milli)), (.dietaryWater, .literUnit(with: .milli)),
    ]

    static let categoryCatalog: [HKCategoryTypeIdentifier] = [
        .sleepAnalysis, .appleStandHour, .mindfulSession,
        .highHeartRateEvent, .lowHeartRateEvent, .irregularHeartRhythmEvent, .lowCardioFitnessEvent,
        .appleWalkingSteadinessEvent, .environmentalAudioExposureEvent, .headphoneAudioExposureEvent,
        .sleepApneaEvent, .handwashingEvent, .toothbrushingEvent,
        .menstrualFlow, .intermenstrualBleeding, .sexualActivity, .ovulationTestResult,
        .cervicalMucusQuality, .pregnancy, .lactation, .contraceptive,
        .headache, .fatigue, .nausea, .dizziness, .fever, .coughing, .soreThroat, .shortnessOfBreath,
        .rapidPoundingOrFlutteringHeartbeat, .chestTightnessOrPain, .moodChanges, .sleepChanges,
        .appetiteChanges, .lowerBackPain, .generalizedBodyAche,
    ]
}
