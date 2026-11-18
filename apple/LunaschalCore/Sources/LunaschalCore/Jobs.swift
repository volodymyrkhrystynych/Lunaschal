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
        "stack_mismatch": "Different stack", "salary_below_preference": "Below your salary floor",
        "soft_preference_missing": "Misses a preference",
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
    /// An undo waiting to be sent brings its card back rather than hiding it.
    public static func hidingDecided(_ jobs: [FeedJob], _ ops: [JobDecisionOp]) -> [FeedJob] {
        let decided = Set(ops.filter { !$0.decision.isUndo }.map(\.jobID))
        return jobs.filter { !decided.contains($0.id) }
    }
}

/// The phone's one-card-at-a-time feed: a drag decides, the last decision can
/// be taken back. Kept here, away from SwiftUI, so the rules are tested on Linux.
public enum JobDeck {
    /// Share of the card's width a drag must travel to decide on release.
    public static let threshold = 0.35
    /// Points per second sideways that decide however short the drag.
    public static let flickSpeed = 900.0

    /// Right queues, left dismisses. Only a mostly-sideways drag decides, so
    /// scrolling a long card up and down can never throw it away.
    public static func decision(dx: Double, dy: Double, velocityX: Double = 0, width: Double) -> JobDecision? {
        guard abs(dx) > abs(dy) * 1.5, abs(dx) > 20 else { return nil }
        let far = abs(dx) >= width * threshold
        let flicked = abs(velocityX) >= flickSpeed && (velocityX > 0) == (dx > 0)
        guard far || flicked else { return nil }
        return dx > 0 ? .queue : .dismiss
    }

    /// The deck in order, "Worth a look" first, as the list groups it.
    public static func cards(_ jobs: [FeedJob], _ ops: [JobDecisionOp]) -> (cards: [FeedJob], promising: Int) {
        let groups = JobFeed.split(JobFeed.hidingDecided(jobs, ops))
        return (groups.promising + groups.rest, groups.promising.count)
    }

    /// What takes `op` back. Always the opposite decision, even when `op` has
    /// not left the phone: queued through `JobStore.decide` it replaces an
    /// unsent op (and the server finds nothing to undo), or follows one the
    /// sync pass is already sending — dropping the op instead would lose that race.
    public static func undo(_ op: JobDecisionOp) -> JobDecision? { op.decision.inverse }
}

/// The posting body's Markdown cut into the blocks the card draws: headings,
/// list items and paragraphs. Inline emphasis and links stay in the text for
/// `AttributedString(markdown:)`, which handles those but not blocks.
public enum PostingBlock: Equatable {
    case heading(String)
    /// `marker` is "•" or "3."; `depth` 0 for a top-level item.
    case item(String, marker: String, depth: Int)
    case paragraph(String)

    public static func parse(_ markdown: String) -> [PostingBlock] {
        var blocks: [PostingBlock] = []
        var paragraph: [String] = []
        func flush() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: " "))) }
            paragraph = []
        }
        for raw in markdown.components(separatedBy: "\n") {
            let indent = raw.prefix(while: { $0 == " " }).count
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { flush(); continue }
            if line.hasPrefix("#") {
                let text = line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
                flush()
                if !text.isEmpty { blocks.append(.heading(text)) }
            } else if line.hasPrefix("- ") || line.hasPrefix("* ") {
                flush()
                blocks.append(.item(String(line.dropFirst(2)), marker: "•", depth: indent / 2))
            } else if let dot = line.firstIndex(of: "."), dot != line.startIndex,
                      line[..<dot].allSatisfy(\.isNumber), line[line.index(after: dot)...].hasPrefix(" ") {
                flush()
                blocks.append(.item(String(line[line.index(dot, offsetBy: 2)...]), marker: String(line[...dot]),
                                    depth: indent / 2))
            } else {
                paragraph.append(line)
            }
        }
        flush()
        return blocks
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
    /// Taking back a queue the server already has: drops the draft application.
    case unqueue
    /// Taking back a dismiss the server already has.
    case undismiss

    public var isUndo: Bool { self == .unqueue || self == .undismiss }

    public var inverse: JobDecision? {
        switch self {
        case .queue: return .unqueue
        case .dismiss: return .undismiss
        case .unqueue, .undismiss: return nil
        }
    }

    /// `POST /api/jobs/<id>/<route>`
    var route: String { self == .undismiss ? "dismiss" : rawValue }

    var verb: String {
        ["queue": "queue", "dismiss": "dismiss", "unqueue": "take back the queue of",
         "undismiss": "bring back"][rawValue]!
    }
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

    /// A posting body once fetched, so the card still reads it offline.
    public func cachedPosting(_ id: String) -> String? {
        guard let url = try? postingFile(id) else { return nil }
        return try? String(contentsOf: url, encoding: .utf8)
    }

    public func savePosting(_ id: String, _ markdown: String) throws {
        let url = try postingFile(id)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try markdown.write(to: url, atomically: true, encoding: .utf8)
    }

    /// Drops the bodies of postings no longer on the feed, so the folder
    /// doesn't grow by one file per posting ever looked at.
    public func prunePostings(keeping ids: Set<String>) {
        let folder = root.appendingPathComponent("postings", isDirectory: true)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        for name in names where !ids.contains((name as NSString).deletingPathExtension) {
            try? FileManager.default.removeItem(at: folder.appendingPathComponent(name))
        }
    }

    private func postingFile(_ id: String) throws -> URL {
        // Ids are ULIDs; anything else must not become a path.
        guard !id.isEmpty, id.allSatisfy({ $0.isLetter || $0.isNumber }) else { throw CaptureError.invalidResponse }
        return root.appendingPathComponent("postings", isDirectory: true).appendingPathComponent("\(id).md")
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
                    refused.append("The server didn't \(op.decision.verb) “\(op.title)”: \(refusal.message)")
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

    static func jobBody(_ decision: JobDecision) -> [String: Any] {
        switch decision {
        case .dismiss: return ["dismissed": true]
        case .undismiss: return ["dismissed": false]
        case .queue, .unqueue: return [:]
        }
    }

    /// The whole posting as Markdown (`descriptionMarkdown`, rebuilt from the
    /// board's HTML); the feed sends only the first 600 plain characters.
    public func jobPosting(id: String) async throws -> String {
        let data = try await get("api/jobs/\(try Self.pathID(id))", [:])
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return object?["descriptionMarkdown"] as? String ?? object?["description"] as? String ?? ""
    }

    /// Every route is safe to repeat: a second queue re-queues the same
    /// application, and a second dismiss changes nothing.
    public func send(_ decision: JobDecision, jobID: String) async throws {
        var req = request("api/jobs/\(try Self.pathID(jobID))/\(decision.route)", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: Self.jobBody(decision))
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
