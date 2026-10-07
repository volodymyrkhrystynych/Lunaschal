import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// The jobs triage feed (the desktop's /api/jobs/feed). Queue and Dismiss are
// kept on the phone until the server has them, and the feed on screen is the
// server's last copy with those cards taken out, so a decision made on a train
// still takes the card away at once.

/// One posting on the feed, as `_feed_item` in backend/routes/jobs.py sends it.
public struct FeedJob: Codable, Equatable, Identifiable {
    public struct Flag: Codable, Equatable {
        public let kind: String
        public let detail: String
        public init(kind: String, detail: String) { self.kind = kind; self.detail = detail }
    }

    public struct MatchReasons: Codable, Equatable {
        public let matched: [String]
        public let missing: [String]
        public init(matched: [String], missing: [String]) { self.matched = matched; self.missing = missing }
    }

    public let id: String
    public var url: String
    public var company: String
    public var title: String
    public var location: String
    public var remote: Bool
    public var salaryMin: Double?
    public var salaryMax: Double?
    public var salaryCurrency: String
    public var description: String
    public var matchReasons: MatchReasons?
    public var postedAt: String?
    public var createdAt: String?
    /// 'strong', 'possible', 'stretch', or '' before the model has read it.
    public var triageFit: String
    /// Two sentences meant to be decided from. Empty until triaged.
    public var triageSummary: String
    public var triageFlags: [Flag]
    /// Nil means the location wasn't recognised, which is not "far".
    public var distanceKm: Double?
    public var distancePrecision: String
    /// What the body says about where the work happens; '' until read.
    public var workLocation: String

    public init(id: String, title: String, company: String, location: String = "", remote: Bool = false,
                salaryMin: Double? = nil, salaryMax: Double? = nil, salaryCurrency: String = "",
                description: String = "", url: String = "", matchReasons: MatchReasons? = nil,
                postedAt: String? = nil, createdAt: String? = nil, triageFit: String = "",
                triageSummary: String = "", triageFlags: [Flag] = [], distanceKm: Double? = nil,
                distancePrecision: String = "", workLocation: String = "") {
        self.id = id; self.title = title; self.company = company; self.location = location
        self.remote = remote; self.salaryMin = salaryMin; self.salaryMax = salaryMax
        self.salaryCurrency = salaryCurrency; self.description = description; self.url = url
        self.matchReasons = matchReasons; self.postedAt = postedAt; self.createdAt = createdAt
        self.triageFit = triageFit; self.triageSummary = triageSummary; self.triageFlags = triageFlags
        self.distanceKm = distanceKm; self.distancePrecision = distancePrecision; self.workLocation = workLocation
    }

    enum CodingKeys: String, CodingKey {
        case id, url, company, title, location, remote, salaryMin, salaryMax, salaryCurrency, description,
             matchReasons, postedAt, createdAt, triageFit, triageSummary, triageFlags, distanceKm,
             distancePrecision, workLocation
    }

