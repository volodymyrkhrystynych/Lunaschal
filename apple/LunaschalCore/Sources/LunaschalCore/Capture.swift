import Foundation

public enum CaptureMode: String, Codable, CaseIterable {
    case text, record, transcribe
}

/// Where a typed capture is filed: the journal, or the food log. Recordings
/// made on their own (the Watch) are always journal entries.
public enum CaptureKind: String, Codable {
    case journal, food
}

public enum CaptureState: String, Codable {
    case recording, interrupted, pending, failed, synced
}

public struct JournalSnapshot: Codable, Equatable {
    public let id: String
    public let content: String
    public let rawContent: String?
    public let title: String?
    public let attachments: [Attachment]?
    /// The server's weather snapshot for the entry, as stored (JSON text).
    public var weather: String? = nil

    public struct Attachment: Codable, Equatable {
        public let id: String
        public let transcriptStatus: String?
        public let transcript: String?
    }
}

/// A YouTube video attached to a captured entry. The attachment id is minted
/// on the device so a re-sent link is recognised by the server as a replay.
public struct CaptureLink: Codable, Equatable {
    public let url: String
    public let attachmentID: String

    public init(url: String, now: Date = Date()) {
        self.url = url
        attachmentID = ULID.make(now: now)
    }
}

/// A photo or file attached to a captured entry. The bytes live in the capture
/// store under `attachmentID`; the name and type are only what the upload says.
public struct CaptureFile: Codable, Equatable, Identifiable {
    public let attachmentID: String
    public let name: String
    public let contentType: String?

    public var id: String { attachmentID }
    public var isImage: Bool { contentType?.hasPrefix("image/") == true }
    /// The food log keeps pictures, videos and voice memos, nothing else.
    public var isFoodMedia: Bool {
        ["image/", "video/", "audio/"].contains { contentType?.hasPrefix($0) == true }
    }

    public init(name: String, contentType: String?, now: Date = Date()) {
        attachmentID = ULID.make(now: now)
        self.name = name
        self.contentType = contentType
    }
}

/// A voice clip recorded into the composer's draft. Transcribe clips have their
/// words appended to the entry by the server; Record clips keep only the audio.
public struct CaptureClip: Codable, Equatable, Identifiable {
    public enum State: String, Codable { case recording, ready, interrupted }

    public let attachmentID: String
    public let transcribe: Bool
    public let createdAt: Date
    public var state: State

    public var id: String { attachmentID }

    public init(transcribe: Bool, now: Date = Date()) {
        attachmentID = ULID.make(now: now)
        self.transcribe = transcribe
        createdAt = now
        state = .recording
    }
}

/// Everything staged in the Capture tab that is not text: it stays here,
/// across launches, until Save entry turns it into a capture.
public struct CaptureDraft: Codable, Equatable {
    public var files: [CaptureFile] = []
    public var clips: [CaptureClip] = []
    /// The iPad notebook drawn for this entry: Notes reopens it, and Save
    /// entry files its pages with everything else. Whether it holds anything
    /// is the notebook's to say, so it is not part of `isEmpty`.
    public var notebookID: String?

    public init() {}
    public var isEmpty: Bool { files.isEmpty && clips.isEmpty }
    func holds(_ attachmentID: String) -> Bool {
        files.contains { $0.attachmentID == attachmentID } || clips.contains { $0.attachmentID == attachmentID }
    }
}

public struct Capture: Codable, Identifiable, Equatable {
    public let id: String
    public let attachmentID: String?
    public let createdAt: Date
    public let mode: CaptureMode
    public let kind: CaptureKind
    /// Changeable only through `CaptureStore.editText`, and only while the
    /// server cannot have the entry yet.
    public internal(set) var text: String
    public let links: [CaptureLink]
    public let files: [CaptureFile]
    public let clips: [CaptureClip]
    public var state: CaptureState
    public var lastError: String?
    public var snapshot: JournalSnapshot?
    /// Where the device was when Save was pressed, if it knew.
    public var latitude: Double?
    public var longitude: Double?
    /// The weather the server looked up for this capture's time and place,
    /// read back after upload (JSON text, as `journal_entries.weather`).
    public var weather: String?
    /// Set when this is reading commentary: the entry is linked to this fic
    /// (and chapter) as the desktop reader's Commentary panel links it. The
    /// chapter is fixed when the capture starts, not when it uploads.
    public let ficID: String?
    public let chapterID: String?
    /// Set when this adds to an entry already on the server (opened from the
    /// Journal and edited): its clips, links and files go up under that
    /// entry, and no entry of its own is created. The capture keeps its own
    /// id, so several additions to one entry are separate uploads.
    public let entryID: String?
    /// Whether a send of this entry may have reached the server. Creating an
    /// entry is replay-safe there (`INSERT OR IGNORE` on the id), so once one
    /// may have landed a re-send carrying new words is silently dropped, and
    /// the words can no longer be edited here. Set before every send; put back
    /// only by a send that failed before connecting. Nil on captures saved
    /// before this was recorded, which a transfer record then decides.
    public internal(set) var mayBeOnServer: Bool?
    /// Entries this one stands in for: a newspaper filed again later in the
    /// day replaces the entry it was filed as before. The server deletes them
    /// once this one lands, and sync holds this one back until they have
    /// landed themselves, so the old one cannot arrive after its replacement.
    public internal(set) var replaces: [String] = []

