import Foundation

/// The app's day runs 04:00 to 04:00 local time, as `backend/day_boundary.py`
/// has it. A weigh-in at 01:30 belongs to the day that started the morning
/// before, so the key is fixed when something is logged, not when it uploads.
public enum DayKey {
    public static let rolloverHour = 4

    public static func of(_ date: Date, calendar: Calendar = .current) -> String {
        let shifted = date.addingTimeInterval(-Double(rolloverHour) * 3600)
        let parts = calendar.dateComponents([.year, .month, .day], from: shifted)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}

/// One thing logged from the Daily tab: the day's selfie, its weigh-in, or a
/// calorie entry. The server keeps one selfie and one weight per day and
/// replaces on re-upload; calorie entries are additive, keyed by `id`.
public struct DailyLog: Codable, Equatable, Identifiable {
    public enum Kind: String, Codable { case selfie, weight, calories }
    public enum State: String, Codable { case pending, failed, synced }

    public let id: String
    public let kind: Kind
    public let day: String
    public let createdAt: Date
    public var weight: Double?
    public var calories: Int?
    public var description: String?
    public var state: State
    public var lastError: String?

    init(kind: Kind, now: Date, calendar: Calendar) {
        id = ULID.make(now: now)
        self.kind = kind
        day = DayKey.of(now, calendar: calendar)
        createdAt = now
        state = .pending
    }
}

public enum DailyError: LocalizedError, Equatable {
    case invalidWeight, invalidCalories, missingDescription, missingImage

    public var errorDescription: String? {
        switch self {
        case .invalidWeight: return "Enter a weight between 1 and 1000."
        case .invalidCalories: return "Enter calories between 0 and 20000."
        case .missingDescription: return "Say what you ate."
        case .missingImage: return "The selfie could not be saved."
        }
    }
}

/// One calorie line typed into a single box, as the desktop's Calories card
/// takes it: "chicken breast and rice, ~600" or "protein shake 180 kcal". A port
/// of `parseCalorieEntry` in `src/lib/lifestyle.ts`; keep the two in step.
public struct CalorieLine: Equatable {
    public let description: String
    public let calories: Int

    // Same expression as the web's CALORIE_LINE: the last number wins, an
    // optional unit may follow it.
    private static let line = try! NSRegularExpression(
        pattern: #"^(.*?)[\s,;:~=-]*(\d{1,5})\s*(?:k?cal|kcal|calories)?\s*$"#,
        options: [.caseInsensitive])
    private static let trailing = try! NSRegularExpression(pattern: #"[\s,;:~=-]+$"#)

    /// Nil when there is no trailing count, or nothing left to call the food.
    public static func parse(_ text: String) -> CalorieLine? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let whole = NSRange(trimmed.startIndex..., in: trimmed)
        guard let match = line.firstMatch(in: trimmed, range: whole),
              let head = Range(match.range(at: 1), in: trimmed),
              let digits = Range(match.range(at: 2), in: trimmed),
              let calories = Int(trimmed[digits]) else { return nil }
        let raw = String(trimmed[head]).trimmingCharacters(in: .whitespacesAndNewlines)
        let description = trailing.stringByReplacingMatches(
            in: raw, range: NSRange(raw.startIndex..., in: raw), withTemplate: "")
        guard !description.isEmpty else { return nil }
        return CalorieLine(description: description, calories: calories)
    }
}

/// The Daily tab's outbox. One JSON manifest per log, the selfie's bytes
/// beside it. Call from one executor (the app uses MainActor).
public final class DailyStore {
    public let root: URL
    private let fm = FileManager.default
    private let calendar: Calendar

    public init(root: URL, calendar: Calendar = .current) throws {
        self.root = root
        self.calendar = calendar
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [DailyLog] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(DailyLog.self, from: Data(contentsOf: $0)) }
            .sorted { $0.createdAt < $1.createdAt }
    }

    public func imageURL(_ log: DailyLog) -> URL { root.appendingPathComponent(log.id).appendingPathExtension("jpg") }

    @discardableResult
    public func logWeight(_ weight: Double, now: Date = Date()) throws -> DailyLog {
        guard weight.isFinite, (1...1000).contains(weight) else { throw DailyError.invalidWeight }
        var log = DailyLog(kind: .weight, now: now, calendar: calendar)
        log.weight = weight
        try supersede(.weight, day: log.day)
        try save(log)
        return log
    }

