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
/// calorie entry or purchase. The server keeps one selfie and one weight per
/// day and replaces on re-upload; calories and spending are additive, keyed by `id`.
public struct DailyLog: Codable, Equatable, Identifiable {
    public enum Kind: String, Codable { case selfie, weight, calories, spending }
    public enum State: String, Codable { case pending, failed, synced }

    public let id: String
    public let kind: Kind
    public let day: String
    public let createdAt: Date
    public var weight: Double?
    public var calories: Int?
    public var description: String?
    public var amountCents: Int?
    public var category: String?
    // Optional so manifests from before deletion support still decode.
    public var deleted: Bool?
    public var isDeletion: Bool { deleted == true }
    public var state: State
    public var lastError: String?

    init(id: String? = nil, kind: Kind, day: String? = nil, now: Date, calendar: Calendar) {
        self.id = id ?? ULID.make(now: now)
        self.kind = kind
        self.day = day ?? DayKey.of(now, calendar: calendar)
        createdAt = now
        state = .pending
    }
}

public enum DailyError: LocalizedError, Equatable {
    case invalidWeight, invalidCalories, missingDescription, missingImage, invalidAmount, invalidCategory, cannotDelete

    public var errorDescription: String? {
        switch self {
        case .invalidWeight: return "Enter a weight between 1 and 1000."
        case .invalidCalories: return "Enter calories between 0 and 20000."
        case .missingDescription: return "Say what you ate."
        case .missingImage: return "The selfie could not be saved."
        case .invalidAmount: return "Enter an amount from $0.01 to $1,000,000.00, with at most two decimal places."
        case .invalidCategory: return "Enter a category of up to 200 characters."
        case .cannotDelete: return "Only calorie and spending entries can be deleted here."
        }
    }
}

/// Money crosses storage and the API as whole CAD cents. Reject extra decimal
/// places instead of rounding a purchase into a different amount.
public enum SpendingAmount {
    public static func cents(_ text: String) -> Int? {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: ".")
        guard value.range(of: #"^[0-9]+(?:\.[0-9]{1,2})?$"#, options: .regularExpression) != nil else { return nil }
        let parts = value.split(separator: ".")
        guard let whole = Int(parts[0]), whole <= 1_000_000 else { return nil }
        let fraction = parts.count == 2 ? Int(parts[1].padding(toLength: 2, withPad: "0", startingAt: 0))! : 0
        let cents = whole * 100 + fraction
        return (1...100_000_000).contains(cents) ? cents : nil
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

    @discardableResult
    public func logSpending(_ amountCents: Int, category: String, now: Date = Date()) throws -> DailyLog {
        guard (1...100_000_000).contains(amountCents) else { throw DailyError.invalidAmount }
        let category = category.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !category.isEmpty, category.unicodeScalars.count <= 200 else { throw DailyError.invalidCategory }
        var log = DailyLog(kind: .spending, now: now, calendar: calendar)
        log.amountCents = amountCents
        log.category = category
        try save(log)
        return log
    }

    /// Keep the deletion on disk, even for an unsynced entry: a previous
    /// upload may have reached the server before its response was lost.
    @discardableResult
    public func delete(id: String, kind: DailyLog.Kind, day: String, now: Date = Date()) throws -> DailyLog {
        guard kind == .calories || kind == .spending else { throw DailyError.cannotDelete }
        var log = try list().first { $0.id == id } ?? DailyLog(id: id, kind: kind, day: day, now: now, calendar: calendar)
        guard log.kind == kind, log.day == day else { throw DailyError.cannotDelete }
        log.deleted = true
        log.state = .pending
        log.lastError = nil
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

    public struct Spending: Codable, Equatable, Identifiable {
        public let id: String
        public let category: String
        public let amountCents: Int
        public init(id: String, category: String, amountCents: Int) {
            self.id = id; self.category = category; self.amountCents = amountCents
        }
    }

    public let day: String
    public var weight: Double?
    public var selfie: Selfie?
    public var entries: [Entry]
    public var spending: [Spending]

    public init(day: String, weight: Double? = nil, selfie: Selfie? = nil, entries: [Entry] = [], spending: [Spending] = []) {
        self.day = day; self.weight = weight; self.selfie = selfie; self.entries = entries
        self.spending = spending
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

    public struct Spending: Equatable, Identifiable {
        public let id: String
        public let category: String
        public let amountCents: Int
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
    public var spending: [Spending] = []
    public var totalCents: Int { spending.reduce(0) { $0 + $1.amountCents } }
    public var hasSelfie: Bool { localSelfie != nil || serverSelfie != nil }

    public init(day: String, server: DailyStatus?, local: [DailyLog]) {
        self.day = day
        let mine = local.filter { $0.day == day }
        let status = server?.day == day ? server : nil
        weight = status?.weight
        serverSelfie = status?.selfie
        let known = Set(status?.entries.map(\.id) ?? [])
        entries = (status?.entries ?? []).map { Entry(id: $0.id, description: $0.description, calories: $0.calories, waiting: false) }
        let knownSpending = Set(status?.spending.map(\.id) ?? [])
        spending = (status?.spending ?? []).map { Spending(id: $0.id, category: $0.category, amountCents: $0.amountCents, waiting: false) }
        for log in mine {
            // Keep hiding a confirmed delete until the server refresh catches
            // up. A refusal restores its row and is also shown in the error list.
            if log.isDeletion {
                if log.state != .failed {
                    entries.removeAll { $0.id == log.id }
                    spending.removeAll { $0.id == log.id }
                }
                continue
            }
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
            case .spending where !knownSpending.contains(log.id):
                spending.append(Spending(id: log.id, category: log.category ?? "", amountCents: log.amountCents ?? 0, waiting: waiting))
            case .spending:
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
        for queued in try store.list() where queued.state == .pending {
            // An earlier upload yields to the UI; the next item might have
            // been deleted or discarded since the snapshot was taken.
            guard var log = try store.list().first(where: { $0.id == queued.id }), log.state == .pending else { continue }
            try Task.checkCancellation()
            do {
                try await transport.sendDaily(log, image: log.kind == .selfie ? store.imageURL(log) : nil)
                guard try store.list().first(where: { $0.id == log.id }) == log else { continue }
                log.state = .synced
                log.lastError = nil
                try store.save(log)
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
                // Never overwrite a deletion made while this request was in flight.
                guard try store.list().first(where: { $0.id == log.id }) == log else { throw error }
                if log.isDeletion, (error as? HTTPFailure)?.status == 404 {
                    log.state = .synced
                    log.lastError = nil
                    try store.save(log)
                    continue
                }
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
