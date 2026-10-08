import XCTest
@testable import LunaschalCore

final class FicImportTests: XCTestCase {
    private func link(_ string: String) -> FicImportLink? { FicImportLink(URL(string: string)!) }

    func testRecognisesEverySiteTheServerImportsFrom() {
        XCTAssertEqual(link("https://forums.spacebattles.com/threads/a-fic.12345/")?.site, "SpaceBattles")
        XCTAssertEqual(link("https://forums.sufficientvelocity.com/threads/9/page-3")?.site, "Sufficient Velocity")
        XCTAssertEqual(link("https://forum.questionablequesting.com/posts/777/")?.site, "Questionable Questing")
        XCTAssertEqual(link("https://www.fanfiction.net/s/123/1/Title")?.site, "FanFiction.net")
        XCTAssertEqual(link("https://m.fanfiction.net/s/123/")?.site, "FanFiction.net")
        XCTAssertEqual(link("https://archiveofourown.org/works/42/chapters/9")?.site, "AO3")
        XCTAssertEqual(link("https://www.patreon.com/posts/chapter-five-98765")?.site, "Patreon")
        XCTAssertEqual(link("HTTPS://WWW.SpaceBattles.com/threads/1")?.site, nil, "the forum host keeps its subdomain")
        XCTAssertEqual(link("https://FORUMS.SPACEBATTLES.COM/threads/1")?.site, "SpaceBattles")
    }

    func testRefusesPagesThatAreNotAFic() {
        XCTAssertNil(link("https://forums.spacebattles.com/forums/creative-writing.18/"))
        XCTAssertNil(link("https://archiveofourown.org/tags/Worm/works"))
        XCTAssertNil(link("https://www.patreon.com/someone"))
        XCTAssertNil(link("https://example.com/threads/1"))
        XCTAssertNil(link("ftp://forums.spacebattles.com/threads/1"))
    }

    func testFindsTheLinkInSharedText() {
        let text = "Worm (Complete) | SpaceBattles\nhttps://forums.spacebattles.com/threads/worm.1/ via Safari"
        XCTAssertEqual(FicImportLink.find(in: text)?.url.absoluteString, "https://forums.spacebattles.com/threads/worm.1/")
        XCTAssertEqual(FicImportLink.find(in: "see https://example.com/x and https://archiveofourown.org/works/5")?.site, "AO3")
        XCTAssertNil(FicImportLink.find(in: "no links here"))
    }

    func testReplySummaryNamesWhatHappened() throws {
        let fresh = try JSONDecoder().decode(FicImportReply.self, from: Data(#"{"id":"01K8"}"#.utf8))
        XCTAssertTrue(fresh.summary(site: "AO3").hasPrefix("Importing from AO3"))
        XCTAssertEqual(FicImportReply(id: "x", alreadyExists: true).summary(site: "AO3"), "That AO3 fic is already in your library.")
        XCTAssertTrue(FicImportReply(id: "x", restarted: true).summary(site: "AO3").contains("trying it again"))
    }

    func testOutboxKeepsALinkOnceAndInOrder() throws {
        let outbox = try FicImportOutbox(root: FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true))
        let first = try XCTUnwrap(link("https://archiveofourown.org/works/1"))
        let second = try XCTUnwrap(link("https://archiveofourown.org/works/2"))
        try outbox.append(first)
        try outbox.append(second)
        try outbox.append(first)
        XCTAssertEqual(try outbox.list().map(\.link), [first, second])
    }

    private final class Transport: FicImportTransport {
        var answers: [String: Result<FicImportReply, Error>]
        var sent: [String] = []
        init(_ answers: [String: Result<FicImportReply, Error>]) { self.answers = answers }
        func importFic(_ url: URL) async throws -> FicImportReply {
            sent.append(url.absoluteString)
            return try answers[url.absoluteString]!.get()
        }
    }

    func testSyncDropsRefusalsAndStopsWhenTheServerCannotBeReached() async throws {
        let outbox = try FicImportOutbox(root: FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true))
        let urls = ["https://archiveofourown.org/works/1", "https://archiveofourown.org/works/2",
                    "https://archiveofourown.org/works/3", "https://archiveofourown.org/works/4"]
        for url in urls { try outbox.append(try XCTUnwrap(link(url))) }
        let transport = Transport([
            urls[0]: .success(FicImportReply(id: "a")),
            urls[1]: .failure(FicServerFailure(status: 422, body: Data(#"{"error":"Could not find a thread"}"#.utf8))),
            urls[2]: .failure(URLError(.notConnectedToInternet)),
            urls[3]: .success(FicImportReply(id: "d")),
        ])
        do {
            _ = try await FicImportSync(outbox: outbox).run(using: transport)
            XCTFail("an unreachable server ends the pass")
        } catch let error as URLError {
            XCTAssertTrue(error.isUnreachable)
        }
        XCTAssertEqual(transport.sent, Array(urls.prefix(3)))
        XCTAssertEqual(try outbox.list().map(\.link.url.absoluteString), Array(urls.suffix(2)),
                       "the sent and the refused are gone; the rest waits")

        transport.answers[urls[2]] = .success(FicImportReply(id: "c", alreadyExists: true))
        let outcome = try await FicImportSync(outbox: outbox).run(using: transport)
        XCTAssertEqual(outcome.imported.count, 2)
        XCTAssertTrue(try outbox.list().isEmpty)
    }

    func testSyncKeepsALinkTheServerFailedOnOrAskedToSignInFor() async throws {
        let outbox = try FicImportOutbox(root: FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true))
        let url = "https://archiveofourown.org/works/1"
        try outbox.append(try XCTUnwrap(link(url)))
        for status in [401, 500] {
            let transport = Transport([url: .failure(FicServerFailure(status: status, body: Data()))])
            do { _ = try await FicImportSync(outbox: outbox).run(using: transport); XCTFail() } catch {}
            XCTAssertEqual(try outbox.list().count, 1, "HTTP \(status) is not a refusal")
        }
    }
}
