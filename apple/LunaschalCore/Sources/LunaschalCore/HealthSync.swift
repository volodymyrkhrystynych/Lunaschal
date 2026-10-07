import Foundation

// Apple Health → server, without importing HealthKit: the app supplies a
// `HealthSource` backed by HKHealthStore, and everything that decides *what*
// to send and *when it counts as sent* lives here, where it can be tested on
// Linux. Server side: backend/apple_health/.

/// One HealthKit quantity or category sample, as the server stores it.
public struct HealthSampleRecord: Codable, Equatable {
    public enum Kind: String, Codable { case quantity, category }
    public let uuid: String
    public let type: String
    public let kind: Kind
    /// Unix seconds; sub-second heart-rate samples keep their fraction.
    public let start: Double
    public let end: Double
    public let value: Double?
    public let unit: String?
    public let source: String?
    public let sourceBundle: String?
    public let device: String?
    public let metadata: [String: String]?

    public init(uuid: String, type: String, kind: Kind, start: Double, end: Double, value: Double?,
                unit: String?, source: String? = nil, sourceBundle: String? = nil, device: String? = nil,
                metadata: [String: String]? = nil) {
        self.uuid = uuid; self.type = type; self.kind = kind; self.start = start; self.end = end
        self.value = value; self.unit = unit; self.source = source; self.sourceBundle = sourceBundle
        self.device = device; self.metadata = metadata
    }
}

public struct HealthWorkoutRecord: Codable, Equatable {
    public let uuid: String
    public let activityType: Int
    public let activityName: String
    public let start: Double
    public let end: Double
    public let duration: Double
    public let energy: Double?
    public let distance: Double?
    public let source: String?
    public let sourceBundle: String?
    public let metadata: [String: String]?

    public init(uuid: String, activityType: Int, activityName: String, start: Double, end: Double,
                duration: Double, energy: Double? = nil, distance: Double? = nil, source: String? = nil,
                sourceBundle: String? = nil, metadata: [String: String]? = nil) {
        self.uuid = uuid; self.activityType = activityType; self.activityName = activityName
        self.start = start; self.end = end; self.duration = duration; self.energy = energy
        self.distance = distance; self.source = source; self.sourceBundle = sourceBundle; self.metadata = metadata
    }
}

/// A cumulative quantity's total for one 4am day, already de-duplicated across
/// the phone and the Watch by HealthKit's statistics query.
public struct HealthDailyTotal: Codable, Equatable {
    public let date: String
    public let type: String
    public let value: Double
    public let unit: String

    public init(date: String, type: String, value: Double, unit: String) {
        self.date = date; self.type = type; self.value = value; self.unit = unit
    }
}

public struct HealthBatch: Codable, Equatable {
    public var samples: [HealthSampleRecord] = []
    public var workouts: [HealthWorkoutRecord] = []
    public var deleted: [String] = []
    public var daily: [HealthDailyTotal] = []

    public init(samples: [HealthSampleRecord] = [], workouts: [HealthWorkoutRecord] = [],
                deleted: [String] = [], daily: [HealthDailyTotal] = []) {
        self.samples = samples; self.workouts = workouts; self.deleted = deleted; self.daily = daily
    }

    public var count: Int { samples.count + workouts.count + deleted.count + daily.count }
    public var isEmpty: Bool { count == 0 }
}

/// What `/api/apple-health/sync` answers: how many of each it stored, and how
/// many it skipped as malformed.
public struct HealthAck: Codable, Equatable {
    public let samples: Int
    public let workouts: Int
    public let deleted: Int
    public let daily: Int
    public let rejectedCount: Int

    public init(samples: Int, workouts: Int, deleted: Int, daily: Int, rejectedCount: Int) {
        self.samples = samples; self.workouts = workouts; self.deleted = deleted
        self.daily = daily; self.rejectedCount = rejectedCount
    }

    /// Every item sent is accounted for -- stored or rejected. Anything else is
    /// not our server (a captive portal answering 200, a proxy), and the anchor
    /// must not move on its word.
    public func accounts(for batch: HealthBatch) -> Bool {
        samples + workouts + deleted + daily + rejectedCount == batch.count
    }
}

