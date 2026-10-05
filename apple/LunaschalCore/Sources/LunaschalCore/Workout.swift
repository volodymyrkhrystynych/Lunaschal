import Foundation

/// One set or activity as typed into the Workout page, the way the desktop's
/// workout log takes it: "bicep curls 20, 10" (pounds, reps), "squats 10"
/// (bodyweight), bare "20, 10" for the selected exercise, "walking 30"
/// (minutes). A port of `backend/lifestyle/quick_entry.py`'s `parse_entry`,
/// checked on the phone so nothing the server would refuse is queued. Folding
/// "curls" onto "bicep curl" stays on the server, which knows the names in use.
public struct WorkoutEntry: Equatable {
    public enum Kind: String, Codable { case strength, outdoor }

    public let name: String
    public let kind: Kind
    public let weight: Double?
    public let reps: Int?
    public let minutes: Int?

    static let outdoor = ["walk": "walking", "walking": "walking", "cycle": "cycling", "cycling": "cycling",
                          "bike": "cycling", "biking": "cycling", "on the bike": "cycling"]

    public enum Problem: LocalizedError, Equatable {
        case empty, noNumbers, negative, noName, outdoorMinutes, badShape, outOfRange

        // The server's wording, so the phone and the desktop say the same thing.
        public var errorDescription: String? {
            switch self {
            case .empty: return "Enter one exercise and its reps or duration."
            case .noNumbers: return "Add reps, weight and reps, or minutes."
            case .negative: return "Use a positive rep count or duration."
            case .noName: return "Name an exercise or select a recent exercise first."
            case .outdoorMinutes: return "Walking and cycling take one duration in minutes (1–1440)."
            case .badShape: return "Use weight, reps (20, 10) or bodyweight reps (10)."
            case .outOfRange: return "Reps must be 1–10000 and weight 0–10000 lb."
            }
        }
    }

    public static func parse(_ text: String, selected: String? = nil) throws -> WorkoutEntry {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, text.count <= 500 else { throw Problem.empty }
        guard let split = match(#"^([^\d]*?)(\d[\s\S]*)$"#, trimmed) else { throw Problem.noNumbers }
        let label = split[1], numbers = split[2]
        let labelEnd = label.trimmingCharacters(in: .whitespaces)
        if labelEnd.hasSuffix("-") || labelEnd.hasSuffix("+") || labelEnd.hasSuffix(".") { throw Problem.negative }
        let given = label.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let picked = given.isEmpty ? selected : given,
              !picked.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw Problem.noName }
        let name = picked.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if let activity = outdoor[name] {
            guard let duration = match(#"^(\d+)\s*(?:m|min|mins|minute|minutes)?$"#, numbers, caseless: true),
                  let minutes = Int(duration[1]), (1...1440).contains(minutes) else { throw Problem.outdoorMinutes }
            return WorkoutEntry(name: activity, kind: .outdoor, weight: nil, reps: nil, minutes: minutes)
        }
        let pair = match(#"^(\d+(?:\.\d+)?)\s*(?:lb|lbs|pounds)?\s*[,x×]\s*(\d+)\s*(?:reps?)?$"#, numbers, caseless: true)
        let single = match(#"^(\d+)\s*(?:reps?)?$"#, numbers, caseless: true)
        guard pair != nil || single != nil else { throw Problem.badShape }
        let weight = pair.flatMap { Double($0[1]) }
        guard let reps = Int(pair?[2] ?? single![1]), (1...10000).contains(reps),
              weight.map({ (0...10000).contains($0) }) ?? true else { throw Problem.outOfRange }
        return WorkoutEntry(name: name, kind: .strength, weight: weight, reps: reps, minutes: nil)
    }

    private static func match(_ pattern: String, _ text: String, caseless: Bool = false) -> [String]? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: caseless ? [.caseInsensitive] : []),
              let found = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else { return nil }
        return (0..<found.numberOfRanges).map { index in
            Range(found.range(at: index), in: text).map { String(text[$0]) } ?? ""
        }
    }
}

/// A set or activity waiting to upload, or one the server refused.
public struct WorkoutLog: Codable, Equatable, Identifiable {
    public enum State: String, Codable { case pending, failed, synced }

