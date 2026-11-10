import Foundation

// A book on the phone. Every chapter and note is one small file holding the
// server's last copy and, beside it, whatever has been typed since. Text reaches
// this file before anything tries the network, so a save that fails, a server
// that is down and an app that is killed all leave the words where they were.
//
// One file per document rather than one per book: this is written every few
// hundred milliseconds while someone types, and rewriting a whole manuscript
// each time would be what made typing slow.

public enum WritingDocumentKind: String, Codable, Sendable {
    case chapter, note
    /// The path segment of its /api/writing routes.
    public var routeName: String { self == .chapter ? "chapters" : "notes" }
}

public struct WritingDocumentKey: Codable, Hashable, Sendable {
    public let kind: WritingDocumentKind
    public let id: String
    public init(_ kind: WritingDocumentKind, _ id: String) { self.kind = kind; self.id = id }
}

public struct WritingServerCopy: Codable, Equatable, Sendable {
    public var title: String
    public var content: String
    public var revision: Int
    public init(title: String, content: String, revision: Int) {
        self.title = title; self.content = content; self.revision = revision
    }
}

/// What has been typed and the server hasn't taken. A nil field is unchanged.
public struct WritingDraft: Codable, Equatable, Sendable {
    public var title: String?
    public var content: String?
    public var editedAt: Date
    public init(title: String? = nil, content: String? = nil, editedAt: Date) {
        self.title = title; self.content = content; self.editedAt = editedAt
    }
}

/// Why a draft has stopped being sent. Only the author can settle these.
public enum WritingHold: Codable, Equatable, Sendable {
    case changed(WritingServerCopy)
    case deleted
}

public struct WritingDocument: Codable, Equatable, Identifiable, Sendable {
    public let kind: WritingDocumentKind
    public let id: String
    public let projectId: String
    public var server: WritingServerCopy
    public var position: Int
    public var docType: String
    public var pinned: Bool
    /// False for one made offline, until its create has gone through.
    public var onServer: Bool
    public var draft: WritingDraft?
    public var hold: WritingHold?
    /// Where the cursor was, in UTF-16 units, for reopening at the same place.
    public var cursor: Int?

    public var key: WritingDocumentKey { WritingDocumentKey(kind, id) }
    public var title: String { draft?.title ?? server.title }
    public var content: String { draft?.content ?? server.content }
    public var isDirty: Bool { draft != nil || !onServer }

    public init(kind: WritingDocumentKind, id: String, projectId: String, server: WritingServerCopy,
                position: Int = 0, docType: String = "note", pinned: Bool = false, onServer: Bool = true) {
        self.kind = kind; self.id = id; self.projectId = projectId; self.server = server
        self.position = position; self.docType = docType; self.pinned = pinned; self.onServer = onServer
    }
}

public enum WritingStoreError: LocalizedError, Equatable {
    case invalidID
    case missing
    public var errorDescription: String? {
        switch self {
        case .invalidID: return "That document has an unusable id."
        case .missing: return "That document is no longer on this device."
        }
    }
}

/// How a held draft is settled.
public enum WritingResolution: Equatable, Sendable {
    /// Send this text over the latest; the other text stays in the server's history.
    case keepMine
    /// Drop this text and take the server's.
    case useLatest
    /// Take the server's, and keep this text as a new document.
    case saveCopy
}

public final class WritingStore {
    public let root: URL
    private let lock = NSRecursiveLock()

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    // MARK: Books

    private var projectsFile: URL { root.appendingPathComponent("projects.json") }

    public func projects() -> [WritingProject] {
        lock.lock(); defer { lock.unlock() }
        guard let data = try? Data(contentsOf: projectsFile) else { return [] }
        return (try? JSONDecoder().decode([WritingProject].self, from: data)) ?? []
    }

    public func saveProjects(_ projects: [WritingProject]) throws {
        lock.lock(); defer { lock.unlock() }
        try JSONEncoder().encode(projects).write(to: projectsFile, options: .atomic)
    }