    /// Lenient about everything but the id: the server sends SQLite's 0/1 for
    /// `remote`, and a column added later shouldn't break an older phone.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func text(_ key: CodingKeys) -> String { ((try? c.decodeIfPresent(String.self, forKey: key)) ?? nil) ?? "" }
        id = try c.decode(String.self, forKey: .id)
        url = text(.url); company = text(.company); title = text(.title); location = text(.location)
        if let flag = try? c.decodeIfPresent(Bool.self, forKey: .remote) { remote = flag }
        else { remote = ((try? c.decodeIfPresent(Int.self, forKey: .remote)) ?? nil ?? 0) != 0 }
        salaryMin = (try? c.decodeIfPresent(Double.self, forKey: .salaryMin)) ?? nil
        salaryMax = (try? c.decodeIfPresent(Double.self, forKey: .salaryMax)) ?? nil
        salaryCurrency = text(.salaryCurrency); description = text(.description)
        matchReasons = (try? c.decodeIfPresent(MatchReasons.self, forKey: .matchReasons)) ?? nil
        postedAt = (try? c.decodeIfPresent(String.self, forKey: .postedAt)) ?? nil
        createdAt = (try? c.decodeIfPresent(String.self, forKey: .createdAt)) ?? nil
        triageFit = text(.triageFit); triageSummary = text(.triageSummary)
        triageFlags = ((try? c.decodeIfPresent([Flag].self, forKey: .triageFlags)) ?? nil) ?? []
        distanceKm = (try? c.decodeIfPresent(Double.self, forKey: .distanceKm)) ?? nil
        distancePrecision = text(.distancePrecision); workLocation = text(.workLocation)
    }

    /// Share of the asked-for keywords the profile backs, or nil when none were found.
    public var matchPercent: Int? {
        guard let reasons = matchReasons else { return nil }
        let total = reasons.matched.count + reasons.missing.count
        return total == 0 ? nil : Int((Double(reasons.matched.count) / Double(total) * 100).rounded())
    }

    /// "90k–120k CAD", or "" when the posting gave no salary.
    public var salaryText: String {
        func round(_ n: Double) -> String { n >= 1000 ? "\(Int((n / 1000).rounded()))k" : "\(Int(n.rounded()))" }
        let unit = salaryCurrency.isEmpty ? "" : " \(salaryCurrency)"
        switch (salaryMin, salaryMax) {
        case let (min?, max?): return "\(round(min))–\(round(max))\(unit)"
        case let (one?, nil), let (nil, one?): return "\(round(one))\(unit)"
        default: return ""
        }
    }

    private var attendsInPerson: Bool { workLocation == "onsite" || workLocation == "hybrid" }

    /// The commute line, matching `distanceLabel` in src/lib/jobs.ts: remote
    /// is never a number unless the body says the job is really in an office.
    public var distanceText: String? {
        if remote && !attendsInPerson { return "Remote" }
        guard let km = distanceKm else { return remote ? "Remote" : nil }
        let rounded = km < 10 ? String(format: "%g", (km * 10).rounded() / 10) : "\(Int(km.rounded()))"
        let prefix = distancePrecision == "exact" ? "" : "~"
        let lead = remote && attendsInPerson ? "\(workLocation == "hybrid" ? "Hybrid" : "On-site") · " : ""
        return "\(lead)\(prefix)\(rounded) km from \(JobFeed.anchor)"
    }

    /// The fit bucket said the way a person would say it.
    public var fitLabel: String? {
        ["strong": "Worth applying", "possible": "Worth a look", "stretch": "A stretch"][triageFit]
    }
}

public enum JobFeed {
    public static let anchor = "Union Station"

    public static let flagLabels = [
        "seniority_mismatch": "Seniority mismatch", "unpaid": "Unpaid", "commission_only": "Commission only",
        "unclear_role": "Vague role", "contract_only": "Contract only", "onsite_required": "On-site required",
        "security_clearance": "Clearance required", "heavy_travel": "Heavy travel",
        "stack_mismatch": "Different stack",
    ]

    /// The web feed's two groups (`splitFeed`): what the model called strong
    /// or possible, then the rest. A posting not yet triaged falls back to its
    /// keyword score. The server's order is kept inside each.
    public static func split(_ jobs: [FeedJob], threshold: Int = 40) -> (promising: [FeedJob], rest: [FeedJob]) {
        var promising: [FeedJob] = [], rest: [FeedJob] = []
        for job in jobs {
            let worth = job.triageFit.isEmpty ? (job.matchPercent ?? -1) >= threshold
                                              : job.triageFit == "strong" || job.triageFit == "possible"
            if worth { promising.append(job) } else { rest.append(job) }
        }
        return (promising, rest)
    }

    /// The feed as it will be once the queued decisions reach the server.
    public static func hidingDecided(_ jobs: [FeedJob], _ ops: [JobDecisionOp]) -> [FeedJob] {
        let decided = Set(ops.map(\.jobID))
        return jobs.filter { !decided.contains($0.id) }
    }
}

public enum JobSort: String, Codable, CaseIterable, Identifiable {
    case match, distance
    public var id: Self { self }
    public var label: String { self == .match ? "Best match" : "Nearest" }
}

public enum JobDecision: String, Codable {
    /// Build a tailored resume in the background.
    case queue
    case dismiss
}

public struct JobDecisionOp: Codable, Equatable, Identifiable {
    public let id: String
    public let jobID: String
    public let decision: JobDecision
    /// Shown if the server turns the decision down after the card is gone.
    public let title: String
    public let createdAt: Date

