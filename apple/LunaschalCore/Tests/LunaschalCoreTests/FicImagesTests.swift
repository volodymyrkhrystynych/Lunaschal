import XCTest
@testable import LunaschalCore

/// A chapter's pictures: the server's copies are found in its HTML, kept on
/// the device with the fic, and a picture that fails never fails the fic.
private actor ImageServer: FicDownloadTransport, FicImageTransport {
    let page: FicDownloadPage
    var fetched: [String] = []
    init(page: FicDownloadPage) { self.page = page }

    func ficDownloadPage(ficID: String, after: String) async throws -> FicDownloadPage { page }
    func mediaChunk(_ item: MediaDescriptor, offset: Int64, count: Int64) async throws -> Data { Data() }
    func ficImage(_ image: FicImageReference) async throws -> Data {
        fetched.append(image.name)
        if image.name == "gone.png" { throw HTTPFailure(status: 404) }
        return Data("png:\(image.name)".utf8)
    }
}

final class FicImagesTests: XCTestCase {
    private let fic = ULID.make()

    func testAServerCopyIsRecognisedAndAnythingElseIsNot() {
        XCTAssertEqual(FicImages.reference("/api/fanfic/\(fic)/images/a1.png"), FicImageReference(ficID: fic, name: "a1.png"))
        XCTAssertEqual(FicImages.reference("api/fanfic/\(fic)/images/a1.png")?.name, "a1.png")
        XCTAssertNil(FicImages.reference("https://example.com/a1.png"))
        XCTAssertNil(FicImages.reference("/api/fanfic/\(fic)/images/../secret"))
        XCTAssertNil(FicImages.reference("/api/fanfic/not-a-ulid/images/a.png"))
        XCTAssertNil(FicImages.reference("/api/fanfic/\(fic)/images/a.png/more"))
    }

    func testReferencesAreFoundOnceEachInOrder() {
        let html = """
        <p>One <img src="/api/fanfic/\(fic)/images/b.jpg" alt="b"></p>
        <img src="https://elsewhere.test/x.png"><img src="/api/fanfic/\(fic)/images/a.png">
        <img src="/api/fanfic/\(fic)/images/b.jpg">
        """
        XCTAssertEqual(FicImages.references(html: html).map(\.name), ["b.jpg", "a.png"])
    }

    func testAnImgIsABlockOfItsOwnAndASmilieStaysItsWords() {
        let blocks = ChapterText.blocks(html: """
        <div>Before the map.<br><img src="/api/fanfic/\(fic)/images/map.png" alt="A map"> After it.
        <img src="data:image/gif;base64,R0lG" alt=":)"></div>
        """, text: nil)
        XCTAssertEqual(blocks.map(\.kind), [.paragraph, .image("/api/fanfic/\(fic)/images/map.png"), .paragraph])
        XCTAssertEqual(blocks[1].text, "A map")
        XCTAssertEqual(blocks[2].text, "After it. [:)]")
        XCTAssertEqual(ChapterText.blocks(html: "<p><img src=\"https://x.test/a.png\"></p>", text: nil).map(\.kind),
                       [.image("https://x.test/a.png")], "one the server couldn't fetch keeps its web address")
    }

    func testDownloadingAFicKeepsItsPicturesAndSkipsOnesThatFail() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("replica.sqlite")
        let epoch = ULID.make()
        try ReplicaStore(url: url).apply(SyncPage(protocolVersion: 1, epoch: epoch, mode: "bootstrap", changes: [],
                                                  hasMore: false, cursor: "fics", collections: ["fics"]),
                                         startingBootstrap: true)
        let id = ULID.make()
        let html = "<p>x<img src=\"/api/fanfic/\(fic)/images/map.png\"><img src=\"/api/fanfic/\(fic)/images/gone.png\"></p>"
        let chapter = SyncChange(revision: 2, collection: "fic_chapters", id: id, deleted: false,
                                 data: ["id": .string(id), "ficId": .string(fic), "title": .string("One"),
                                        "contentHtml": .string(html)])
        let server = ImageServer(page: FicDownloadPage(epoch: epoch, fic: nil, chapters: [chapter], hasMore: false,
            after: "0:a", totalChapters: 1, textBytes: 10, bytesBefore: 0, pageBytes: 10, media: nil))
        let worker = LibraryDownload(replicaURL: url, mediaURL: root.appendingPathComponent("media"))
        try await worker.downloadFic(fic, using: server) { _ in }

        let images = try FicImageStore(root: root.appendingPathComponent("fic-images"))
        let map = try XCTUnwrap(images.file(FicImageReference(ficID: fic, name: "map.png")))
        XCTAssertEqual(try String(contentsOf: map, encoding: .utf8), "png:map.png")
        XCTAssertNil(images.file(FicImageReference(ficID: fic, name: "gone.png")))
        let usedBytes = try await worker.usedBytes()
        XCTAssertEqual(usedBytes, Int64("png:map.png".utf8.count), "counted as downloaded media")

        // A second download fetches only what is still missing.
        try await worker.downloadFic(fic, using: server) { _ in }
        let fetched = await server.fetched
        XCTAssertEqual(fetched, ["map.png", "gone.png", "gone.png"])

        try await worker.removeImages()
        XCTAssertNil(images.file(FicImageReference(ficID: fic, name: "map.png")))
    }
}