    /// True when the server's copy of a book is newer than the one kept here.
    public func needsBundle(_ project: WritingProject) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard let data = try? Data(contentsOf: bundleMark(project.id)),
              let seen = String(data: data, encoding: .utf8) else { return true }
        return seen != (project.updatedAt ?? "")
    }

    private func bundleMark(_ projectID: String) -> URL {
        root.appendingPathComponent(projectID).appendingPathComponent("synced-at")
    }

    // MARK: Documents

    private func directory(_ projectID: String) throws -> URL {
        guard Self.isSafe(projectID) else { throw WritingStoreError.invalidID }
        let url = root.appendingPathComponent(projectID, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func file(_ key: WritingDocumentKey, project: String) throws -> URL {
        guard Self.isSafe(key.id) else { throw WritingStoreError.invalidID }
        return try directory(project).appendingPathComponent("\(key.kind.rawValue)-\(key.id).json")
    }

    /// Ids become file names, so only plain ones are allowed.
    static func isSafe(_ id: String) -> Bool {
        !id.isEmpty && id.count <= 64 && id.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }
    }

    private func write(_ document: WritingDocument) throws {
        let url = try file(document.key, project: document.projectId)
        try JSONEncoder().encode(document).write(to: url, options: .atomic)
    }

    public func documents(project: String) -> [WritingDocument] {
        lock.lock(); defer { lock.unlock() }
        guard let url = try? directory(project),
              let names = try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)
        else { return [] }
        return names.filter { $0.pathExtension == "json" }
            .compactMap { try? JSONDecoder().decode(WritingDocument.self, from: Data(contentsOf: $0)) }
            .sorted { ($0.position, $0.id) < ($1.position, $1.id) }
    }

    public func allDocuments() -> [WritingDocument] {
        lock.lock(); defer { lock.unlock() }
        guard let names = try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isDirectoryKey])
        else { return [] }
        return names.filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
            .flatMap { documents(project: $0.lastPathComponent) }
    }

    public func document(_ key: WritingDocumentKey, project: String) -> WritingDocument? {
        lock.lock(); defer { lock.unlock() }
        guard let url = try? file(key, project: project), let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(WritingDocument.self, from: data)
    }

    public func remove(_ key: WritingDocumentKey, project: String) throws {
        lock.lock(); defer { lock.unlock() }
        try? FileManager.default.removeItem(at: try file(key, project: project))
    }

    public func removeProject(_ id: String) throws {
        lock.lock(); defer { lock.unlock() }
        try? FileManager.default.removeItem(at: try directory(id))
        try saveProjects(projects().filter { $0.id != id })
    }

    /// Change a document in place, under the lock.
    @discardableResult
    private func update(_ key: WritingDocumentKey, project: String,
                        _ change: (inout WritingDocument) throws -> Void) throws -> WritingDocument {
        lock.lock(); defer { lock.unlock() }
        guard var document = document(key, project: project) else { throw WritingStoreError.missing }
        try change(&document)
        try write(document)
        return document
    }

    /// Keep what was typed. Text matching the server's copy is not a draft at all.
    @discardableResult
    public func edit(_ key: WritingDocumentKey, project: String, title: String? = nil, content: String? = nil,
                     at date: Date = Date()) throws -> WritingDocument {
        try update(key, project: project) { document in
            var draft = document.draft ?? WritingDraft(editedAt: date)
            if let title { draft.title = title == document.server.title ? nil : title }
            if let content { draft.content = content == document.server.content ? nil : content }
            draft.editedAt = date
            document.draft = draft.title == nil && draft.content == nil ? nil : draft
        }
    }

    public func setCursor(_ key: WritingDocumentKey, project: String, _ cursor: Int) throws {
        try update(key, project: project) { $0.cursor = cursor }
    }

    /// A chapter or note begun here. It sorts last, and the server keeps its id.
    @discardableResult
    public func create(_ kind: WritingDocumentKind, project: String, title: String, docType: String = "note",
                       content: String = "", at date: Date = Date()) throws -> WritingDocument {
        lock.lock(); defer { lock.unlock() }
        let last = documents(project: project).filter { $0.kind == kind }.map(\.position).max() ?? -1
        var document = WritingDocument(kind: kind, id: ULID.make(), projectId: project,
                                       server: WritingServerCopy(title: title, content: "", revision: 0),
                                       position: last + 1, docType: docType, onServer: false)
        if !content.isEmpty { document.draft = WritingDraft(content: content, editedAt: date) }
        try write(document)
        return document
    }

    public func setPosition(_ key: WritingDocumentKey, project: String, _ position: Int) throws {
        try update(key, project: project) { $0.position = position }
    }

    public func setPinned(_ key: WritingDocumentKey, project: String, _ pinned: Bool) throws {
        try update(key, project: project) { $0.pinned = pinned }
    }

    public func setDocType(_ key: WritingDocumentKey, project: String, _ docType: String) throws {
        try update(key, project: project) { $0.docType = docType }
    }

    // MARK: Sync

    func markCreated(_ key: WritingDocumentKey, project: String) throws {
        try update(key, project: project) { $0.onServer = true }
    }

    /// The server took a save. A draft that has moved on since it was sent stays:
    /// only what was actually sent has stopped needing to be.
    func acknowledge(_ key: WritingDocumentKey, project: String, title: String?, content: String?,
                     revision: Int) throws {
        try update(key, project: project) { document in
            if let title { document.server.title = title }
            if let content { document.server.content = content }
            document.server.revision = revision
            guard var draft = document.draft else { return }
            if draft.title == document.server.title { draft.title = nil }
            if draft.content == document.server.content { draft.content = nil }
            document.draft = draft.title == nil && draft.content == nil ? nil : draft
        }
    }

    func hold(_ key: WritingDocumentKey, project: String, _ hold: WritingHold) throws {
        try update(key, project: project) { $0.hold = hold }
    }

    /// Takes in a whole book from the server. A clean document simply becomes
    /// the server's; one with a draft is never touched, only held if the server
    /// moved underneath it, so that nothing typed here can be replaced here.
    public func apply(_ bundle: WritingBundle) throws {
        lock.lock(); defer { lock.unlock() }
        let project = bundle.project.id
        var incoming = [WritingDocumentKey: WritingDocument]()
        for chapter in bundle.chapters {
            let copy = WritingServerCopy(title: chapter.title, content: chapter.content ?? "", revision: chapter.revision)
            let document = WritingDocument(kind: .chapter, id: chapter.id, projectId: project, server: copy,
                                           position: chapter.position)
            incoming[document.key] = document
        }
        for (index, note) in bundle.notes.enumerated() {
            let copy = WritingServerCopy(title: note.title, content: note.content ?? "", revision: note.revision)
            let document = WritingDocument(kind: .note, id: note.id, projectId: project, server: copy,
                                           position: index, docType: note.docType, pinned: note.pinned)
            incoming[document.key] = document
        }
        for local in documents(project: project) where incoming[local.key] == nil {
            if !local.onServer { continue }
            if local.draft == nil { try remove(local.key, project: project) }
            else if local.hold != .deleted { try hold(local.key, project: project, .deleted) }
        }
        for (key, fresh) in incoming {
            guard var local = document(key, project: project) else { try write(fresh); continue }
            local.position = fresh.position
            local.docType = fresh.docType
            local.pinned = fresh.pinned
            if local.draft == nil {
                local.server = fresh.server
                local.hold = nil
            } else if local.title == fresh.server.title, local.content == fresh.server.content {
                local.server = fresh.server
                local.draft = nil
                local.hold = nil
            } else if fresh.server.revision != local.server.revision {
                local.hold = .changed(fresh.server)
            } else {
                local.hold = nil
            }
            try write(local)
        }
        try saveProjects([bundle.project] + projects().filter { $0.id != project })
        try Data((bundle.project.updatedAt ?? "").utf8).write(to: bundleMark(project), options: .atomic)
    }

    /// The server's latest copy of one document, for a screen opened while online.
    public func refresh(_ key: WritingDocumentKey, project: String, server: WritingServerCopy) throws {
        try update(key, project: project) { document in
            guard server.revision != document.server.revision else { return }
            if document.draft == nil {
                document.server = server
            } else if document.title == server.title, document.content == server.content {
                document.server = server
                document.draft = nil
            } else {
                document.hold = .changed(server)
            }
        }
    }

    /// A restore on the server replaces the document, draft included: it is what
    /// the author just asked for, and the replaced text is in the history.
    public func restored(_ key: WritingDocumentKey, project: String, server: WritingServerCopy) throws {
        try update(key, project: project) { document in
            document.server = server
            document.draft = nil
            document.hold = nil
        }
    }

    /// Settles a held draft. Returns the new document when a copy was made.
    @discardableResult
    public func resolve(_ key: WritingDocumentKey, project: String, _ resolution: WritingResolution,
                        at date: Date = Date()) throws -> WritingDocument? {
        lock.lock(); defer { lock.unlock() }
        guard let current = document(key, project: project), let hold = current.hold else {
            throw WritingStoreError.missing
        }
        func copy(title: String) throws -> WritingDocument {
            try create(current.kind, project: project, title: title, docType: current.docType,
                       content: current.content, at: date)
        }
        switch (hold, resolution) {
        case let (.changed(latest), .keepMine):
            try update(key, project: project) { document in
                document.server = latest
                document.hold = nil
                if document.draft != nil { document.draft?.editedAt = date }
            }
            return nil
        case let (.changed(latest), .useLatest):
            try update(key, project: project) { document in
                document.server = latest
                document.draft = nil
                document.hold = nil
            }
            return nil
        case let (.changed(latest), .saveCopy):
            let made = try copy(title: "\(current.title) (copy)")
            try update(key, project: project) { document in
                document.server = latest
                document.draft = nil
                document.hold = nil
            }
            return made
        case (.deleted, .useLatest):
            try remove(key, project: project)
            return nil
        case (.deleted, .keepMine), (.deleted, .saveCopy):
            let made = try copy(title: current.title)
            try remove(key, project: project)
            return made
        }
    }
}
