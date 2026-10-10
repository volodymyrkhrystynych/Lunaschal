import Foundation

/// A paginated notes canvas from the iPad's Capture tab: blank pages, or the
/// pages of one newspaper issue with ink over them. Each page's markup is
/// opaque bytes (PaperKit's `dataRepresentation()`), so this store never
/// imports PaperKit and is tested on Linux like the rest of the core.
public struct Notebook: Codable, Identifiable, Hashable {
    public enum Source: Codable, Hashable {
        case blank
        /// The issue's edition date, `YYYY-MM-DD`.
        case newspaper(date: String)
    }

    /// How the pages are held. A paged notebook keeps one markup per page; a
    /// column keeps a single markup with every page stacked down it
    /// (`NotebookColumn`), which is what lets a newspaper be read as one
    /// continuous scroll and ink cross from one page to the next.
    public enum Layout: String, Codable, Hashable {
        case paged, column
    }

    public let id: String
    public var title: String
    public let createdAt: Date
    public var updatedAt: Date
    public let source: Source
    /// The one YouTube video this notebook's entry will carry, canonical form.
    public var youtubeURL: String?
    public var pageCount: Int
    /// Pages backed by the issue PDF, which come first. Zero for a blank notebook.
    public var pdfPageCount: Int
    /// Pages written or pasted on; a newspaper files only these and its cover.
    public var markedPages: Set<Int>
    public var checkpoint: String?
    /// Journal captures made from this notebook, oldest first.
    public var savedCaptureIDs: [String]
    public var savedAt: Date?
    /// Absent from every notebook written before columns existed, which is
    /// exactly what makes those read as paged.
    public var layout: Layout?

    init(title: String, source: Source, pageCount: Int, pdfPageCount: Int, now: Date) {
        id = ULID.make(now: now)
        self.title = title
        createdAt = now
        updatedAt = now
        self.source = source
        youtubeURL = nil
        self.pageCount = pageCount
        self.pdfPageCount = pdfPageCount
        markedPages = []
        checkpoint = nil
        savedCaptureIDs = []
        savedAt = nil
        layout = nil
    }

    public var isColumn: Bool { layout == .column }

    /// How many markups a checkpoint holds: one for a column, one per page otherwise.
    public var markupCount: Int { isColumn ? 1 : pageCount }

    public var newspaperDate: String? {
        if case .newspaper(let date) = source { return date }
        return nil
    }

    /// The pages a Save files, in order. A newspaper of sixty pages becomes
    /// its cover and whatever was marked, never sixty attachments; a blank
    /// notebook files every page it has (empty ones are dropped at render).
    public var pagesToFile: [Int] {
        guard newspaperDate != nil else { return Array(0..<pageCount) }
        return Array(Set([0]).union(markedPages.filter { $0 < pageCount })).sorted()
    }
}

/// Same publish discipline as `DrawingStore`: a checkpoint becomes visible
/// only after every page and the preview are written, and the previous one is
/// kept so a failed write never replaces good ink. Call from the main actor.
public final class NotebookStore {
    public let root: URL
    private let fm = FileManager.default
    public static let maximumPDFBytes: Int64 = 512 * 1024 * 1024

    public init(root: URL) throws {
        self.root = root
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        try fm.createDirectory(at: inbox, withIntermediateDirectories: true)
    }

    public func notebooks() throws -> [Notebook] {
        try fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try JSONDecoder().decode(Notebook.self, from: Data(contentsOf: $0)) }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    public func notebook(_ id: String) throws -> Notebook {
        let value = try JSONDecoder().decode(Notebook.self, from: Data(contentsOf: manifest(id)))
        guard value.id == id else { throw CaptureError.invalidID }
        return value
    }

    public func create(title: String = "Notes", now: Date = Date()) throws -> Notebook {
        let notebook = Notebook(title: title, source: .blank, pageCount: 1, pdfPageCount: 0, now: now)
        try write(notebook)
        return notebook
    }

    /// A notebook over one issue. The PDF is moved in before any manifest
    /// exists, so a download that fails or isn't a PDF leaves nothing behind.
    public func createNewspaper(date: String, pdf: URL, pageCount: Int, now: Date = Date()) throws -> Notebook {
        guard NewspaperIssue.isDate(date), pageCount > 0 else { throw NotebookError.invalidIssue }
        try Self.validatePDF(pdf)
        var notebook = Notebook(title: NewspaperIssue.title(date), source: .newspaper(date: date),
                                pageCount: pageCount, pdfPageCount: pageCount, now: now)
        notebook.layout = .column
        let folder = try directory(notebook.id)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        let destination = folder.appendingPathComponent("source.pdf")
        do {
            try fm.moveItem(at: pdf, to: destination)
            try write(notebook)
        } catch {
            try? fm.removeItem(at: folder)
            throw error
        }
        return notebook
    }

    /// The notebook to reopen for an issue: the newest one over it, filed or
    /// not. An issue is written on through the day, and filing it again
    /// replaces the entry it was filed as, so a Save must not start it over.
    public func newspaper(date: String) throws -> Notebook? {
        try notebooks().first { $0.newspaperDate == date }
    }

