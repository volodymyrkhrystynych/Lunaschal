import Foundation

/// Small, append-oriented capture outbox. Each manifest is replaced atomically;
/// recordings are separate files and never loaded into the manifest or pruned.
/// Call from one executor (the app uses MainActor).
public final class CaptureStore {
    public let root: URL
    private let fm = FileManager.default
    /// Each manifest as last decoded, by file name, with the modification date
    /// it had then. `list` runs several times a sync pass and decoded every
    /// capture the device ever made each time; now only a changed file is read.
    /// Keyed by the file rather than invalidated by `save`, because the Watch
    /// receipts write and remove manifests directly.
    private var decoded: [String: (modified: Date, capture: Capture)] = [:]
    private let lock = NSLock()

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
    }

    public func list() throws -> [Capture] {
        lock.lock(); defer { lock.unlock() }
        let files = try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.contentModificationDateKey])
            .filter { $0.pathExtension == "json" && !["server.json", "draft.json"].contains($0.lastPathComponent) }
        var fresh: [String: (modified: Date, capture: Capture)] = [:]
        for file in files {
            let modified = try file.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate ?? .distantPast
            if let known = decoded[file.lastPathComponent], known.modified == modified {
                fresh[file.lastPathComponent] = known
            } else {
                fresh[file.lastPathComponent] = (modified, try JSONDecoder().decode(Capture.self, from: Data(contentsOf: file)))
            }
        }
        decoded = fresh
        return fresh.values.map(\.capture).sorted { $0.createdAt > $1.createdAt }
    }

    /// What the replica says about a synced capture's journal entry.
    public enum EntryOnServer { case present, removed, unknown }

    public static let removedOnServer = "Removed on server. This device still holds its original capture."

    /// Settles synced journal captures against the replica's copy of their
    /// entries, which sync already brings (they were once fetched again, one
    /// request each, every pass). An entry deleted on another device marks its
    /// capture, which is kept and never sent again. An entry that is there
    /// stands in for the capture, which the feed then hides, so after a week's
    /// grace the capture is removed: the file only made every `list` slower.
    /// Kept regardless: meals (the feed shows them from here), anything not
    /// synced, and a Watch recording whose receipt the Watch hasn't confirmed.
    /// Receipt files stay, so a replayed delivery is still recognised.
    /// Returns how many captures it changed.
    @discardableResult
    public func tidySynced(entry: (String) throws -> EntryOnServer, olderThan: TimeInterval = 7 * 86_400,
                           now: Date = Date()) throws -> Int {
        var changed = 0
        for var capture in try list() where capture.state == .synced && capture.kind == .journal {
            switch try entry(capture.snapshot?.id ?? capture.id) {
            case .unknown: continue
            case .removed:
                guard capture.lastError != Self.removedOnServer else { continue }
                capture.lastError = Self.removedOnServer
                try save(capture)
                changed += 1
            case .present:
                let origin = root.appendingPathComponent(capture.id + ".watch-origin").path
                let confirmed = root.appendingPathComponent(capture.id + ".watch-server-confirmed").path
                guard now.timeIntervalSince(capture.createdAt) > olderThan,
                      !fm.fileExists(atPath: origin) || fm.fileExists(atPath: confirmed) else { continue }
                if capture.attachmentID != nil { try? fm.removeItem(at: audioURL(capture)) }
                for file in capture.files { try? fm.removeItem(at: fileURL(file)) }
                for clip in capture.clips { try? fm.removeItem(at: clipURL(clip)) }
                try fm.removeItem(at: manifest(capture.id))
                changed += 1
            }
        }
        return changed
    }

    public func save(_ capture: Capture) throws {
        guard ULID.isValid(capture.id), capture.attachmentID.map(ULID.isValid) ?? true else {
            throw CaptureError.invalidID
        }
        if capture.kind == .food {
            // The food log has no YouTube links and keeps media only.
            guard capture.mode == .text, capture.links.isEmpty else { throw CaptureError.invalidID }
            guard capture.files.allSatisfy(\.isFoodMedia) else { throw CaptureError.notFoodMedia }
        }
        for link in capture.links {
            guard capture.mode == .text, ULID.isValid(link.attachmentID),
                  try YouTubeLink.canonical(link.url) == link.url else { throw LinkError.invalidURL }
        }
        for file in capture.files {
            guard capture.mode == .text, ULID.isValid(file.attachmentID) else { throw CaptureError.invalidID }
            let size = try fileURL(file).resourceValues(forKeys: [.fileSizeKey]).fileSize
            guard (size ?? 0) > 0 else { throw CaptureError.missingFile }
        }
        for clip in capture.clips {
            guard capture.mode == .text, ULID.isValid(clip.attachmentID) else { throw CaptureError.invalidID }
            guard clip.state != .recording else { throw CaptureError.stillRecording }
            let size = try clipURL(clip).resourceValues(forKeys: [.fileSizeKey]).fileSize
            guard (size ?? 0) > 0 else { throw CaptureError.missingAudio }
        }
        // A photo or a clip with nothing written about it is still an entry; the
        // server accepts an empty body when it is told attachments are on the way.
        if capture.mode == .text && capture.files.isEmpty && capture.clips.isEmpty
            && capture.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            throw CaptureError.emptyText
        }
        try JSONEncoder().encode(capture).write(to: manifest(capture.id), options: .atomic)
    }

    public func load(_ id: String) throws -> Capture {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return try JSONDecoder().decode(Capture.self, from: Data(contentsOf: manifest(id)))
    }

    public func audioURL(_ capture: Capture) throws -> URL {
        guard let id = capture.attachmentID, ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id).appendingPathExtension("m4a")
    }

    /// Attached files live in their own folder, extensionless, so nothing the
    /// user picks (a `.json`, say) can be mistaken for a capture manifest.
    public func fileURL(_ file: CaptureFile) throws -> URL {
        guard ULID.isValid(file.attachmentID) else { throw CaptureError.invalidID }
        return root.appendingPathComponent("files").appendingPathComponent(file.attachmentID)
    }

    public func clipURL(_ clip: CaptureClip) throws -> URL {
        guard ULID.isValid(clip.attachmentID) else { throw CaptureError.invalidID }
        // AVAudioRecorder picks its container from the extension.
        return root.appendingPathComponent("files").appendingPathComponent(clip.attachmentID).appendingPathExtension("m4a")
    }

    // MARK: Draft

    public func draft() throws -> CaptureDraft {
        let url = root.appendingPathComponent("draft.json")
        guard fm.fileExists(atPath: url.path) else { return CaptureDraft() }
        return try JSONDecoder().decode(CaptureDraft.self, from: Data(contentsOf: url))
    }

    private func updateDraft(_ change: (inout CaptureDraft) throws -> Void) throws {
        var value = try draft()
        try change(&value)
        try JSONEncoder().encode(value).write(to: root.appendingPathComponent("draft.json"), options: .atomic)
    }

    /// Copies a picked file into the draft, so it survives the picker's
    /// temporary URL going away and the app being closed before Save.
    @discardableResult
    public func stageFile(from source: URL, name: String, contentType: String?, now: Date = Date()) throws -> CaptureFile {
        let file = CaptureFile(name: name, contentType: contentType, now: now)
        let destination = try fileURL(file)
        try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try fm.copyItem(at: source, to: destination)
        guard (try destination.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0) > 0 else {
            try? fm.removeItem(at: destination)
            throw CaptureError.missingFile
        }
        try updateDraft { $0.files.append(file) }
        return file
    }

    @discardableResult
    public func stageFile(data: Data, name: String, contentType: String?, now: Date = Date()) throws -> CaptureFile {
        guard !data.isEmpty else { throw CaptureError.missingFile }
        let file = CaptureFile(name: name, contentType: contentType, now: now)
        let destination = try fileURL(file)
        try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: destination, options: .atomic)
        try updateDraft { $0.files.append(file) }
        return file
    }

    /// Records the clip in the draft before any audio is written, so a crash
    /// mid-recording still leaves a draft entry explaining whose file it is.
    public func beginClip(transcribe: Bool, now: Date = Date()) throws -> CaptureClip {
        let clip = CaptureClip(transcribe: transcribe, now: now)
        try fm.createDirectory(at: clipURL(clip).deletingLastPathComponent(), withIntermediateDirectories: true)
        try updateDraft { $0.clips.append(clip) }
        return clip
    }

    /// A clip that captured nothing is dropped rather than kept as an empty row.
    public func finishClip(_ id: String) throws {
        let clip = try draft().clips.first { $0.attachmentID == id }
        guard let clip else { return }
        let size = (try? clipURL(clip).resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? nil
        guard (size ?? 0) > 0 else {
            try discard(clip)
            throw CaptureError.missingAudio
        }
        try setClipState(id, .ready)
    }

    public func interruptClip(_ id: String) throws { try setClipState(id, .interrupted) }

    private func setClipState(_ id: String, _ state: CaptureClip.State) throws {
        try updateDraft { draft in
            if let index = draft.clips.firstIndex(where: { $0.attachmentID == id }) { draft.clips[index].state = state }
        }
    }

    /// Removes a staged file the user took back out of the draft.
    public func discardStaged(_ file: CaptureFile) throws {
        let url = try fileURL(file)
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
        try updateDraft { $0.files.removeAll { $0.attachmentID == file.attachmentID } }
    }

    public func discard(_ clip: CaptureClip) throws {
        let url = try clipURL(clip)
        if fm.fileExists(atPath: url.path) { try fm.removeItem(at: url) }
        try updateDraft { $0.clips.removeAll { $0.attachmentID == clip.attachmentID } }
    }

    /// Save entry (or Save food entry): turns the typed text plus everything
    /// staged into one capture, then empties the draft. The staged bytes are
    /// not moved — the capture refers to them where they already are.
    /// A food entry takes no links; the caller keeps them for the next entry.
    @discardableResult
    /// `location` is the device's fix at Save, if it had a recent one; the
    /// server uses it for the entry's place and the weather it looks up.
    public func commitDraft(text: String, youtubeURLs: [String], kind: CaptureKind = .journal,
                            location: (latitude: Double, longitude: Double)? = nil,
                            now: Date = Date()) throws -> Capture {
        let staged = try draft()
        let capture = try commit(text: text, youtubeURLs: youtubeURLs, kind: kind, files: staged.files,
                                 clips: staged.clips, location: location, now: now)
        try updateDraft { $0 = CaptureDraft() }
        return capture
    }

    /// A journal entry made of pictures that were never staged: the iPad
    /// notebook's rendered pages. The composer's draft is neither read nor
    /// cleared, so a half-written text entry waits where it was.
    @discardableResult
    public func commitImages(text: String, youtubeURLs: [String], images: [(data: Data, name: String)],
                             location: (latitude: Double, longitude: Double)? = nil,
                             now: Date = Date()) throws -> Capture {
        var files: [CaptureFile] = []
        do {
            for image in images {
                guard !image.data.isEmpty else { throw CaptureError.missingFile }
                let file = CaptureFile(name: image.name, contentType: "image/jpeg", now: now)
                let destination = try fileURL(file)
                try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
                try image.data.write(to: destination, options: .atomic)
                files.append(file)
            }
            return try commit(text: text, youtubeURLs: youtubeURLs, kind: .journal, files: files, clips: [],
                              location: location, now: now)
        } catch {
            for file in files { try? fm.removeItem(at: fileURL(file)) }
            throw error
        }
    }

    private func commit(text: String, youtubeURLs: [String], kind: CaptureKind, files: [CaptureFile],
                        clips: [CaptureClip], location: (latitude: Double, longitude: Double)?,
                        now: Date) throws -> Capture {
        let links = kind == .food ? [] : try youtubeURLs.map(YouTubeLink.canonical)
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // With nothing written, links are the entry's words; a file or clip needs none.
        var capture = Capture(text: body.isEmpty ? links.joined(separator: "\n") : body, kind: kind, now: now,
                              youtubeURLs: links, files: files, clips: clips)
        capture.latitude = location?.latitude
        capture.longitude = location?.longitude
        try save(capture)
        return capture
    }

    public func finishRecording(_ id: String) throws {
        var capture = try load(id)
        let url = try audioURL(capture)
        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0 else { throw CaptureError.missingAudio }
        capture.state = .pending
        capture.lastError = nil
        try save(capture)
    }

    /// Do not automatically upload an unfinalized container after an OS kill.
    /// The UI offers playback/export and an explicit recovery action.
    public func recoverInterruptedRecordings() throws {
        for var capture in try list() where capture.state == .recording {
            capture.state = .interrupted
            capture.lastError = "Recording was interrupted. Check playback before keeping it."
            try save(capture)
        }
        // A draft clip is only uploaded once Save entry is pressed, which is
        // the explicit keep; it just stays marked so the row can say so.
        try updateDraft { draft in
            for index in draft.clips.indices where draft.clips[index].state == .recording {
                draft.clips[index].state = .interrupted
            }
        }
    }

    public var server: URL? {
        get throws {
            let file = root.appendingPathComponent("server.json")
            guard fm.fileExists(atPath: file.path) else { return nil }
            return try ServerAddress.parse(JSONDecoder().decode(String.self, from: Data(contentsOf: file)))
        }
    }

    public func bind(to address: URL) throws {
        let normalized = try ServerAddress.parse(address.absoluteString)
        if let existing = try server, existing != normalized { throw CaptureError.differentServer }
        try JSONEncoder().encode(normalized.absoluteString)
            .write(to: root.appendingPathComponent("server.json"), options: .atomic)
    }

    private func manifest(_ id: String) -> URL { root.appendingPathComponent(id).appendingPathExtension("json") }
}