    @discardableResult
    public func logCalories(_ calories: Int, description: String, now: Date = Date()) throws -> DailyLog {
        guard (0...20000).contains(calories) else { throw DailyError.invalidCalories }
        let text = description.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw DailyError.missingDescription }
        var log = DailyLog(kind: .calories, now: now, calendar: calendar)
        log.calories = calories
        log.description = text
        try save(log)
        return log
    }

    @discardableResult
    public func logSelfie(jpeg: Data, now: Date = Date()) throws -> DailyLog {
        guard !jpeg.isEmpty else { throw DailyError.missingImage }
        let log = DailyLog(kind: .selfie, now: now, calendar: calendar)
        try jpeg.write(to: imageURL(log), options: .atomic)
        try supersede(.selfie, day: log.day)
        try save(log)
        return log
    }

    public func save(_ log: DailyLog) throws {
        guard ULID.isValid(log.id) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(log).write(to: manifest(log.id), options: .atomic)
    }

    /// The server keeps one selfie and one weight a day, so an unsent earlier
    /// one would only be overwritten the moment it arrived. Drop it instead.
    private func supersede(_ kind: DailyLog.Kind, day: String) throws {
        for old in try list() where old.kind == kind && old.day == day && old.state != .synced {
            try remove(old)
        }
    }

    public func remove(_ log: DailyLog) throws {
        for url in [manifest(log.id), imageURL(log)] where fm.fileExists(atPath: url.path) {
            try fm.removeItem(at: url)
        }
    }

    /// Synced logs are only kept for showing the current day offline.
    public func pruneSynced(before day: String) throws {
        for log in try list() where log.state == .synced && log.day < day { try remove(log) }
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }
}

/// What the server has for one day, read from the Lifestyle routes.
public struct DailyStatus: Equatable {
    public struct Selfie: Equatable {
        public let id: String
        public init(id: String) { self.id = id }
    }
    public struct Entry: Equatable, Identifiable {
        public let id: String
        public let description: String
        public let calories: Int
        public init(id: String, description: String, calories: Int) {
            self.id = id; self.description = description; self.calories = calories
        }
    }

    public let day: String
    public var weight: Double?
    public var selfie: Selfie?
    public var entries: [Entry]

    public init(day: String, weight: Double? = nil, selfie: Selfie? = nil, entries: [Entry] = []) {
        self.day = day; self.weight = weight; self.selfie = selfie; self.entries = entries
    }
}

/// The Daily tab's view of a day: the server's record with this device's
/// unsent logs laid over it, or the device's logs alone when offline.
public struct DailySummary: Equatable {
    public struct Entry: Equatable, Identifiable {
        public let id: String
        public let description: String
        public let calories: Int
        public let waiting: Bool
    }

    public let day: String
    public var weight: Double?
    public var weightWaiting = false
    /// A selfie on this device, newest first; takes precedence over the server's.
    public var localSelfie: DailyLog?
    public var serverSelfie: DailyStatus.Selfie?
    public var entries: [Entry] = []
    public var total: Int { entries.reduce(0) { $0 + $1.calories } }
    public var hasSelfie: Bool { localSelfie != nil || serverSelfie != nil }

    public init(day: String, server: DailyStatus?, local: [DailyLog]) {
        self.day = day
        let mine = local.filter { $0.day == day }
        let status = server?.day == day ? server : nil
        weight = status?.weight
        serverSelfie = status?.selfie
        let known = Set(status?.entries.map(\.id) ?? [])
        entries = (status?.entries ?? []).map { Entry(id: $0.id, description: $0.description, calories: $0.calories, waiting: false) }
        for log in mine {
            // With the server's record in hand, a synced log is already in it.
            let waiting = log.state != .synced
            if status != nil && !waiting { continue }
            switch log.kind {
            case .weight:
                weight = log.weight
                weightWaiting = waiting
            case .selfie:
                localSelfie = log
            case .calories where !known.contains(log.id):
                entries.append(Entry(id: log.id, description: log.description ?? "", calories: log.calories ?? 0, waiting: waiting))
            case .calories:
                break
            }
        }
    }
}

public protocol DailyTransport {
    func sendDaily(_ log: DailyLog, image: URL?) async throws
    func dailyStatus(day: String) async throws -> DailyStatus
}

/// Uploads the Daily outbox oldest first. A log the server refuses outright is
/// marked failed and the rest carry on; anything else stops the pass.
@MainActor
public final class DailySync {
    private let store: DailyStore
    private let now: () -> Date

    public init(store: DailyStore, now: @escaping () -> Date = Date.init) {
        self.store = store
        self.now = now
    }

    public func run(using transport: DailyTransport) async throws {
        for var log in try store.list() where log.state == .pending {
            try Task.checkCancellation()
            do {
                try await transport.sendDaily(log, image: log.kind == .selfie ? store.imageURL(log) : nil)
                log.state = .synced
                log.lastError = nil
                try store.save(log)
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
                log.lastError = error.localizedDescription
                if let http = error as? HTTPFailure, ![401, 403].contains(http.status), !http.retryAutomatically {
                    log.state = .failed
                    try store.save(log)
                    continue
                }
                try store.save(log)
                throw error
            }
        }
        try store.pruneSynced(before: DayKey.of(now()))
    }
}
