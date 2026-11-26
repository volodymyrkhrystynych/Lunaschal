import Foundation

/// A workout's recorded route out of Health -- usually the Watch's own GPS, a
/// fix a second, recorded whether or not the phone came along -- turned into
/// trail points (LocationTrail.swift). HealthSync reads routes as their own
/// stream and appends them to the trail outbox instead of the Health batch: on
/// the server a route is location, drawn on the day's map like any walk.
public struct WorkoutRoute: Equatable {
    public struct Fix: Equatable {
        public let ts: Double
        public let latitude: Double
        public let longitude: Double
        public let accuracy: Double?
        public let altitude: Double?
        public let speed: Double?
        public init(ts: Double, latitude: Double, longitude: Double, accuracy: Double? = nil,
                    altitude: Double? = nil, speed: Double? = nil) {
            self.ts = ts; self.latitude = latitude; self.longitude = longitude
            self.accuracy = accuracy; self.altitude = altitude; self.speed = speed
        }
    }

    /// HealthKit's UUID for the route sample.
    public let uuid: String
    /// In time order. Sorted here, because each point's id is its position.
    public let fixes: [Fix]
    /// What the workout was, as Core Motion would have called it.
    public let activity: TrailPolicy.Activity?

    public init(uuid: String, fixes: [Fix], activity: TrailPolicy.Activity? = nil) {
        self.uuid = uuid
        self.fixes = fixes.enumerated().sorted { ($0.element.ts, $0.offset) < ($1.element.ts, $1.offset) }.map(\.element)
        self.activity = activity
    }

    /// The route's UUID and the fix's position in it: the same route read
    /// twice (a resend, an anchor reset) produces the same ids, and the server
    /// upserts on them. 3 + 36 + 1 + a few digits fits the server's 64.
    public static func pointID(route uuid: String, index: Int) -> String { "wr-\(uuid)-\(index)" }

    /// The route as `fine` points, thinned by the same filter as the phone's
    /// own GPS: a fix a second for an hour's run is 3,600 points, nearly all of
    /// them a metre or two from the one before.
    public var points: [TrailPoint] {
        var filter = TrailFilter()
        return fixes.enumerated().compactMap { index, fix in
            let point = TrailPoint(id: Self.pointID(route: uuid, index: index), ts: fix.ts,
                                   latitude: fix.latitude, longitude: fix.longitude, accuracy: fix.accuracy,
                                   altitude: fix.altitude, speed: fix.speed.flatMap { $0 >= 0 ? $0 : nil },
                                   kind: .fine, activity: activity(at: fix))
            return filter.admit(point) ? point : nil
        }
    }

    /// The workout's activity, unless the fix is too fast for it: a walk left
    /// running on the train home is not a walk, and says nothing about what it is.
    private func activity(at fix: Fix) -> TrailPolicy.Activity? {
        if activity == .walking || activity == .running,
           let speed = fix.speed, speed > TrailPolicy.selfPropelled.upperBound { return nil }
        return activity
    }

    /// A workout's activity (HealthKitSource's names) as one the server's
    /// commute reading knows. Anything else says nothing about how you moved.
    public static func activity(forWorkout name: String) -> TrailPolicy.Activity? {
        switch name {
        case "walking", "hiking": return .walking
        case "running": return .running
        case "cycling", "handCycling": return .cycling
        default: return nil
        }
    }
}
