import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Saved places and the Home/Work roles over them (backend/place_roles.py).
// A role is a saved place being Home or a Work for a half-open range of 4am
// days, [startedOn, endedOn); endedOn nil is current. Moving is a new place
// plus a new Home period, so the old one stays as history. Fetched rather than
// replicated, and kept on disk so the Places screen and the geofences work
// offline.

public struct SavedPlace: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public var name: String
    public var notes: String
    public var latitude: Double?
    public var longitude: Double?
    public var radiusM: Int

    public init(id: String, name: String, notes: String = "", latitude: Double?, longitude: Double?, radiusM: Int = 150) {
        self.id = id; self.name = name; self.notes = notes
        self.latitude = latitude; self.longitude = longitude; self.radiusM = radiusM
    }

    public var isLocated: Bool { latitude != nil && longitude != nil }
}

public enum PlaceRole: String, Codable, CaseIterable, Sendable {
    case home, work
    public var label: String { self == .home ? "Home" : "Work" }
}

public struct PlaceRolePeriod: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let role: PlaceRole
    public let placeId: String
    public let placeName: String
    public let latitude: Double
    public let longitude: Double
    public let radiusM: Int
    public let startedOn: String
    public let endedOn: String?

    public init(id: String, role: PlaceRole, placeId: String, placeName: String, latitude: Double, longitude: Double,
                radiusM: Int = 150, startedOn: String, endedOn: String? = nil) {
        self.id = id; self.role = role; self.placeId = placeId; self.placeName = placeName
        self.latitude = latitude; self.longitude = longitude; self.radiusM = radiusM
        self.startedOn = startedOn; self.endedOn = endedOn
    }

    /// Day keys compare as strings, which is what makes this a one-liner.
    public func isActive(on day: String) -> Bool { startedOn <= day && (endedOn.map { day < $0 } ?? true) }

    /// "since 2026-03-01", or "2025-01-01 → 2026-03-01".
    public var range: String { endedOn.map { "\(startedOn) → \($0)" } ?? "since \(startedOn)" }
}

public struct PlacesSnapshot: Codable, Equatable, Sendable {
    public var places: [SavedPlace]
    public var periods: [PlaceRolePeriod]
    public init(places: [SavedPlace] = [], periods: [PlaceRolePeriod] = []) {
        self.places = places; self.periods = periods
    }

    /// The role a place holds on `day`: at most one, by the server's rule.
    public func role(of placeID: String, on day: String) -> PlaceRolePeriod? {
        periods.first { $0.placeId == placeID && $0.isActive(on: day) }
    }

    /// One role's periods, newest start first: the History list.
    public func history(_ role: PlaceRole) -> [PlaceRolePeriod] {
        periods.filter { $0.role == role }.sorted { $0.startedOn > $1.startedOn }
    }

    public func home(on day: String) -> PlaceRolePeriod? {
        periods.first { $0.role == .home && $0.isActive(on: day) }
    }

    public func works(on day: String) -> [PlaceRolePeriod] {
        periods.filter { $0.role == .work && $0.isActive(on: day) }
    }
}

/// The places snapshot on disk.
public struct PlacesCache {
    public let url: URL
    public init(url: URL) { self.url = url }
    public func load() -> PlacesSnapshot {
        (try? JSONDecoder().decode(PlacesSnapshot.self, from: Data(contentsOf: url))) ?? PlacesSnapshot()
    }
    public func save(_ snapshot: PlacesSnapshot) {
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? JSONEncoder().encode(snapshot).write(to: url, options: .atomic)
    }
}

// MARK: Geofences

/// One circle for the phone to watch. The trail already records visits, but
/// iOS reports those late and sometimes merges them; a geofence on Home and
/// each Work gives the exact minute of arriving and leaving, which is what
/// keeps the calendar's workday true without anyone editing it.
public struct MonitoredRegion: Equatable, Codable, Sendable {
    public let placeID: String
    public let name: String
    public let role: PlaceRole
    public let latitude: Double
    public let longitude: Double
    public let radius: Double
}

