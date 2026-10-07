#if DEBUG
import Foundation
import LunaschalCore
import UIKit

/// A made-up library for the simulator and UI tests, which have no server:
/// launched with `-libraryFixture`, the Library gets books from every
/// provider, three folders, bookmarks, and one book in each download state —
/// on the device, half on it, not on it, a PDF book, and one the "server"
/// can't send. Opening a book that isn't on the device downloads it from a
/// stand-in server that answers slowly enough for the progress, the queue
/// and the time estimate to be seen. Every launch resets the books to these
/// states. Debug builds only.
enum LibraryFixture {
    static let argument = "-libraryFixture"
    static var isActive: Bool { ProcessInfo.processInfo.arguments.contains(argument) }

    /// The Journal fixture's epoch: a different one would make this bootstrap
    /// wipe the journal rows when both fixtures are launched together.
    static let epoch = "01K6ZZZZZZZZZZZZZZZZZZZZZZ"

    struct Book {
        let id: String
        let title: String
        let author: String
        let site: String?
        let sourceType: String
        let tags: [String]
        let folders: [String]
        let chapters: Int
        /// How many of its chapters are already on the device.
        let onDevice: Int
        let daysSinceUpdate: Double
        let description: String
    }

    static let reading = "01K8BBBBBBBBBBBBBBBBBBBBF1"
    static let finished = "01K8BBBBBBBBBBBBBBBBBBBBF2"
    static let toRead = "01K8BBBBBBBBBBBBBBBBBBBBF3"

    static let downloaded = "01K8BBBBBBBBBBBBBBBBBBBB01"
    static let notDownloaded = "01K8BBBBBBBBBBBBBBBBBBBB02"
    static let queued = "01K8BBBBBBBBBBBBBBBBBBBB03"
    static let partial = "01K8BBBBBBBBBBBBBBBBBBBB04"
    static let pdf = "01K8BBBBBBBBBBBBBBBBBBBB05"
    static let missing = "01K8BBBBBBBBBBBBBBBBBBBB06"

    static let books: [Book] = [
        Book(id: downloaded, title: "The Long Way Round", author: "Merrow", site: "forums.spacebattles.com",
             sourceType: "xenforo", tags: ["Worm", "Slice of Life"], folders: [reading], chapters: 6, onDevice: 6,
             daysSinceUpdate: 0.2, description: "On the device: opens straight into the chapter it was left at."),
        Book(id: notDownloaded, title: "Ashes of the Old Guard", author: "Tallow", site: "forums.sufficientvelocity.com",
             sourceType: "xenforo", tags: ["Quest", "Fantasy"], folders: [reading], chapters: 24, onDevice: 0,
             daysSinceUpdate: 1, description: "Not on the device: opening it downloads it, with progress and time left."),
        Book(id: queued, title: "Quiet Hours", author: "lanternlight", site: nil, sourceType: "ao3",
             tags: ["Hurt/Comfort", "Fantasy"], folders: [toRead], chapters: 8, onDevice: 0,
             daysSinceUpdate: 3, description: "Not on the device: open it while another is downloading to see it queue."),
        Book(id: partial, title: "Field Notes on Dragons", author: "Ossory", site: nil, sourceType: "fanfiction",
             tags: ["Dragons", "Adventure"], folders: [], chapters: 10, onDevice: 3,
             daysSinceUpdate: 6, description: "Half on the device: the first chapters read now, the rest arrive."),
        Book(id: pdf, title: "A Treatise on Clockwork", author: "H. Ambrose", site: nil, sourceType: "pdf",
             tags: ["Non-fiction"], folders: [toRead], chapters: 0, onDevice: 0,
             daysSinceUpdate: 20, description: "A PDF book that isn't on the device yet."),
        Book(id: missing, title: "Deleted Upstream", author: "ghost", site: "forum.questionablequesting.com",
             sourceType: "xenforo", tags: ["Quest"], folders: [], chapters: 4, onDevice: 0,
             daysSinceUpdate: 40, description: "The server can't send this one: shows the error and Try again."),
        Book(id: "01K8BBBBBBBBBBBBBBBBBBBB07", title: "Patron Serial, Book Two", author: "Wren Calder", site: nil,
             sourceType: "patreon", tags: ["Sci-Fi"], folders: [finished], chapters: 3, onDevice: 3,
             daysSinceUpdate: 9, description: "On the device."),
        Book(id: "01K8BBBBBBBBBBBBBBBBBBBB08", title: "The Lighthouse Keeper", author: "Ines Varga", site: nil,
             sourceType: "epub", tags: ["Literary"], folders: [finished], chapters: 5, onDevice: 5,
             daysSinceUpdate: 120, description: "An imported EPUB, on the device."),
    ]