    /// The entry everything here is filed under.
    public var targetID: String { entryID ?? id }

    /// The weather to show, once the server has looked it up.
    public var entryWeather: EntryWeather? { EntryWeather.parse(weather ?? snapshot?.weather) }

    public var recordingTranscript: JournalSnapshot.Attachment? {
        guard let attachmentID else { return nil }
        return snapshot?.attachments?.first { $0.id == attachmentID }
    }

    /// Whether this capture's words can still be changed on the device: a
    /// typed journal entry, waiting, not being sent, and certainly not on the
    /// server yet. `attempt` is its transfer record, if it has one.
    public func canEditText(attempt: TransferAttempt?) -> Bool {
        guard kind == .journal, mode == .text, entryID == nil, state == .pending,
              attempt?.state != .sending else { return false }
        return mayBeOnServer.map { !$0 } ?? (attempt == nil)
    }

    public func matchesSearch(_ query: String) -> Bool {
        let terms = query.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        guard !terms.isEmpty else { return true }
        let fields = ([self.text, snapshot?.title, snapshot?.content, snapshot?.rawContent]
            + links.map { $0.url } + files.map { $0.name }
            + (snapshot?.attachments?.map { $0.transcript } ?? []))
            .compactMap { $0 }
        return terms.allSatisfy { term in
            fields.contains { $0.localizedCaseInsensitiveContains(term) }
        }
    }

    public init(text: String = "", mode: CaptureMode = .text, kind: CaptureKind = .journal, now: Date = Date(),
                youtubeURLs: [String] = [], files: [CaptureFile] = [], clips: [CaptureClip] = [],
                ficID: String? = nil, chapterID: String? = nil, entryID: String? = nil) {
        id = ULID.make(now: now)
        self.entryID = entryID
        self.ficID = ficID
        self.chapterID = ficID == nil ? nil : chapterID
        attachmentID = mode == .text ? nil : ULID.make(now: now)
        createdAt = now
        self.mode = mode
        self.kind = kind
        self.text = text
        links = youtubeURLs.map { CaptureLink(url: $0, now: now) }
        self.files = files
        self.clips = clips
        state = mode == .text ? .pending : .recording
        mayBeOnServer = false
    }

    private enum CodingKeys: String, CodingKey {
        case id, attachmentID, createdAt, mode, kind, text, links, files, clips, state, lastError, snapshot
        case latitude, longitude, weather, ficID, chapterID, entryID, mayBeOnServer, replaces
    }

    // Manifests written before an entry could hold several links stored one
    // as `youtubeURL` + `linkAttachmentID`; keep its id so a replay still matches.
    private enum LegacyKeys: String, CodingKey { case youtubeURL, linkAttachmentID }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        attachmentID = try c.decodeIfPresent(String.self, forKey: .attachmentID)
        createdAt = try c.decode(Date.self, forKey: .createdAt)
        mode = try c.decode(CaptureMode.self, forKey: .mode)
        // Everything saved before the food log existed was a journal entry.
        kind = try c.decodeIfPresent(CaptureKind.self, forKey: .kind) ?? .journal
        text = try c.decode(String.self, forKey: .text)
        state = try c.decode(CaptureState.self, forKey: .state)
        lastError = try c.decodeIfPresent(String.self, forKey: .lastError)
        snapshot = try c.decodeIfPresent(JournalSnapshot.self, forKey: .snapshot)
        latitude = try c.decodeIfPresent(Double.self, forKey: .latitude)
        longitude = try c.decodeIfPresent(Double.self, forKey: .longitude)
        weather = try c.decodeIfPresent(String.self, forKey: .weather)
        ficID = try c.decodeIfPresent(String.self, forKey: .ficID)
        chapterID = try c.decodeIfPresent(String.self, forKey: .chapterID)
        entryID = try c.decodeIfPresent(String.self, forKey: .entryID)
        mayBeOnServer = try c.decodeIfPresent(Bool.self, forKey: .mayBeOnServer)
        replaces = try c.decodeIfPresent([String].self, forKey: .replaces) ?? []
        files = try c.decodeIfPresent([CaptureFile].self, forKey: .files) ?? []
        clips = try c.decodeIfPresent([CaptureClip].self, forKey: .clips) ?? []
        if let links = try c.decodeIfPresent([CaptureLink].self, forKey: .links) {
            self.links = links
        } else {
            let legacy = try decoder.container(keyedBy: LegacyKeys.self)
            if let url = try legacy.decodeIfPresent(String.self, forKey: .youtubeURL),
               let attachmentID = try legacy.decodeIfPresent(String.self, forKey: .linkAttachmentID) {
                links = [CaptureLink(url: url, attachmentID: attachmentID)]
            } else {
                links = []
            }
        }
    }
}