public enum RegionPlan {
    /// iOS's ceiling on monitored regions per app.
    public static let limit = 20
    /// Below this iOS cannot reliably tell inside from outside; a smaller
    /// saved radius is widened for the geofence only.
    public static let minRadius: Double = 100
    /// The `CLMonitor` the Home and Work geofences live in, reopened by name on
    /// every launch. It must pass `isValidMonitorName`: CoreLocation asserts on
    /// anything else, and "lunaschal-places" crashed every launch that had
    /// Always permission, background relaunches included.
    public static let monitorName = "lunaschalPlaces"

    /// CoreLocation's rule for a monitor name ("Monitor Name contains
    /// non-alphanumeric character" / "cannot begin with _"), checked here
    /// because breaking it is an NSAssertion in `CLMonitor.init`, not an error.
    public static func isValidMonitorName(_ name: String) -> Bool {
        !name.isEmpty && name.unicodeScalars.allSatisfy {
            $0.isASCII && CharacterSet.alphanumerics.contains($0)
        }
    }

    /// The current Home and every current Work.
    public static func regions(_ snapshot: PlacesSnapshot, today: String) -> [MonitoredRegion] {
        let active = ([snapshot.home(on: today)].compactMap { $0 }) + snapshot.works(on: today)
        return active.prefix(limit).map {
            MonitoredRegion(placeID: $0.placeId, name: $0.placeName, role: $0.role,
                            latitude: $0.latitude, longitude: $0.longitude,
                            radius: max(minRadius, Double($0.radiusM)))
        }
    }
}

/// The open geofence stays, kept across launches: an exit can arrive after
/// iOS relaunched the app for it, and the exit must update the same trail
/// point the entry made (same id, now with a departure).
public struct RegionLedger: Codable, Equatable {
    public var open: [String: Double] = [:]
    public init() {}

    public static func pointID(place: String, entered: Double) -> String {
        // The server's id rule is [A-Za-z0-9_-]{8,64}; a ULID place id is 26.
        "region-\(Int(entered))-\(place.prefix(40))"
    }

    /// An entry at `at`. A second entry while one is open (iOS repeating
    /// itself after a relaunch) keeps the original arrival.
    public mutating func enter(_ region: MonitoredRegion, at: Double) -> TrailPoint {
        let entered = open[region.placeID] ?? at
        open[region.placeID] = entered
        return point(region, entered: entered, departed: nil)
    }

    /// An exit, or nil when there was no entry to close (iOS reports the
    /// starting state as an exit for every region the phone is outside).
    public mutating func exit(_ region: MonitoredRegion, at: Double) -> TrailPoint? {
        guard let entered = open.removeValue(forKey: region.placeID) else { return nil }
        return point(region, entered: entered, departed: max(at, entered))
    }

    /// Regions no longer monitored lose their open stay without an exit: the
    /// phone never saw one, and inventing a departure would be a guess.
    public mutating func keepOnly(_ regions: [MonitoredRegion]) {
        let ids = Set(regions.map(\.placeID))
        open = open.filter { ids.contains($0.key) }
    }

    private func point(_ region: MonitoredRegion, entered: Double, departed: Double?) -> TrailPoint {
        TrailPoint(id: Self.pointID(place: region.placeID, entered: entered), ts: entered,
                   latitude: region.latitude, longitude: region.longitude, kind: .region,
                   departed: departed, placeId: region.placeID)
    }
}

// MARK: The Journal's day card

public struct DayWhereabouts: Codable, Equatable, Sendable {
    public struct Home: Codable, Equatable, Sendable {
        public let seconds: Int
        public let lastArrival: String?
        public let here: Bool
    }
    public struct Work: Codable, Equatable, Sendable {
        public let name: String
        public let seconds: Int
        public let firstArrival: String?
        public let lastDeparture: String?
    }
    public let home: Home?
    public let work: [Work]
}

public struct Commute: Codable, Equatable, Sendable {
    public let direction: String  // "toWork" | "toHome"
    public let from: String
    public let to: String
    public let departed: String?
    public let arrived: String?
    public let seconds: Int
    public let meters: Int
    public let mode: String       // walking | running | cycling | automotive
    public let modeSource: String // "motion" | "speed"
}

