import Foundation

/// The server owns yesterday/backlog selection and permanent dismissal, shared
/// by every client. Captions and titles are previews, not user context.
public struct JournalContextPrompt: Decodable, Identifiable, Equatable {
    public let id: String
    public let title: String?
    public let createdAt: String?
    public let attachments: [Attachment]

    public struct Attachment: Decodable, Equatable {
        public let id: String
        public let kind: String
        public let name: String
        public let mime: String?
        public let position: Int
        public let transcript: String?
        public let description: String?
    }

    public var displayTitle: String {
        let value = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? "Untitled media entry" : value
    }

    public var media: [JournalAttachmentItem] {
        attachments.map {
            JournalAttachmentItem(id: $0.id, entryID: id, kind: $0.kind, name: $0.name,
                                  mime: $0.mime ?? "", position: $0.position,
                                  transcript: $0.transcript, description: $0.description)
        }.sorted { ($0.position, $0.id) < ($1.position, $1.id) }
    }
}

public enum JournalContextAction: String, Encodable { case save, dismiss }

public struct JournalContextResolution: Encodable {
    public let action: JournalContextAction
    public let content: String?

    public init(action: JournalContextAction, content: String? = nil) {
        self.action = action
        self.content = action == .save ? content?.trimmingCharacters(in: .whitespacesAndNewlines) : nil
    }
}

public protocol JournalContextTransport {
    func journalContextPrompts() async throws -> [JournalContextPrompt]
    func resolveJournalContext(_ id: String, resolution: JournalContextResolution) async throws
}

extension JournalAPI: JournalContextTransport {
    /// The backlog, after today's waiting entries: the phone asks for today's
    /// (Daily lists them), which the web's Chat cards leave out.
    public func journalContextPrompts() async throws -> [JournalContextPrompt] {
        try JSONDecoder().decode([JournalContextPrompt].self,
            from: await postJSON("api/journal/context-prompts/prepare", ["includeToday": true]))
    }

    public func resolveJournalContext(_ id: String, resolution: JournalContextResolution) async throws {
        _ = try await postJSON("api/journal/\(try Self.pathID(id))/context-prompt", resolution)
    }
}

/// Today's half of the context prompts: what the Daily page lists and counts
/// in its badge, while the day is still remembered. The server's list covers
/// what has uploaded; a capture still on this device is judged here, so a
/// photo taken offline asks for its words at once rather than after a sync.
public enum JournalContextToday {
    /// The server lists one of today's entries only once it has sat this long
    /// (`TODAY_SETTLE_SECONDS` in `backend/journal/context_prompts.py`), so a
    /// capture this device has just uploaded is still listed from its own
    /// judgement until then, rather than vanishing for two minutes.
    public static let settleSeconds: TimeInterval = 120

    public enum Item: Identifiable, Equatable {
        /// On the server, answerable now.
        case prompt(JournalContextPrompt)
        /// Still on this device, or just uploaded and not yet listed by the
        /// server; answerable once the server lists it.
        case local(Capture)

        public var id: String {
            switch self {
            case .prompt(let prompt): return prompt.id
            case .local(let capture): return capture.targetID
            }
        }

        /// Uploaded, waiting only for the server to list it: worth asking again.
        public var awaitingServer: Bool {
            if case .local(let capture) = self { return capture.state == .synced }
            return false
        }

        public var createdAt: Date? {
            switch self {
            case .prompt(let prompt): return JournalTimestamp.parse(prompt.createdAt)
            case .local(let capture): return capture.createdAt
            }
        }

        public var photoCount: Int {
            switch self {
            case .prompt(let prompt): return prompt.attachments.filter { $0.kind == "image" }.count
            case .local(let capture): return capture.files.filter(\.isImage).count
            }
        }

        public var clipCount: Int {
            switch self {
            case .prompt(let prompt): return prompt.attachments.filter { $0.kind == "audio" }.count
            case .local(let capture):
                return capture.clips.count + capture.files.filter(JournalContextToday.isAudio).count
                    + (capture.mode == .record ? 1 : 0)
            }
        }
    }

    /// Whether a capture not yet on the server will arrive as an entry with
    /// nothing but photos and wordless clips — the server's eligibility rule
    /// (`backend/journal/context_prompts.py`), judged before upload. A
    /// Transcribe clip is words on the way, so it never counts; if it turns out
    /// silent, the server lists the entry once the transcript is in.
    public static func needsContext(_ capture: Capture) -> Bool {
        (capture.state == .pending || capture.state == .failed) && isWordless(capture)
    }

    /// Just uploaded, wordless, and still inside the server's settle time.
    static func awaitingServer(_ capture: Capture, now: Date) -> Bool {
        capture.state == .synced && now.timeIntervalSince(capture.createdAt) < settleSeconds && isWordless(capture)
    }

    static func isWordless(_ capture: Capture) -> Bool {
        // A Capture-tab Save may yet be filed as a meal or a purchase; the
        // server lists it once it has stayed a journal entry.
        guard capture.kind == .journal, capture.entryID == nil, !capture.asTodo, !capture.autoFile,
              capture.mode != .transcribe,
              capture.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              capture.links.isEmpty,
              capture.clips.allSatisfy({ !$0.transcribe }),
              capture.files.allSatisfy({ $0.isImage || isAudio($0) }) else { return false }
        return !capture.files.isEmpty || !capture.clips.isEmpty || capture.mode == .record
    }

    /// Today's waiting entries, newest first. A server prompt for an entry this
    /// device still holds unsent defers to the device's verdict: the server
    /// may have its photo but not yet the words that go with it.
    public static func items(prompts: [JournalContextPrompt], captures: [Capture], day: String,
                             now: Date = Date(), calendar: Calendar = .current) -> [Item] {
        let unsent = Dictionary(captures.filter { $0.entryID == nil && $0.state != .synced }.map { ($0.id, $0) },
                                uniquingKeysWith: { first, _ in first })
        let served = prompts.filter { prompt in
            guard let at = JournalTimestamp.parse(prompt.createdAt), DayKey.of(at, calendar: calendar) == day
            else { return false }
            return unsent[prompt.id].map(needsContext) ?? true
        }
        let ids = Set(served.map(\.id))
        let local = captures.filter {
            (needsContext($0) || awaitingServer($0, now: now)) && !ids.contains($0.id)
                && DayKey.of($0.createdAt, calendar: calendar) == day
        }
        return (served.map(Item.prompt) + local.map(Item.local))
            .sorted { ($0.createdAt ?? .distantPast, $0.id) > ($1.createdAt ?? .distantPast, $1.id) }
    }

    static func isAudio(_ file: CaptureFile) -> Bool { file.contentType?.hasPrefix("audio/") == true }
}
