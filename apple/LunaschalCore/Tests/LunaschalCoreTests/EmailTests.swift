import XCTest
@testable import LunaschalCore

final class EmailTests: XCTestCase {
    private let id = "01K7ZZZZZZZZZZZZZZZZZZZZE1"

    func testDecodesAServerRowAndValuesThisBuildHasNotHeardOf() throws {
        let json = #"{"id":"a","subject":"","sender":"","senderEmail":"x@y.z","snippet":"hi","receivedAt":"2026-11-24T10:00:00+00:00","category":"brand_new","importance":"high","importanceReason":"deadline","summary":null}"#
        let message = try JSONDecoder().decode(EmailMessage.self, from: Data(json.utf8))
        XCTAssertEqual(message.from, "x@y.z")
        XCTAssertEqual(message.title, "(no subject)")
        XCTAssertEqual(message.preview, "hi")
        XCTAssertEqual(message.reason, .deadline)
        XCTAssertTrue(message.isImportant)
        XCTAssertNotNil(message.date)
    }

    func testAccountDecodesAndSaysHowLongItHas() throws {
        let json = #"[{"provider":"gmail","emailAddress":"me@x.y","connected":true,"lastSyncedAt":1700000000,"lastSyncError":null,"syncEnabled":true,"needsReconnect":false,"authErrorAt":null,"connectedAt":null,"refreshExpiresAt":"2026-11-27T12:00:00+00:00"}]"#
        var account = try XCTUnwrap(JSONDecoder().decode([EmailAccount].self, from: Data(json.utf8)).first)
        let now = try XCTUnwrap(EmailDate.parse("2026-11-24T10:00:00+00:00"))
        XCTAssertEqual(account.health(now: now), .expires(days: 3))
        XCTAssertEqual(account.health(now: now.addingTimeInterval(9 * 86_400)), .expires(days: 0))
        account.refreshExpiresAt = nil
        XCTAssertEqual(account.health(now: now), .connected)
        account.connected = false
        XCTAssertEqual(account.health(now: now), .disconnected)
        account.needsReconnect = true
        XCTAssertEqual(account.health(now: now), .needsReconnect)
    }

    func testDaysKeepTheServersOrder() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let now = try XCTUnwrap(EmailDate.parse("2026-11-24T10:00:00+00:00"))
        let list = ["2026-11-24T09:00:00+00:00", "2026-11-24T01:00:00+00:00", "2026-11-23T23:00:00+00:00", "bad"]
            .enumerated().map { EmailMessage(id: "\($0.offset)", receivedAt: $0.element) }
        let groups = EmailDays.group(list, now: now, calendar: calendar)
        XCTAssertEqual(groups.map(\.title), ["Today", "Yesterday", "Undated"])
        XCTAssertEqual(groups[0].messages.map(\.id), ["0", "1"])
    }

    func testRoutesFromPushesAndLinks() {
        XCTAssertEqual(EmailRoute(userInfo: ["kind": "email", "route": "reconnect"]), .reconnect)
        XCTAssertEqual(EmailRoute(userInfo: ["kind": "email", "route": "email", "emailId": id]), .message(id))
        XCTAssertEqual(EmailRoute(userInfo: ["kind": "email", "emailId": "../x"]), .inbox)
        XCTAssertNil(EmailRoute(userInfo: ["kind": "code_task", "emailId": id]))
        for route in [EmailRoute.inbox, .reconnect, .message(id)] { XCTAssertEqual(EmailRoute(url: route.url), route) }
        XCTAssertNil(EmailRoute(url: URL(string: "lunaschal://chat")!))
    }

    func testSignInEndings() {
        func parse(_ text: String) -> EmailSignIn? { EmailSignIn.parse(URL(string: text)!) }
        XCTAssertEqual(parse("lunaschal://email?connected=1"), .connected)
        guard case .failed? = parse("lunaschal://email?error=whatever") else { return XCTFail() }
        XCTAssertNotEqual(parse("lunaschal://email?error=declined"), parse("lunaschal://email?error=other_account"))
        XCTAssertNil(parse("lunaschal://email"))
        XCTAssertNil(parse("https://email?connected=1"))
    }

    func testChatNotices() {
        let item = #"{"id":"\#(id)","sender":"Dana","subject":"Next steps","reason":"job_next_step"}"#
        XCTAssertEqual(EmailNotice.parse(metadata: #"{"email":{"kind":"important","items":[\#(item),{"id":"x"}]}}"#),
                       .important([.init(id: id, sender: "Dana", subject: "Next steps", reason: .jobNextStep)]))
        XCTAssertEqual(EmailNotice.parse(metadata: #"{"email":{"kind":"disconnected","provider":"gmail","address":"a@b"}}"#),
                       .disconnected(provider: "gmail", address: "a@b"))
        XCTAssertNil(EmailNotice.parse(metadata: #"{"coach":{}}"#))
        XCTAssertNil(EmailNotice.parse(metadata: nil))
    }

    func testImagesLoadOnlyWhenAskedAndOnlyFromTheServersCopies() throws {
        let html = #"<img data-src="/api/email/images/ab12"><img data-src="https://evil/x">"#
        XCTAssertEqual(EmailHTML.imageCount(html), 1)
        XCTAssertFalse(EmailHTML.document(html, images: false).contains(#" src=""#))
        let shown = EmailHTML.document(html, images: true)
        XCTAssertTrue(shown.contains(#"<img src="lunaschal-image://email/ab12">"#))
        XCTAssertTrue(shown.contains("default-src 'none'"))
        XCTAssertTrue(shown.contains(#"data-src="https://evil/x""#))
        XCTAssertEqual(EmailHTML.imageHash(URL(string: "lunaschal-image://email/ab12")!), "ab12")
        XCTAssertNil(EmailHTML.imageHash(URL(string: "lunaschal-image://email/..")!))
        XCTAssertNil(EmailHTML.imageHash(URL(string: "https://email/ab12")!))
    }

    func testStoreKeepsTheRecentMessages() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try EmailStore(root: root)
        XCTAssertTrue(store.inbox().isEmpty)
        let message = EmailMessage(id: id, subject: "Hi", receivedAt: "2026-11-24T10:00:00+00:00", bodyText: "body")
        try store.saveInbox([message])
        try store.saveMessage(message)
        try store.saveAccounts([EmailAccount(provider: "gmail", emailAddress: "a", connected: true)])
        let again = try EmailStore(root: root)
        XCTAssertEqual(again.inbox(), [message])
        XCTAssertEqual(again.message(id), message)
        XCTAssertEqual(again.accounts().first?.provider, "gmail")
        XCTAssertThrowsError(try store.saveMessage(EmailMessage(id: "../x", receivedAt: "")))
        for _ in 0..<(EmailStore.keep + 5) { try store.saveMessage(EmailMessage(id: ULID.make(), receivedAt: "")) }
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("messages").path).count, EmailStore.keep)
    }
}