    public func pdfURL(_ notebook: Notebook) throws -> URL? {
        guard notebook.newspaperDate != nil else { return nil }
        let url = try directory(notebook.id).appendingPathComponent("source.pdf")
        return fm.fileExists(atPath: url.path) ? url : nil
    }

    /// Every page's markup in order — for a column, the one markup holding
    /// them all — or empty before the first checkpoint.
    public func pages(_ notebook: Notebook) throws -> [Data] {
        guard let checkpoint = notebook.checkpoint else { return [] }
        let folder = try checkpointDirectory(notebook.id, checkpoint)
        return try (0..<notebook.markupCount).map { try Data(contentsOf: folder.appendingPathComponent("page-\($0).markup")) }
    }

    /// Each page's locked layer (pictures pinned under the ink), nil where a
    /// page has none. Same length as `pages`; empty before the first checkpoint.
    public func lockedLayers(_ notebook: Notebook) throws -> [Data?] {
        guard let checkpoint = notebook.checkpoint else { return [] }
        let folder = try checkpointDirectory(notebook.id, checkpoint)
        return (0..<notebook.markupCount).map { try? Data(contentsOf: folder.appendingPathComponent("page-\($0).locked")) }
    }

    public func previewURL(_ notebook: Notebook) throws -> URL? {
        guard let checkpoint = notebook.checkpoint else { return nil }
        return try checkpointDirectory(notebook.id, checkpoint).appendingPathComponent("preview.png")
    }

    /// `column` saves a column: `pages` is its one markup and `slots` how many
    /// pages are stacked in it. A paged notebook saved this way becomes a
    /// column from then on — the converted copy of its pages is in this
    /// checkpoint, and the paged originals stay in the previous one.
    @discardableResult
    public func checkpoint(_ id: String, pages: [Data], locked: [Data?] = [], marked: Set<Int>,
                           preview: Data, column slots: Int? = nil) throws -> Notebook {
        var notebook = try notebook(id)
        let pageCount = slots ?? pages.count
        guard !pages.isEmpty, !preview.isEmpty, !pages.contains(where: \.isEmpty),
              slots == nil || pages.count == 1,
              // A column saved as pages would read back as one page of a stack.
              slots != nil || !notebook.isColumn,
              pageCount >= notebook.pdfPageCount, locked.count <= pages.count,
              !locked.contains(where: { $0?.isEmpty == true }) else { throw DrawingError.incompleteCheckpoint }
        let previous = notebook.checkpoint
        let revision = ULID.make()
        let folder = try checkpointDirectory(id, revision)
        try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        for (index, page) in pages.enumerated() {
            try page.write(to: folder.appendingPathComponent("page-\(index).markup"), options: .atomic)
        }
        for (index, layer) in locked.enumerated() {
            guard let layer else { continue }
            try layer.write(to: folder.appendingPathComponent("page-\(index).locked"), options: .atomic)
        }
        try preview.write(to: folder.appendingPathComponent("preview.png"), options: .atomic)
        notebook.checkpoint = revision
        notebook.pageCount = pageCount
        notebook.markedPages = marked.filter { $0 < pageCount }
        if slots != nil { notebook.layout = .column }
        notebook.updatedAt = Date()
        try write(notebook)
        // Current + previous only, as in DrawingStore.
        for version in (try? fm.contentsOfDirectory(at: folder.deletingLastPathComponent(), includingPropertiesForKeys: nil)) ?? []
            where ULID.isValid(version.lastPathComponent)
            && version.lastPathComponent != revision && version.lastPathComponent != previous {
            try? fm.removeItem(at: version)
        }
        return notebook
    }

    public func restorePrevious(_ id: String) throws -> Notebook {
        var notebook = try notebook(id)
        let parent = try directory(id)
        let candidates = try fm.contentsOfDirectory(at: parent, includingPropertiesForKeys: nil)
            .filter { ULID.isValid($0.lastPathComponent) && $0.lastPathComponent != notebook.checkpoint }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
        guard let previous = candidates.first(where: { folder in
            guard let names = try? fm.contentsOfDirectory(atPath: folder.path) else { return false }
            return names.contains("preview.png") && names.contains("page-0.markup")
        }) else { throw DrawingError.noPreviousCheckpoint }
        let count = try fm.contentsOfDirectory(atPath: previous.path).filter { $0.hasSuffix(".markup") }.count
        notebook.checkpoint = previous.lastPathComponent
        // A column's one markup holds every page, so its page count is not a
        // file count — unless the previous checkpoint predates the column, in
        // which case the notebook is paged again and converts on next open.
        if notebook.isColumn && count == 1 {
            notebook.pageCount = max(notebook.pageCount, notebook.pdfPageCount)
        } else {
            notebook.layout = nil
            notebook.pageCount = max(count, notebook.pdfPageCount)
        }
        notebook.updatedAt = Date()
        try write(notebook)
        return notebook
    }