/// One step of an anchored read: new objects, deleted UUIDs, and the anchor to
/// resume from once they are safely on the server.
public struct HealthPage {
    public var samples: [HealthSampleRecord]
    public var workouts: [HealthWorkoutRecord]
    public var deleted: [String]
    public var anchor: Data?

    public init(samples: [HealthSampleRecord] = [], workouts: [HealthWorkoutRecord] = [],
                deleted: [String] = [], anchor: Data?) {
        self.samples = samples; self.workouts = workouts; self.deleted = deleted; self.anchor = anchor
    }

    var batch: HealthBatch { HealthBatch(samples: samples, workouts: workouts, deleted: deleted) }
    var count: Int { samples.count + workouts.count + deleted.count }
}

public protocol HealthSource {
    /// The anchored streams to read, one per HealthKit type identifier.
    var streams: [String] { get }
    /// Up to `limit` objects added or deleted since `anchor` (nil: from the start).
    func page(stream: String, anchor: Data?, limit: Int) async throws -> HealthPage
    /// Daily totals of every cumulative type, for 4am days `first...last`.
    func dailyTotals(first: String, last: String) async throws -> [HealthDailyTotal]
}

public protocol HealthTransport {
    func sendHealth(_ batch: HealthBatch) async throws -> HealthAck
}

public enum HealthSyncError: LocalizedError, Equatable {
    case unaccounted
    public var errorDescription: String? {
        "The server's reply to a Health upload did not match what was sent. Nothing was marked as uploaded."
    }
}

/// Where each stream got to, and what the last pass did. Anchors are
/// HealthKit's own opaque, archived HKQueryAnchor bytes.
public final class HealthStateStore {
    public struct Status: Codable, Equatable {
        /// The last 4am day whose totals were sent.
        public var dailyThrough: String?
        public var lastSuccess: Date?
        public var lastAttempt: Date?
        public var lastError: String?
        /// Items acknowledged by the server, over the life of this install.
        public var sent: Int = 0
        public init() {}
    }

    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root.appendingPathComponent("anchors", isDirectory: true),
                               withIntermediateDirectories: true)
    }

    private func anchorURL(_ stream: String) -> URL {
        // Stream names are HealthKit identifiers -- letters only -- but keep the
        // filename safe whatever a future source calls a stream.
        let safe = stream.map { $0.isLetter || $0.isNumber ? $0 : "_" }
        return root.appendingPathComponent("anchors", isDirectory: true)
            .appendingPathComponent(String(safe)).appendingPathExtension("anchor")
    }

    public func anchor(_ stream: String) -> Data? { try? Data(contentsOf: anchorURL(stream)) }

    public func setAnchor(_ data: Data?, for stream: String) throws {
        if let data { try data.write(to: anchorURL(stream), options: .atomic) }
        else if fm.fileExists(atPath: anchorURL(stream).path) { try fm.removeItem(at: anchorURL(stream)) }
    }

    private var statusURL: URL { root.appendingPathComponent("status.state") }

    public func status() -> Status {
        (try? JSONDecoder().decode(Status.self, from: Data(contentsOf: statusURL))) ?? Status()
    }

    public func save(_ status: Status) throws {
        try JSONEncoder().encode(status).write(to: statusURL, options: .atomic)
    }

    /// Forget everything, so the next pass re-reads all of Health. The server
    /// upserts by UUID, so a full resend duplicates nothing.
    public func reset() throws {
        if fm.fileExists(atPath: root.path) { try fm.removeItem(at: root) }
        try fm.createDirectory(at: root.appendingPathComponent("anchors", isDirectory: true),
                               withIntermediateDirectories: true)
    }
}

/// One Health pass: drain every stream from its anchor, then refresh recent
/// daily totals. An anchor only moves after the server has acknowledged the
/// page it covers, so a pass cut off anywhere -- killed, cancelled, offline --
/// resumes exactly where it stopped and resends at most one page.
@MainActor
public final class HealthSync {
    public static let pageLimit = 2000
    /// The first pass reaches this far back for daily totals; HealthKit's raw
    /// samples go back to the beginning regardless.
    public static let historyDays = 3 * 365
    /// Days re-sent on every pass: the Watch can hand over yesterday's walk
    /// hours after it happened, which changes a total already sent.
    public static let recomputeDays = 3