    static func chapterID(_ book: Book, _ index: Int) -> String {
        // The shared prefix, the book's own last two characters, then the chapter number.
        String(book.id.dropLast(4)) + book.id.suffix(2) + String(format: "%02d", index)
    }

    // MARK: Seeding

    static func seedIfAsked(root: URL) throws {
        guard isActive else { return }
        let iso = ISO8601DateFormatter()
        let now = Date()
        var changes: [[String: Any]] = [
            change("fic_folders", reading, ["name": "Reading now", "position": 0]),
            change("fic_folders", finished, ["name": "Finished", "position": 1]),
            change("fic_folders", toRead, ["name": "To read", "position": 2]),
        ]
        for book in books {
            changes.append(change("fics", book.id, bookData(book, now: now, iso: iso)))
            for index in 0..<book.onDevice { changes.append(chapter(book, index, now: now)) }
        }
        let first = books[0]
        changes.append(change("fic_bookmarks", "01K8BBBBBBBBBBBBBBBBBBBBK1",
                              ["ficId": first.id, "chapterId": chapterID(first, 3), "type": "continue",
                               "scrollPosition": 0.35, "createdAt": iso.string(from: now.addingTimeInterval(-7200))]))
        changes.append(change("fic_bookmarks", "01K8BBBBBBBBBBBBBBBBBBBBK2",
                              ["ficId": first.id, "chapterId": chapterID(first, 1), "type": "favorite",
                               "scrollPosition": 0, "createdAt": iso.string(from: now.addingTimeInterval(-86_400))]))
        let page: [String: Any] = ["protocolVersion": 1, "epoch": epoch, "mode": "bootstrap", "changes": changes,
                                   "hasMore": false, "cursor": "library-fixture",
                                   "collections": ["fics", "fic_folders", "fic_bookmarks", "fic_chapters"]]
        let decoded = try JSONDecoder().decode(SyncPage.self, from: JSONSerialization.data(withJSONObject: page))
        // A complete bootstrap sweeps what it doesn't carry, so chapters the
        // last run downloaded are gone again and every launch starts the same.
        try ReplicaStore(url: root.appendingPathComponent("replica.sqlite")).apply(decoded, startingBootstrap: true)
        // The PDF book goes back to not downloaded too.
        let media = try MediaStore(root: root.appendingPathComponent("downloaded-media", isDirectory: true))
        _ = try? media.removeDownloadedCopy(collection: "fics", id: pdf)
    }

    private static func change(_ collection: String, _ id: String, _ data: [String: Any]) -> [String: Any] {
        var data = data
        data["id"] = id
        return ["revision": 1, "collection": collection, "id": id, "deleted": false, "data": data]
    }

    private static func bookData(_ book: Book, now: Date, iso: ISO8601DateFormatter) -> [String: Any] {
        var data: [String: Any] = [
            "title": book.title, "author": book.author, "sourceType": book.sourceType,
            "description": book.description, "chapterCount": book.chapters,
            "wordCount": book.chapters * 3_200, "tags": book.tags, "folderIds": book.folders,
            "latestActivity": Int(now.addingTimeInterval(-book.daysSinceUpdate * 86_400).timeIntervalSince1970),
            "downloadStatus": "complete", "createdAt": iso.string(from: now.addingTimeInterval(-90 * 86_400)),
            "updatedAt": iso.string(from: now.addingTimeInterval(-book.daysSinceUpdate * 86_400)),
        ]
        if let site = book.site { data["site"] = site }
        return data
    }

    static func chapter(_ book: Book, _ index: Int, now: Date = Date()) -> [String: Any] {
        let text = paragraphs(book: book, chapter: index).joined(separator: "\n\n")
        return change("fic_chapters", chapterID(book, index), [
            "ficId": book.id, "position": index, "title": "Chapter \(index + 1)",
            "contentText": text, "contentHtml": "<p>" + text.replacingOccurrences(of: "\n\n", with: "</p><p>") + "</p>",
            "wordCount": text.split(separator: " ").count,
            "postedAt": Int(now.addingTimeInterval(-Double(book.chapters - index) * 86_400).timeIntervalSince1970),
        ])
    }