/// `/api/location/journal`: one card per day the phone recorded a trail.
public struct LocationDay: Codable, Equatable, Identifiable, Sendable {
    public let date: String
    public let archivedAt: String
    public let pointCount: Int
    public let placeCount: Int
    public let entryCount: Int
    public let imageUrl: String
    public let whereabouts: DayWhereabouts?
    public let commutes: [Commute]?
    public var id: String { date }

    public init(date: String, archivedAt: String, pointCount: Int, placeCount: Int, entryCount: Int,
                imageUrl: String, whereabouts: DayWhereabouts? = nil, commutes: [Commute]? = nil) {
        self.date = date; self.archivedAt = archivedAt; self.pointCount = pointCount; self.placeCount = placeCount
        self.entryCount = entryCount; self.imageUrl = imageUrl; self.whereabouts = whereabouts; self.commutes = commutes
    }
}

public enum Whereabouts {
    /// "7h 20m", "45m", "3h".
    public static func duration(_ seconds: Int) -> String {
        let minutes = Int((Double(seconds) / 60).rounded())
        let h = minutes / 60, m = minutes % 60
        if h == 0 { return "\(m)m" }
        return m == 0 ? "\(h)h" : "\(h)h \(m)m"
    }

    static func clock(_ iso: String, in zone: TimeZone) -> String? {
        guard let date = JournalTimestamp.parse(iso) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }

    /// The web card's line: each Work by time there, then home — "home from
    /// 18:40" while still there, otherwise the time spent.
    public static func line(_ w: DayWhereabouts?, zone: TimeZone = .current) -> String? {
        guard let w else { return nil }
        var parts = w.work.filter { $0.seconds >= 60 }.map { "\(duration($0.seconds)) at \($0.name)" }
        if let home = w.home {
            if home.here, let arrival = home.lastArrival, let at = clock(arrival, in: zone) {
                parts.append("home from \(at)")
            } else if home.seconds >= 60 {
                parts.append("\(duration(home.seconds)) at home")
            }
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    public static func modeLabel(_ mode: String) -> String {
        switch mode {
        case "walking": return "on foot"
        case "running": return "running"
        case "cycling": return "by bike"
        default: return "by car or transit"
        }
    }

    public static func modeSymbol(_ mode: String) -> String {
        switch mode {
        case "walking": return "figure.walk"
        case "running": return "figure.run"
        case "cycling": return "bicycle"
        default: return "car"
        }
    }

    /// "30m to Office by bike (2.1 km)".
    public static func commuteLine(_ c: Commute) -> String {
        let km = Double(c.meters) / 1000
        let distance = km < 10 ? String(format: "%.1f km", km) : String(format: "%.0f km", km)
        let target = c.direction == "toWork" ? "to \(c.to)" : "home"
        return "\(duration(c.seconds)) \(target) \(modeLabel(c.mode)) (\(distance))"
    }
}

// MARK: Weekly patterns

/// `/api/location/patterns`.
public struct LocationPatterns: Codable, Equatable, Sendable {
    public struct CommuteStats: Codable, Equatable, Sendable {
        public struct Direction: Codable, Equatable, Sendable {
            public let count: Int
            public let avgSeconds: Int?
        }
        public let toWork: Direction
        public let toHome: Direction
        public let modes: [String: Int]
    }
    public struct Week: Codable, Equatable, Identifiable, Sendable {
        public let weekStart: String
        public let observedDays: Int
        public let officeDays: Int
        public let workSeconds: Int
        public let homeSeconds: Int
        public let avgArrival: String?
        public let avgDeparture: String?
        public let commute: CommuteStats
        public var id: String { weekStart }
    }
    public struct Weekday: Codable, Equatable, Identifiable, Sendable {
        public let weekday: String
        public let observedDays: Int
        public let officeDays: Int
        public var id: String { weekday }
    }
    public let weeks: [Week]
    public let weekdays: [Weekday]
    public let commute: CommuteStats
    public let observedDays: Int
    public let officeDays: Int
    public let days: Int
    public let since: String
}

// MARK: Transport

public protocol PlacesTransport {
    func places() async throws -> PlacesSnapshot
    func savePlace(_ place: SavedPlace, isNew: Bool) async throws -> SavedPlace
    func deletePlace(_ id: String) async throws
    func assignRole(_ role: PlaceRole, placeID: String, startedOn: String) async throws
    func updateRole(_ id: String, startedOn: String?, endedOn: String??) async throws
    func deleteRole(_ id: String) async throws
}

/// What the server said no to, worded by the server ("A place with that name
/// already exists", "Office is already Work for part of that range").
public struct PlacesError: LocalizedError, Equatable {
    public let message: String
    public var errorDescription: String? { message }
}

extension JournalAPI: PlacesTransport {
    public func places() async throws -> PlacesSnapshot {
        async let places = get("api/memory/places", [:])
        async let periods = get("api/memory/places/roles", [:])
        let decoder = JSONDecoder()
        return PlacesSnapshot(places: try decoder.decode([SavedPlace].self, from: await places),
                              periods: try decoder.decode([PlaceRolePeriod].self, from: await periods))
    }

    public func savePlace(_ place: SavedPlace, isNew: Bool) async throws -> SavedPlace {
        var body: [String: Any] = ["name": place.name, "notes": place.notes, "radiusM": place.radiusM]
        body["latitude"] = place.latitude ?? NSNull()
        body["longitude"] = place.longitude ?? NSNull()
        let data = try await placesCall(isNew ? "api/memory/places" : "api/memory/places/\(try Self.placePath(place.id))",
                                        method: isNew ? "POST" : "PUT", body)
        return try JSONDecoder().decode(SavedPlace.self, from: data)
    }

    public func deletePlace(_ id: String) async throws {
        _ = try await placesCall("api/memory/places/\(try Self.placePath(id))", method: "DELETE", nil)
    }

    public func assignRole(_ role: PlaceRole, placeID: String, startedOn: String) async throws {
        _ = try await placesCall("api/memory/places/roles", method: "POST",
                                 ["role": role.rawValue, "placeId": placeID, "startedOn": startedOn])
    }

    /// `endedOn`: nil leaves it, .some(nil) reopens the period.
    public func updateRole(_ id: String, startedOn: String?, endedOn: String??) async throws {
        var body: [String: Any] = [:]
        if let startedOn { body["startedOn"] = startedOn }
        if let endedOn { body["endedOn"] = endedOn ?? NSNull() }
        _ = try await placesCall("api/memory/places/roles/\(try Self.placePath(id))", method: "PATCH", body)
    }

    public func deleteRole(_ id: String) async throws {
        _ = try await placesCall("api/memory/places/roles/\(try Self.placePath(id))", method: "DELETE", nil)
    }

    public func locationDays() async throws -> [LocationDay] {
        try JSONDecoder().decode([LocationDay].self, from: await get("api/location/journal", [:]))
    }

    public func locationPatterns(days: Int = 56) async throws -> LocationPatterns {
        try JSONDecoder().decode(LocationPatterns.self, from: await get("api/location/patterns", ["days": String(days)]))
    }

    /// A day map's picture. The path is the server's own `imageUrl`.
    public func dayMap(_ imageURL: String) async throws -> Data {
        guard imageURL.hasPrefix("/api/location/days/") else { throw CaptureError.invalidResponse }
        var parts = URLComponents(string: imageURL)
        let query = Dictionary(uniqueKeysWithValues: (parts?.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        parts?.queryItems = nil
        return try await get(String((parts?.path ?? imageURL).dropFirst()), query)
    }

    static func placePath(_ id: String) throws -> String {
        guard !id.isEmpty, id.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) else {
            throw CaptureError.invalidID
        }
        return id
    }

    private func placesCall(_ path: String, method: String, _ body: [String: Any]?) async throws -> Data {
        var req = request(path, method: method)
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: req)
        if let http = response as? HTTPURLResponse, (400..<500).contains(http.statusCode),
           let reply = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let message = reply["error"] as? String {
            throw PlacesError(message: message)
        }
        try check(data, response)
        return data
    }
}

// MARK: Date pickers

extension DayKey {
    /// The calendar date a date picker shows, with no 4am shift: picking
    /// "3 March" at 02:00 means the 3rd, not the 2nd.
    public static func picked(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// Noon on a day key's date: what a date picker is set to, safely away
    /// from either midnight.
    public static func date(_ key: String, calendar: Calendar = .current) -> Date? {
        let parts = key.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }
}