    private let store: HealthStateStore
    private let now: () -> Date
    private let calendar: Calendar

    public init(store: HealthStateStore, now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.store = store; self.now = now; self.calendar = calendar
    }

    public func run(source: HealthSource, transport: HealthTransport) async throws {
        var status = store.status()
        status.lastAttempt = now()
        try store.save(status)
        do {
            for stream in source.streams {
                try await drain(stream, source: source, transport: transport, status: &status)
            }
            try await sendDaily(source: source, transport: transport, status: &status)
            status.lastSuccess = now()
            status.lastError = nil
            try store.save(status)
        } catch {
            if !(Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled) {
                status.lastError = error.localizedDescription
            }
            try? store.save(status)
            throw error
        }
    }

    private func drain(_ stream: String, source: HealthSource, transport: HealthTransport,
                       status: inout HealthStateStore.Status) async throws {
        var anchor = store.anchor(stream)
        while true {
            try Task.checkCancellation()
            let page = try await source.page(stream: stream, anchor: anchor, limit: Self.pageLimit)
            if page.count > 0 {
                try await send(page.batch, transport: transport, status: &status)
            }
            // A full page that didn't move the anchor would be read again
            // forever; HealthKit always advances it, so this only guards a bug.
            guard page.anchor != anchor || page.count == 0 else {
                if page.count < Self.pageLimit { return }
                throw HealthSyncError.unaccounted
            }
            if page.anchor != anchor {
                try store.setAnchor(page.anchor, for: stream)
                anchor = page.anchor
            }
            // A short page is the end of the stream; a full one may have more.
            if page.count < Self.pageLimit { return }
        }
    }

    private func sendDaily(source: HealthSource, transport: HealthTransport,
                           status: inout HealthStateStore.Status) async throws {
        let today = DayKey.of(now(), calendar: calendar)
        let first: String
        if let through = status.dailyThrough {
            first = Self.day(through, minus: Self.recomputeDays - 1)
        } else {
            first = Self.day(today, minus: Self.historyDays)
        }
        let totals = try await source.dailyTotals(first: min(first, today), last: today)
        var index = 0
        while index < totals.count {
            try Task.checkCancellation()
            let chunk = Array(totals[index..<min(index + Self.pageLimit, totals.count)])
            try await send(HealthBatch(daily: chunk), transport: transport, status: &status)
            index += chunk.count
        }
        status.dailyThrough = today
    }

    private func send(_ batch: HealthBatch, transport: HealthTransport,
                      status: inout HealthStateStore.Status) async throws {
        let ack = try await transport.sendHealth(batch)
        guard ack.accounts(for: batch) else { throw HealthSyncError.unaccounted }
        status.sent += batch.count - ack.rejectedCount
        try store.save(status)
    }

    /// `day` minus `n` days, as a day key. Day keys are calendar dates, so
    /// this is date arithmetic in UTC and never meets a DST change.
    static func day(_ day: String, minus n: Int) -> String {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = TimeZone(identifier: "UTC")!
        let parts = day.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3,
              let date = utc.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2])),
              let earlier = utc.date(byAdding: .day, value: -n, to: date) else { return day }
        let c = utc.dateComponents([.year, .month, .day], from: earlier)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    /// Whether a background pass is worth asking iOS for: Health has no
    /// outbox to look at, so it is due by age.
    public static func isDue(_ status: HealthStateStore.Status, now: Date, every interval: TimeInterval = 3 * 3600) -> Bool {
        guard let last = status.lastSuccess else { return true }
        return now.timeIntervalSince(last) >= interval
    }
}

extension JournalAPI: HealthTransport {
    public func sendHealth(_ batch: HealthBatch) async throws -> HealthAck {
        let data = try await postJSON("api/apple-health/sync", batch)
        return try JSONDecoder().decode(HealthAck.self, from: data)
    }
}
