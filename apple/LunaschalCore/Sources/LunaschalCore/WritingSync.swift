import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Moves drafts to the server and books back. Nothing here decides what happens
// to text the server refuses except to stop sending it and say so: a draft
// leaves the phone only when the server has taken it or the author chose.

/// The server answered and said no, so sending the same thing again won't help.
public enum WritingSaveRefusal: Error, Equatable {
    case changed(WritingServerCopy)
    case missing
    case refused(String)
}

public protocol WritingTransport {
    func createDocument(_ document: WritingDocument) async throws
    /// Returns the revision the save produced.
    func saveDocument(_ key: WritingDocumentKey, title: String?, content: String?, baseRevision: Int,
                      capturedAt: Date) async throws -> Int
    func writingProjects() async throws -> [WritingProject]
    func writingBundle(_ projectID: String) async throws -> WritingBundle
}

public final class WritingSync {
    private let store: WritingStore
    private let gate = NSLock()
    private var running = false
    private var runAgain = false

    public init(store: WritingStore) { self.store = store }

    /// Sends every draft, oldest first. One pass at a time: two passes sending
    /// from the same base would conflict with each other, so a request during a
    /// pass makes that pass go round again instead. A network failure stops
    /// the pass with everything still kept. Returns what the server refused.
    public func push(using transport: WritingTransport) async throws -> [String] {
        guard begin() else { return [] }
        defer { end() }
        var refused: [String] = []
        repeat {
            let pending = store.allDocuments()
                .filter { $0.isDirty && $0.hold == nil }
                .sorted { ($0.draft?.editedAt ?? .distantPast) < ($1.draft?.editedAt ?? .distantPast) }
            for item in pending {
                try Task.checkCancellation()
                // Read again: earlier sends took time, and typing didn't stop.
                guard let document = store.document(item.key, project: item.projectId),
                      document.isDirty, document.hold == nil else { continue }
                if let reason = try await send(document, using: transport) { refused.append(reason) }
            }
        } while again()
        return refused
    }

    private func begin() -> Bool {
        gate.lock(); defer { gate.unlock() }
        if running { runAgain = true; return false }
        running = true
        return true
    }

    private func again() -> Bool {
        gate.lock(); defer { gate.unlock() }
        let more = runAgain
        runAgain = false
        return more
    }

    private func end() {
        gate.lock(); defer { gate.unlock() }
        running = false
    }

    private func send(_ document: WritingDocument, using transport: WritingTransport) async throws -> String? {
        let key = document.key, project = document.projectId
        do {
            if !document.onServer {
                try await transport.createDocument(document)
                try store.markCreated(key, project: project)
            }
            guard let draft = document.draft else { return nil }
            let revision = try await transport.saveDocument(
                key, title: draft.title, content: draft.content, baseRevision: document.server.revision,
                capturedAt: draft.editedAt)
            try store.acknowledge(key, project: project, title: draft.title, content: draft.content,
                                  revision: revision)
            return nil
        } catch let refusal as WritingSaveRefusal {
            switch refusal {
            case let .changed(latest): try store.hold(key, project: project, .changed(latest))
            case .missing: try store.hold(key, project: project, .deleted)
            case let .refused(message): return "The server didn't take “\(document.title)”: \(message)"
            }
            return nil
        }
    }

    /// Brings every book the server has changed down to the phone.
    public func pull(using transport: WritingTransport) async throws {
        let projects = try await transport.writingProjects()
        let known = store.projects()
        try store.saveProjects(projects)
        for gone in known where !projects.contains(where: { $0.id == gone.id }) {
            // A book deleted elsewhere keeps any draft that was never sent.
            if !store.documents(project: gone.id).contains(where: \.isDirty) {
                try store.removeProject(gone.id)
            }
        }
        for project in projects where store.needsBundle(project) {
            try Task.checkCancellation()
            try store.apply(try await transport.writingBundle(project.id))
        }
        // apply() files each book at the front; the server's order is the right one.
        try store.saveProjects(projects)
    }
}

extension JournalAPI: WritingTransport {
    public func createDocument(_ document: WritingDocument) async throws {
        struct Body: Encodable { let id: String; let title: String; let docType: String? }
        var req = request("api/writing/projects/\(try Self.pathID(document.projectId))/\(document.kind.routeName)",
                          method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(Body(id: document.id, title: document.server.title,
                                                     docType: document.kind == .note ? document.docType : nil))
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        try Self.checkWritingStatus(http.statusCode, data: data)
    }

    public func saveDocument(_ key: WritingDocumentKey, title: String?, content: String?, baseRevision: Int,
                             capturedAt: Date) async throws -> Int {
        struct Body: Encodable {
            let title: String?; let content: String?; let baseRevision: Int; let capturedAt: Int
        }
        var req = request("api/writing/\(key.kind.routeName)/\(try Self.pathID(key.id))", method: "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(Body(title: title, content: content, baseRevision: baseRevision,
                                                     capturedAt: Int(capturedAt.timeIntervalSince1970)))
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else { throw CaptureError.invalidResponse }
        return try Self.writingSaveResult(status: http.statusCode, data: data)
    }

    /// What one save's reply means; separate so it can be tested without a server.
    static func writingSaveResult(status: Int, data: Data) throws -> Int {
        struct Saved: Decodable { let revision: Int }
        struct Conflict: Decodable {
            let current: Current
            struct Current: Decodable { let title: String; let content: String; let revision: Int }
        }
        if status == 409 {
            let current = try JSONDecoder().decode(Conflict.self, from: data).current
            throw WritingSaveRefusal.changed(WritingServerCopy(title: current.title, content: current.content,
                                                               revision: current.revision))
        }
        try checkWritingStatus(status, data: data)
        return try JSONDecoder().decode(Saved.self, from: data).revision
    }

    /// 401, 403, 408 and 429 are about the moment and not the document, so
    /// they stop the pass with everything kept, as a network failure does.
    static func checkWritingStatus(_ status: Int, data: Data) throws {
        switch status {
        case 200..<300:
            return
        case 404:
            throw WritingSaveRefusal.missing
        case 400..<500 where status != 401 && status != 403 && status != 408 && status != 429:
            throw WritingSaveRefusal.refused(errorMessage(data) ?? "HTTP \(status)")
        default:
            throw HTTPFailure(status: status)
        }
    }

    public func writingBundle(_ projectID: String) async throws -> WritingBundle {
        try JSONDecoder().decode(WritingBundle.self,
                                 from: await get("api/writing/projects/\(try Self.pathID(projectID))/bundle", [:]))
    }
}