    private static let sentences = [
        "The rain had not stopped since morning, and the road had become a river of grey.",
        "She counted the lanterns along the wall and found one more than there had been yesterday.",
        "Nobody in the village could say when the bell had last been rung, only that it had been a bad year.",
        "He set the map down, smoothed the fold with his thumb, and said nothing for a long while.",
        "Somewhere below, a door opened and shut, and the whole tower seemed to listen.",
        "It was easier, she decided, to be wrong out loud than to be right in silence.",
        "The letter was three pages long and said, in the end, only that they were sorry.",
        "By the time the fire caught, the stars were out, and the cold had gone soft at the edges.",
    ]

    private static func paragraphs(book: Book, chapter: Int) -> [String] {
        (0..<14).map { paragraph in
            (0..<4).map { sentences[(book.title.count + chapter * 5 + paragraph * 3 + $0) % sentences.count] }
                .joined(separator: " ")
        }
    }

    // MARK: The stand-in server

    /// Answers `ficDownloadPage` a few chapters at a time, waiting between
    /// pages, and serves the PDF book in slow chunks.
    struct Transport: FicDownloadTransport {
        static let chaptersPerPage = 3
        static let pageDelay: UInt64 = 900_000_000

        func ficDownloadPage(ficID: String, after: String) async throws -> FicDownloadPage {
            try await Task.sleep(nanoseconds: Self.pageDelay)
            guard let book = books.first(where: { $0.id == ficID }), book.id != missing else {
                throw FicDownloadError.notOnServer
            }
            let start = after.isEmpty ? 0 : (Int(after.split(separator: ":").first ?? "") ?? -1) + 1
            let end = min(book.chapters, start + Self.chaptersPerPage)
            let chapters = (start..<max(start, end)).map { chapter(book, $0) }
            let size = { (chapters: [[String: Any]]) in
                chapters.reduce(0) { $0 + (($1["data"] as? [String: Any])?["contentText"] as? String ?? "").utf8.count * 2 }
            }
            let all = (0..<book.chapters).map { chapter(book, $0) }
            var page: [String: Any] = [
                "epoch": epoch, "fic": change("fics", book.id, bookData(book, now: Date(), iso: ISO8601DateFormatter())),
                "chapters": chapters, "hasMore": end < book.chapters,
                "after": end > start ? "\(end - 1):\(chapterID(book, end - 1))" : after,
                "totalChapters": book.chapters, "textBytes": size(all),
                "bytesBefore": size(Array(all.prefix(start))), "pageBytes": size(chapters),
            ]
            if book.sourceType == "pdf" {
                let file = try pdfFile()
                page["media"] = ["collection": "fics", "id": book.id, "available": true,
                                 "size": try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0,
                                 "sha256": try MediaStore.sha256(file), "mime": "application/pdf"]
            }
            return try JSONDecoder().decode(FicDownloadPage.self, from: JSONSerialization.data(withJSONObject: page))
        }

        func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data {
            // A megabyte a call, as the real one: smaller here so the bar moves.
            let data = try Data(contentsOf: try pdfFile())
            let size = min(count, 96 * 1024)
            try await Task.sleep(nanoseconds: 250_000_000)
            let lower = Int(offset), upper = min(data.count, Int(offset + size))
            return lower < upper ? data.subdata(in: lower..<upper) : Data()
        }
    }

    /// The PDF book: a few pages of text, made once per launch.
    static func pdfFile() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("library-fixture-clockwork.pdf")
        if FileManager.default.fileExists(atPath: url.path) { return url }
        let bounds = CGRect(x: 0, y: 0, width: 612, height: 792)
        let book = books.first { $0.id == pdf }!
        let data = UIGraphicsPDFRenderer(bounds: bounds).pdfData { context in
            for page in 0..<12 {
                context.beginPage()
                ("\(book.title) — \(page + 1)" as NSString).draw(at: CGPoint(x: 54, y: 54),
                    withAttributes: [.font: UIFont.boldSystemFont(ofSize: 22)])
                (paragraphs(book: book, chapter: page).prefix(6).joined(separator: "\n\n") as NSString)
                    .draw(in: bounds.insetBy(dx: 54, dy: 100), withAttributes: [.font: UIFont.systemFont(ofSize: 13)])
            }
        }
        try data.write(to: url)
        return url
    }
}
#endif
