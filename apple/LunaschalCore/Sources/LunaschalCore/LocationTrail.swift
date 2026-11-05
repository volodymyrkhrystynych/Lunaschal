import Foundation

// The background location trail, without importing CoreLocation: the app's
// LocationTracker turns CLLocation/CLVisit/CMMotionActivity into the plain
// values below, and everything that decides *which* fixes to keep, *when* to
// switch the GPS on, and *when a point counts as sent* lives here, where it can
// be tested on Linux. Server side: backend/location/.
//
// Two recording modes, chosen by `TrailPolicy`:
// - **coarse**: iOS visits and significant-change wakes. Close to free, runs
//   all day, and relaunches the app after it was killed. A point every few
//   hundred metres at best.
// - **fine**: live GPS, switched on only while walking, running or cycling, and
//   off again when that stops. That is where the battery goes, and where the
//   real routes come from.

public struct TrailPoint: Codable, Equatable {
    /// `region` is a geofence stay at one of the user's saved places (Home or
    /// a Work, RegionPlan in Places.swift): like a visit, its exit arrives
    /// later as the same id with `departed` set.
    public enum Kind: String, Codable { case fine, coarse, visit, region }
    /// Minted here; the server upserts on it, so a resend stores nothing twice.
    public let id: String
    /// Unix seconds of the fix (a visit's arrival).
    public let ts: Double
    public let latitude: Double
    public let longitude: Double
    public let accuracy: Double?
    public let altitude: Double?
    public let speed: Double?
    public let kind: Kind
    /// A visit's or region stay's departure; nil while still there.
    public let departed: Double?
    /// The saved place a region stay was at.
    public let placeId: String?
    /// What Core Motion said the user was doing when the fix was taken. The
    /// server reads it to tell a commute by bike from one by car.
    public let activity: TrailPolicy.Activity?

    public init(id: String, ts: Double, latitude: Double, longitude: Double, accuracy: Double? = nil,
                altitude: Double? = nil, speed: Double? = nil, kind: Kind, departed: Double? = nil,
                placeId: String? = nil, activity: TrailPolicy.Activity? = nil) {
        self.id = id; self.ts = ts; self.latitude = latitude; self.longitude = longitude
        self.accuracy = accuracy; self.altitude = altitude; self.speed = speed; self.kind = kind
        self.departed = departed; self.placeId = placeId
        // `unknown` says nothing the server could use.
        self.activity = activity == .unknown ? nil : activity
    }

    /// iOS reports a visit twice -- on arrival, and again on departure with the
    /// same arrival time. One id for both makes the second an update.
    public static func visitID(arrival: Double) -> String { "visit-\(Int(arrival))" }
}

/// Which fixes are worth keeping. Stateful: standing still with the GPS on
/// produces a fix every second or two, scattered by its own accuracy, and
/// every one of them would otherwise be drawn as a scribble.
public struct TrailFilter {
    /// Past these, a fix says less than the map's own detail.
    public static let maxAccuracy: [TrailPoint.Kind: Double] = [.fine: 75, .coarse: 1000, .visit: 1000, .region: 5000]
    /// A fine fix closer than this to the last one kept adds nothing.
    public static let minStep: Double = 10
    /// ...unless this long has passed, so a long pause still leaves a mark.
    public static let keepAliveInterval: Double = 120

    private var lastFine: TrailPoint?

    public init() {}

    public mutating func admit(_ point: TrailPoint) -> Bool {
        if let accuracy = point.accuracy {
            // CoreLocation's negative accuracy means "invalid fix".
            guard accuracy >= 0, accuracy <= Self.maxAccuracy[point.kind] ?? 0 else { return false }
        }
        guard point.kind == .fine else { return true }
        if let last = lastFine, point.ts - last.ts < Self.keepAliveInterval {
            let step = max(Self.minStep, (point.accuracy ?? 0) / 2)
            guard Self.distance(last, point) >= step else { return false }
        }
        lastFine = point
        return true
    }

    /// Haversine, in metres.
    public static func distance(_ a: TrailPoint, _ b: TrailPoint) -> Double {
        let r = 6_371_000.0, rad = Double.pi / 180
        let p1 = a.latitude * rad, p2 = b.latitude * rad
        let dp = p2 - p1, dl = (b.longitude - a.longitude) * rad
        let h = sin(dp / 2) * sin(dp / 2) + cos(p1) * cos(p2) * sin(dl / 2) * sin(dl / 2)
        return 2 * r * asin(min(1, h.squareRoot()))
    }
}

/// When to have the GPS on. Pure: the tracker feeds it what Core Motion and
/// CoreLocation said, and applies the answer.
public enum TrailPolicy {
    public enum Mode: String, Codable { case coarse, fine }
    public enum Activity: String, Codable { case stationary, walking, running, cycling, automotive, unknown }

    public struct Motion: Equatable {
        public let activity: Activity
        /// Core Motion's medium or high confidence.
        public let confident: Bool
        public let at: Date
        public init(_ activity: Activity, confident: Bool = true, at: Date) {
            self.activity = activity; self.confident = confident; self.at = at
        }
    }

    /// Stationary this long ends a fine stretch. Short enough that a coffee
    /// stop doesn't burn GPS, long enough that a traffic light doesn't end a walk.
    public static let settle: TimeInterval = 3 * 60
    /// With no motion data at all, the speeds that are someone on foot or a bike.
    public static let selfPropelled: ClosedRange<Double> = 0.7...8