    public init(id: String = ULID.make(), jobID: String, decision: JobDecision, title: String,
                createdAt: Date = Date()) {
        self.id = id; self.jobID = jobID; self.decision = decision; self.title = title; self.createdAt = createdAt
    }
}

/// The decisions not yet on the server, oldest first, and the last feed seen
/// so the screen has something to show offline.
public final class JobStore {
    public let root: URL
    private var outboxFile: URL { root.appendingPathComponent("outbox.json") }
    private var feedFile: URL { root.appendingPathComponent("feed.json") }

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func pending() throws -> [JobDecisionOp] {
        guard FileManager.default.fileExists(atPath: outboxFile.path) else { return [] }
        return try JSONDecoder().decode([JobDecisionOp].self, from: Data(contentsOf: outboxFile))
    }

    /// A second decision on a card already decided replaces the first.
    @discardableResult
    public func decide(_ job: FeedJob, _ decision: JobDecision, now: Date = Date()) throws -> JobDecisionOp {
        let op = JobDecisionOp(jobID: job.id, decision: decision, title: job.title, createdAt: now)
        try writeOutbox(try pending().filter { $0.jobID != job.id } + [op])
        return op
    }

    public func remove(_ op: JobDecisionOp) throws {
        try writeOutbox(try pending().filter { $0.id != op.id })
    }

    public func cachedFeed() -> [FeedJob] {
        guard let data = try? Data(contentsOf: feedFile) else { return [] }
        return (try? JSONDecoder().decode([FeedJob].self, from: data)) ?? []
    }

    public func saveFeed(_ jobs: [FeedJob]) throws {
        try JSONEncoder().encode(jobs).write(to: feedFile, options: .atomic)
    }

    private func writeOutbox(_ ops: [JobDecisionOp]) throws {
        try JSONEncoder().encode(ops).write(to: outboxFile, options: .atomic)
    }
}

public protocol JobTransport {
    func send(_ decision: JobDecision, jobID: String) async throws
}

/// The server turned a decision down for good, e.g. the posting was deleted.
public struct JobRefusal: LocalizedError, Equatable {
    public let status: Int
    public let message: String
    public init(status: Int, message: String) { self.status = status; self.message = message }
    public var errorDescription: String? { message }
}

/// Sends the queued decisions in order. A refused one is dropped and
/// reported; anything else (offline, signed out, a server error) stops the
/// pass with the rest still queued.
public final class JobSync {
    private let store: JobStore

    public init(store: JobStore) { self.store = store }

    /// What was refused, worded for the feed.
    public func run(using transport: JobTransport) async throws -> [String] {
        var refused: [String] = []
        for op in try store.pending() {
            try Task.checkCancellation()
            do {
                try await transport.send(op.decision, jobID: op.jobID)
            } catch let refusal as JobRefusal {
                // Dismissing a posting that's already gone is what was wanted.
                if !(refusal.status == 404 && op.decision == .dismiss) {
                    let verb = op.decision == .queue ? "queue" : "dismiss"
                    refused.append("The server didn't \(verb) “\(op.title)”: \(refusal.message)")
                }
            }
            try store.remove(op)
        }
        return refused
    }
}

// MARK: The /api/jobs routes

extension JournalAPI: JobTransport {
    public func jobFeed(sort: JobSort = .match, limit: Int = 100) async throws -> [FeedJob] {
        var query = ["limit": String(limit)]
        if sort == .distance { query["sort"] = "distance" }
        return try JSONDecoder().decode([FeedJob].self, from: await get("api/jobs/feed", query))
    }

    /// Both routes are safe to repeat: a second queue re-queues the same
    /// application, and a second dismiss changes nothing.
    public func send(_ decision: JobDecision, jobID: String) async throws {
        var req = request("api/jobs/\(try Self.pathID(jobID))/\(decision.rawValue)", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: decision == .dismiss ? ["dismissed": true] : [:])
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let failure = HTTPFailure(status: http.statusCode)
            if (400..<500).contains(http.statusCode), ![401, 403].contains(http.statusCode), !failure.retryAutomatically {
                throw JobRefusal(status: http.statusCode, message: Self.errorMessage(data) ?? "HTTP \(http.statusCode)")
            }
            throw failure
        }
    }
}
