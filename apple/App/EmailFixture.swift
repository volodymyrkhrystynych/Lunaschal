import Foundation
import LunaschalCore

/// Demo mail for the UI tests, which run with no server: `-emailFixture` fills
/// the cached inbox, `-emailDisconnectedFixture` also loses the connection, and
/// `-emailChatFixture` makes today's chat the agent's message about it.
enum EmailFixture {
    static let argument = "-emailFixture"
    static let disconnectedArgument = "-emailDisconnectedFixture"
    static let chatArgument = "-emailChatFixture"
    private static let interview = "01K7ZZZZZZZZZZZZZZZZZZZZE1"

    static var isOn: Bool {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        return [argument, disconnectedArgument, chatArgument].contains(where: arguments.contains)
        #else
        return false
        #endif
    }

    static func seedIfAsked(root: URL) throws {
        guard isOn else { return }
        let folder = root.appendingPathComponent("email", isDirectory: true)
        if FileManager.default.fileExists(atPath: folder.path) { try FileManager.default.removeItem(at: folder) }
        let store = try EmailStore(root: folder)
        let iso = ISO8601DateFormatter()
        let at = { (days: Double) in iso.string(from: Date().addingTimeInterval(-days * 86_400)) }
        let list = [
            EmailMessage(id: interview, subject: "Globex — next steps", sender: "Dana Okafor",
                         senderEmail: "dana@globex.example.com", snippet: "We would like to set up",
                         receivedAt: at(0), category: "job_application", importance: "high",
                         importanceReason: "job_next_step", summary: "Dana asks whether Tuesday works for an interview."),
            EmailMessage(id: "01K7ZZZZZZZZZZZZZZZZZZZZE2", subject: "This week in backend engineering",
                         sender: "The Backend Weekly", senderEmail: "hello@weekly.example.com",
                         snippet: "Five things worth reading", receivedAt: at(1), category: "newsletter", importance: "low"),
        ]
        try store.saveInbox(list)
        var opened = list[0]
        opened.bodyText = "Are you free Tuesday?"
        opened.bodyHtml = #"<p>Are you free <b>Tuesday</b>?</p><img data-src="/api/email/images/ab12">"#
        try store.saveMessage(opened)
        let lost = ProcessInfo.processInfo.arguments.contains(disconnectedArgument)
        try store.saveAccounts([EmailAccount(provider: "gmail", emailAddress: "jordan@example.com", connected: !lost,
                                             needsReconnect: lost, lastSyncedAt: Int(Date().timeIntervalSince1970),
                                             refreshExpiresAt: iso.string(from: Date().addingTimeInterval(3.5 * 86_400)))])
    }

    static let conversation = ChatConversation(id: "01K7EMAILFIXTURECONVERSAT1", messages: [
        ChatMessage(id: "01K7EMAILFIXTUREMESSAGE001", role: "assistant",
                    content: "Dana at Globex wants to set up an interview.",
                    metadata: #"{"email":{"kind":"important","items":[{"id":"\#(interview)","sender":"Dana Okafor","subject":"Globex — next steps","reason":"job_next_step"}]}}"#,
                    status: "done", createdAt: "2026-11-24T09:00:00+00:00"),
        ChatMessage(id: "01K7EMAILFIXTUREMESSAGE002", role: "assistant",
                    content: "Gmail disconnected: jordan@example.com needs you to sign in again.",
                    metadata: #"{"email":{"kind":"disconnected","provider":"gmail","address":"jordan@example.com"}}"#,
                    status: "done", createdAt: "2026-11-24T09:01:00+00:00"),
    ])
}