extension CaptureLink {
    init(url: String, attachmentID: String) {
        self.url = url
        self.attachmentID = attachmentID
    }
}

public enum YouTubeLink {
    /// The first YouTube video linked in `text`, canonical: a shared URL, or a
    /// page title with its link after it, as some apps share.
    public static func find(in text: String) -> String? {
        let tokens = text.split(whereSeparator: { $0.isWhitespace || $0 == "<" || $0 == ">" || $0 == "\"" })
        return tokens.lazy.filter { $0.lowercased().hasPrefix("http") }
            .compactMap { try? canonical(String($0)) }.first
    }

    public static func canonical(_ value: String) throws -> String {
        guard let parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(parts.scheme?.lowercased() ?? ""),
              parts.user == nil, parts.password == nil,
              let host = parts.host?.lowercased(),
              ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"].contains(host) else {
            throw LinkError.invalidURL
        }
        let segments = parts.path.split(separator: "/").map(String.init)
        let candidate: String?
        if host.hasSuffix("youtu.be") { candidate = segments.first }
        else if segments.first == "watch" { candidate = parts.queryItems?.first(where: { $0.name == "v" })?.value }
        else if segments.count >= 2, ["shorts", "embed", "live", "v"].contains(segments[0]) { candidate = segments[1] }
        else { candidate = nil }
        guard let candidate, candidate.count == 11,
              candidate.allSatisfy({ "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-".contains($0) }) else {
            throw LinkError.invalidURL
        }
        return "https://www.youtube.com/watch?v=\(candidate)"
    }
}

public enum LinkError: LocalizedError {
    case invalidURL
    public var errorDescription: String? { "Enter a YouTube video link, such as a watch, Shorts, or youtu.be URL." }
}

public enum ULID {
    private static let alphabet = Array("0123456789ABCDEFGHJKMNPQRSTVWXYZ")

    public static func make(now: Date = Date()) -> String {
        var timestamp = UInt64(max(0, now.timeIntervalSince1970 * 1000))
        var prefix = [Character](repeating: "0", count: 10)
        for index in (0..<10).reversed() {
            prefix[index] = alphabet[Int(timestamp & 31)]
            timestamp >>= 5
        }
        // Sixteen independent five-bit draws supply the ULID's 80 random bits.
        return String(prefix + (0..<16).map { _ in alphabet[Int.random(in: 0..<32)] })
    }

    public static func isValid(_ value: String) -> Bool {
        value.count == 26 && value.first! <= "7" && value.allSatisfy { alphabet.contains($0) }
    }
}

public enum CaptureError: LocalizedError {
    case invalidID, emptyText, nothingToAdd, missingAudio, missingFile, notFoodMedia, stillRecording, invalidServer, differentServer, invalidResponse
    case alreadySent

    public var errorDescription: String? {
        switch self {
        case .invalidID: return "The saved capture has an invalid identifier."
        case .emptyText: return "Write something before saving."
        case .nothingToAdd: return "Record, photograph or attach something before saving."
        case .missingAudio: return "The recording is missing or empty. Its saved entry has been kept."
        case .missingFile: return "The attached file is missing or empty."
        case .notFoodMedia: return "A food entry can hold photos, videos and recordings only. Remove other files first."
        case .stillRecording: return "Stop the recording before saving."
        case .invalidServer: return "Enter an HTTPS server address without a path, credentials, or query."
        case .differentServer: return "This device's captures are bound to a different server."
        case .invalidResponse: return "The server did not confirm this capture. It is still saved on this device."
        case .alreadySent: return "This entry may already be on the server, so it can no longer be edited here. Edit it once it has synced."
        }
    }
}

public enum ServerAddress {
    public static func parse(_ value: String) throws -> URL {
        guard var parts = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/" else { throw CaptureError.invalidServer }
        parts.scheme = "https"
        parts.host = host.lowercased()
        parts.path = ""
        if parts.port == 443 { parts.port = nil }
        guard let url = parts.url else { throw CaptureError.invalidServer }
        return url
    }
}