    public let id: String
    public let text: String
    /// The exercise a bare "20, 10" means; sent so the server reads it the same way.
    public let exercise: String?
    public let createdAt: Date
    public var state: State
    public var lastError: String?
}

/// The Workout page's outbox, one JSON manifest per entry, uploaded in order so
/// the server groups sets into workouts exactly as they were done.
public final class WorkoutStore {
    public let root: URL
    private let fm = FileManager.default

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [WorkoutLog] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(WorkoutLog.self, from: Data(contentsOf: $0)) }
            .sorted { $0.createdAt < $1.createdAt }
    }

    /// Checks the line, then queues it. Returns what it was read as.
    @discardableResult
    public func log(_ text: String, selected: String?, now: Date = Date()) throws -> (WorkoutLog, WorkoutEntry) {
        let entry = try WorkoutEntry.parse(text, selected: selected)
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Only a bare "20, 10" leans on the selection; a named line stands alone.
        let bare = trimmed.first?.isNumber == true
        let item = WorkoutLog(id: ULID.make(now: now), text: trimmed, exercise: bare ? entry.name : nil,
                              createdAt: now, state: .pending, lastError: nil)
        try save(item)
        return (item, entry)
    }

    public func save(_ item: WorkoutLog) throws {
        guard ULID.isValid(item.id) else { throw CaptureError.invalidID }
        try JSONEncoder().encode(item).write(to: manifest(item.id), options: .atomic)
    }

    public func remove(_ item: WorkoutLog) throws {
        let url = manifest(item.id)
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }
}

/// A recent workout as `GET /api/lifestyle/workouts` returns it.
public struct WorkoutSession: Codable, Equatable, Identifiable {
    public struct Exercise: Codable, Equatable, Identifiable {
        public struct Set: Codable, Equatable {
            public let weight: Double?
            public let reps: Int?
            public init(weight: Double?, reps: Int?) { self.weight = weight; self.reps = reps }
        }
        public let id: String
        public let displayName: String
        public let sets: [Set]
    }

    public let id: String
    public let date: String
    public let locationType: String
    public let captureKind: String?
    public let durationMinutes: Int?
    public let intensityRating: Int?
    public let exercises: [Exercise]
}

public struct RecentExercise: Codable, Equatable, Identifiable {
    public let name: String
    public let displayName: String
    public var id: String { name }
    public init(name: String, displayName: String) { self.name = name; self.displayName = displayName }
}

/// The desktop's labels and meanings (src/lib/lifestyle.ts); keep in step.
public enum WorkoutLabels {
    public static let locations: [(id: String, label: String)] = [
        ("goodlife_brother", "Goodlife with brother"), ("goodlife_alone", "Goodlife alone"),
        ("building", "Building workout room"), ("lifting_home", "Lifting at home"), ("outside", "Outside"),
    ]
    public static func location(_ id: String) -> String {
        locations.first { $0.id == id }?.label ?? "Location not set"
    }
    public static let intensity = [1: "Not intense whatsoever", 2: "Just a smidge", 3: "I'm sweating",
                                   4: "I'm really trying hard", 5: "I am going ham"]

    /// The pills: recent exercises, then walking and cycling if they aren't among them.
    public static func pills(_ recent: [RecentExercise]) -> [RecentExercise] {
        var pills = recent
        for (name, label) in [("walking", "Walking"), ("cycling", "Cycling")] where !pills.contains(where: { $0.name == name }) {
            pills.append(RecentExercise(name: name, displayName: label))
        }
        return pills
    }

    /// "60×8 ×2  65×6", "10 × 4 bodyweight" — `formatSets` in src/lib/lifestyle.ts.
    public static func sets(_ sets: [WorkoutSession.Exercise.Set]) -> String {
        var groups: [(set: WorkoutSession.Exercise.Set, count: Int)] = []
        for set in sets {
            if let last = groups.last, last.set == set { groups[groups.count - 1].count += 1 }
            else { groups.append((set, 1)) }
        }
        return groups.map { group in
            let reps = group.set.reps.map(String.init) ?? "?"
            guard let weight = group.set.weight else {
                return group.count > 1 ? "\(reps) × \(group.count) bodyweight" : "\(reps) bodyweight"
            }
            // JavaScript prints 60 as "60" and 22.5 as "22.5".
            let pounds = weight == weight.rounded() ? String(Int(weight)) : String(weight)
            return "\(pounds)×\(reps)" + (group.count > 1 ? " ×\(group.count)" : "")
        }.joined(separator: "  ")
    }
}

public protocol WorkoutTransport {
    func sendWorkout(_ item: WorkoutLog) async throws
}

/// Uploads the Workout outbox oldest first. A line the server refuses is marked
/// failed and the rest carry on; anything else stops the pass, keeping order.
@MainActor
public final class WorkoutSync {
    private let store: WorkoutStore

    public init(store: WorkoutStore) { self.store = store }

    public func run(using transport: WorkoutTransport) async throws {
        for var item in try store.list() where item.state != .failed {
            if item.state == .synced { try store.remove(item); continue }
            try Task.checkCancellation()
            do {
                try await transport.sendWorkout(item)
                // Nothing to keep once it is in a workout on the server.
                try store.remove(item)
            } catch {
                if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { throw error }
                item.lastError = error.localizedDescription
                if let http = error as? HTTPFailure, ![401, 403].contains(http.status), !http.retryAutomatically {
                    item.state = .failed
                    try store.save(item)
                    continue
                }
                try store.save(item)
                throw error
            }
        }
    }
}