    /// One video per notebook; setting another replaces it, nil removes it.
    @discardableResult
    public func setYouTube(_ id: String, url: String?) throws -> Notebook {
        var notebook = try notebook(id)
        let trimmed = url?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        notebook.youtubeURL = trimmed.isEmpty ? nil : try YouTubeLink.canonical(trimmed)
        notebook.updatedAt = Date()
        try write(notebook)
        return notebook
    }

    public func rename(_ id: String, title: String) throws {
        var notebook = try notebook(id)
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        notebook.title = trimmed.isEmpty ? "Notes" : trimmed
        notebook.updatedAt = Date()
        try write(notebook)
    }

    @discardableResult
    public func markSaved(_ id: String, captureID: String, now: Date = Date()) throws -> Notebook {
        var notebook = try notebook(id)
        notebook.savedCaptureIDs.append(captureID)
        notebook.savedAt = now
        try write(notebook)
        return notebook
    }

    public func delete(_ id: String) throws {
        try fm.removeItem(at: manifest(id))
        try? fm.removeItem(at: directory(id))
    }

    // MARK: Screenshot inbox

    /// A screenshot that arrived while no notebook was open; the next editor
    /// to open takes it onto its current page.
    public func enqueueScreenshot(_ data: Data, now: Date = Date()) throws {
        guard !data.isEmpty else { throw CaptureError.missingFile }
        try data.write(to: inbox.appendingPathComponent(ULID.make(now: now)).appendingPathExtension("png"), options: .atomic)
    }

    public func inboxCount() -> Int {
        ((try? fm.contentsOfDirectory(atPath: inbox.path)) ?? []).filter { $0.hasSuffix(".png") }.count
    }

    /// Oldest first. The caller hands back the files it placed with
    /// `clearInbox`, so a crash between reading and placing loses nothing.
    public func inboxItems() throws -> [(url: URL, data: Data)] {
        try fm.contentsOfDirectory(at: inbox, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "png" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .map { ($0, try Data(contentsOf: $0)) }
    }

    public func clearInbox(_ urls: [URL]) {
        for url in urls where url.deletingLastPathComponent().standardizedFileURL == inbox.standardizedFileURL {
            try? fm.removeItem(at: url)
        }
    }

    // MARK: Paths

    static func validatePDF(_ url: URL) throws {
        let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
        guard size > 0, Int64(size) <= maximumPDFBytes else { throw NotebookError.notAPDF }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        guard try handle.read(upToCount: 5) == Data("%PDF-".utf8) else { throw NotebookError.notAPDF }
    }

    private var inbox: URL { root.appendingPathComponent("inbox", isDirectory: true) }

    private func write(_ notebook: Notebook) throws {
        try JSONEncoder().encode(notebook).write(to: manifest(notebook.id), options: .atomic)
    }
    private func manifest(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id).appendingPathExtension("json")
    }
    private func directory(_ id: String) throws -> URL {
        guard ULID.isValid(id) else { throw CaptureError.invalidID }
        return root.appendingPathComponent(id, isDirectory: true)
    }
    private func checkpointDirectory(_ id: String, _ checkpoint: String) throws -> URL {
        guard ULID.isValid(checkpoint) else { throw CaptureError.invalidID }
        return try directory(id).appendingPathComponent(checkpoint, isDirectory: true)
    }
}

/// One archived issue as the server lists it (`GET /api/newspapers/issues`)
/// or the replica projects it (`newspaper_issues`).
public struct NewspaperIssue: Codable, Equatable {
    public let date: String
    public let pageCount: Int

    public init(date: String, pageCount: Int) {
        self.date = date
        self.pageCount = pageCount
    }

    /// From a replica record, whose columns may arrive camelCased or not.
    public init?(record: [String: JSONValue]?) {
        guard let record, let date = record["date"]?.string, Self.isDate(date),
              let count = (record["pageCount"] ?? record["page_count"])?.int, count > 0 else { return nil }
        self.init(date: date, pageCount: count)
    }

    public static func isDate(_ value: String) -> Bool {
        value.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil
    }

    public static func title(_ date: String) -> String { "Toronto Star · \(date)" }

    public enum Choice: Equatable {
        case today(NewspaperIssue)
        /// Today's isn't archived yet; this is the newest that is.
        case newest(NewspaperIssue)
        case none
    }

    /// Today's paper by the 4am day, so the paper read after midnight is
    /// still that evening's; otherwise the newest one, for the caller to offer.
    public static func choose(_ issues: [NewspaperIssue], now: Date = Date(), calendar: Calendar = .current) -> Choice {
        let today = DayKey.of(now, calendar: calendar)
        if let issue = issues.first(where: { $0.date == today }) { return .today(issue) }
        // ISO dates sort as strings; one dated in the future is not "newest".
        if let issue = issues.filter({ $0.date <= today }).max(by: { $0.date < $1.date }) { return .newest(issue) }
        return .none
    }
}

public enum NotebookError: LocalizedError {
    case invalidIssue, notAPDF, noIssue
    public var errorDescription: String? {
        switch self {
        case .invalidIssue: return "That newspaper issue has no pages."
        case .notAPDF: return "The server didn't send a readable newspaper PDF."
        case .noIssue: return "No newspaper issue is archived yet."
        }
    }
}