    /// `motion` is what Core Motion has reported, in any order. It reports an
    /// activity once, when it *starts*, so the newest confident sample is the
    /// current activity however old it is: a walk that began half an hour ago
    /// has one sample, half an hour old, and is still a walk.
    public static func next(current: Mode, motion: [Motion], speed: Double?, now: Date) -> Mode {
        let confident = motion.filter(\.confident).sorted { $0.at < $1.at }
        guard let latest = confident.last else {
            // No Core Motion (denied, or nothing yet): judge by speed, and only
            // to switch on. Switching off is iOS's auto-pause, which the
            // tracker hears as didPauseLocationUpdates.
            if let speed, selfPropelled.contains(speed) { return .fine }
            return current
        }
        switch latest.activity {
        case .walking, .running, .cycling:
            return .fine
        case .automotive:
            return .coarse
        case .stationary:
            guard current == .fine else { return .coarse }
            // Stationary since the start of the unbroken stationary run.
            let since = confident.reversed().prefix { $0.activity == .stationary }.last?.at ?? latest.at
            return now.timeIntervalSince(since) < settle ? .fine : .coarse
        case .unknown:
            return current
        }
    }
}

/// Points waiting for the server, oldest first, as JSON lines. Appending is a
/// write to the end; acknowledging a batch rewrites the file without its head.
public final class TrailOutbox {
    public struct Status: Codable, Equatable {
        public var lastSuccess: Date?
        public var lastAttempt: Date?
        public var lastError: String?
        /// Points the server has acknowledged, over the life of this install.
        public var sent: Int = 0
        /// Points recorded, over the life of this install.
        public var recorded: Int = 0
        public init() {}
    }

    public let root: URL
    private let fm = FileManager.default
    private var file: URL { root.appendingPathComponent("outbox.jsonl") }
    private var statusURL: URL { root.appendingPathComponent("status.json") }

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func append(_ points: [TrailPoint]) throws {
        guard !points.isEmpty else { return }
        let encoder = JSONEncoder()
        var data = Data()
        for point in points {
            data.append(try encoder.encode(point))
            data.append(0x0A)
        }
        if let handle = try? FileHandle(forWritingTo: file) {
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } else {
            try data.write(to: file, options: .atomic)
        }
        var status = self.status()
        status.recorded += points.count
        try save(status)
    }

    private func lines() -> [Data] {
        guard let data = try? Data(contentsOf: file) else { return [] }
        return data.split(separator: 0x0A, omittingEmptySubsequences: true).map { Data($0) }
    }

    /// The oldest `limit` points. A line that doesn't decode (a write cut off
    /// by the app being killed) is skipped rather than blocking the queue.
    public func peek(_ limit: Int) -> (points: [TrailPoint], lines: Int) {
        let head = lines().prefix(limit)
        let decoder = JSONDecoder()
        return (head.compactMap { try? decoder.decode(TrailPoint.self, from: $0) }, head.count)
    }

    public var count: Int { lines().count }

    /// Forget the oldest `n` lines, once the server has them.
    public func drop(_ n: Int) throws {
        let rest = lines().dropFirst(n)
        if rest.isEmpty {
            if fm.fileExists(atPath: file.path) { try fm.removeItem(at: file) }
            return
        }
        var data = Data()
        for line in rest { data.append(line); data.append(0x0A) }
        try data.write(to: file, options: .atomic)
    }

    public func status() -> Status {
        (try? JSONDecoder().decode(Status.self, from: Data(contentsOf: statusURL))) ?? Status()
    }

    public func save(_ status: Status) throws {
        try JSONEncoder().encode(status).write(to: statusURL, options: .atomic)
    }
}

/// What `/api/location/points` answers.
public struct TrailAck: Codable, Equatable {
    public let points: Int
    public let rejectedCount: Int
    public init(points: Int, rejectedCount: Int) { self.points = points; self.rejectedCount = rejectedCount }
}

public protocol TrailTransport {
    func sendTrail(_ points: [TrailPoint]) async throws -> TrailAck
}

public enum TrailSyncError: LocalizedError, Equatable {
    case unaccounted
    public var errorDescription: String? {
        "The server's reply to a location upload did not match what was sent. Nothing was marked as uploaded."
    }
}

/// Drains the outbox. Points leave it only once the server has accounted for
/// every one of them, so a pass cut off anywhere resends at most one batch --
/// which the server's upsert absorbs.
@MainActor
public final class TrailSync {
    public static let batchLimit = 1000
    private let outbox: TrailOutbox
    private let now: () -> Date

    public init(outbox: TrailOutbox, now: @escaping () -> Date = Date.init) {
        self.outbox = outbox; self.now = now
    }

    public func run(using transport: TrailTransport) async throws {
        var status = outbox.status()
        status.lastAttempt = now()
        try outbox.save(status)
        do {
            while true {
                try Task.checkCancellation()
                let (points, lines) = outbox.peek(Self.batchLimit)
                if lines == 0 { break }
                if !points.isEmpty {
                    let ack = try await transport.sendTrail(points)
                    guard ack.points + ack.rejectedCount == points.count else { throw TrailSyncError.unaccounted }
                    status.sent += ack.points
                }
                try outbox.drop(lines)
                try outbox.save(status)
            }
            status.lastSuccess = now()
            status.lastError = nil
            try outbox.save(status)
        } catch {
            if !(Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled) {
                status.lastError = error.localizedDescription
            }
            try? outbox.save(status)
            throw error
        }
    }

    /// Worth asking iOS for a background pass: anything waiting at all.
    public static func isDue(_ outbox: TrailOutbox) -> Bool { outbox.count > 0 }
}

extension JournalAPI: TrailTransport {
    public func sendTrail(_ points: [TrailPoint]) async throws -> TrailAck {
        struct Body: Encodable { let points: [TrailPoint] }
        let data = try await postJSON("api/location/points", Body(points: points))
        return try JSONDecoder().decode(TrailAck.self, from: data)
    }
}
