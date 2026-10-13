import XCTest
@testable import LunaschalCore

/// A book's chapter list splits into one tab per threadmark category, and
/// Next/Previous never step out of the category being read.
final class ChapterCategoriesTests: XCTestCase {
    private let fic = ULID.make()

    private func chapter(_ title: String, _ position: Int, _ category: String?) -> SyncChange {
        let id = ULID.make()
        var data: [String: JSONValue] = ["id": .string(id), "ficId": .string(fic), "title": .string(title),
                                         "position": .number(Double(position)),
                                         "contentHtml": .string("<p>\(title)</p>"), "contentText": .string(title)]
        if let category { data["category"] = .string(category) }
        return SyncChange(revision: 1, collection: "fic_chapters", id: id, deleted: false, data: data)
    }

    private lazy var outline = [
        chapter("One", 0, "Threadmarks"), chapter("Two", 1, "Threadmarks"), chapter("Three", 2, "Threadmarks"),
        chapter("Omake", 3, "Sidestory"), chapter("Map", 4, "Informational"), chapter("Omake 2", 5, "Sidestory"),
    ]

    func testGroupsFollowReadingOrderOfEachCategorysFirstChapter() {
        let groups = ChapterCategories.groups(outline)
        XCTAssertEqual(groups.map(\.category), ["Threadmarks", "Sidestory", "Informational"])
        XCTAssertEqual(groups[1].chapters.map(\.title), ["Omake", "Omake 2"])
    }

    func testAChapterWithNoCategoryIsAThreadmarkAndTheDefaultIsTitled() {
        let groups = ChapterCategories.groups([chapter("A", 0, nil), chapter("B", 1, "threadmarks"), chapter("C", 2, "")])
        XCTAssertEqual(groups.count, 1, "absent, empty and the server's default are one tab")
        XCTAssertEqual(groups[0].chapters.count, 3)
        XCTAssertEqual(groups[0].title, "Threadmarks")
        XCTAssertTrue(ChapterCategories.groups([]).isEmpty)
    }

    func testNextAndPreviousStayInsideTheCategory() {
        let three = ChapterCategories.neighbors(of: outline[2].id, in: outline)
        XCTAssertEqual(three.previous?.title, "Two")
        XCTAssertNil(three.next, "the last threadmark doesn't run on into the sidestories")

        let omake = ChapterCategories.neighbors(of: outline[3].id, in: outline)
        XCTAssertNil(omake.previous)
        XCTAssertEqual(omake.next?.title, "Omake 2", "skipping the informational post between them")

        let map = ChapterCategories.neighbors(of: outline[4].id, in: outline)
        XCTAssertNil(map.previous); XCTAssertNil(map.next)

        let unknown = ChapterCategories.neighbors(of: ULID.make(), in: outline)
        XCTAssertNil(unknown.previous); XCTAssertNil(unknown.next)
    }

    func testTheOutlineCarriesEachChaptersCategory() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let store = try ReplicaStore(url: root.appendingPathComponent("replica.sqlite"))
        try store.apply(SyncPage(protocolVersion: 1, epoch: ULID.make(), mode: "bootstrap", changes: outline,
                                 hasMore: false, cursor: ULID.make(), collections: ["fic_chapters"]),
                        startingBootstrap: true)
        let stored = try store.chapterOutline(bookID: fic)
        XCTAssertEqual(stored.map(ChapterCategories.category(of:)),
                       ["Threadmarks", "Threadmarks", "Threadmarks", "Sidestory", "Informational", "Sidestory"])
        XCTAssertEqual(ChapterCategories.groups(stored).map(\.category), ["Threadmarks", "Sidestory", "Informational"])
    }
}
