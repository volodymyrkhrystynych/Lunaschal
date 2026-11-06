import Foundation
import LunaschalCore

/// Demo data for the Chat tab, since UI tests run with no server: launched
/// with `-chatFixture`, today's conversation is one exchange whose reply
/// staged names for two screenshot runs. Nothing is fetched; accepting a card
/// fails the way it would offline.
enum ChatFixture {
    static let argument = "-chatFixture"

    static func ifAsked() -> ChatConversation? {
        #if DEBUG
        ProcessInfo.processInfo.arguments.contains(argument) ? conversation : nil
        #else
        nil
        #endif
    }

    private static let metadata = #"""
    {"proposals": [{"id": "p1", "kind": "screenshot_titles", "status": "pending",
      "data": {"items": [
        {"entryId": "01K7CHATFIXTURESCREENSHOT1", "title": "Comparing flights to Lviv",
         "date": "2026-10-28", "time": "18:04", "endTime": "18:41", "shots": 6},
        {"entryId": "01K7CHATFIXTURESCREENSHOT2", "title": "Reading about lighthouse lenses",
         "date": "2026-10-27", "time": "21:10", "endTime": "21:32", "shots": 3}]}}],
     "steps": [{"tool": "find_untitled_screenshots", "ok": true, "arg": "7 days", "count": 2},
               {"tool": "propose_screenshot_titles", "ok": true, "arg": "2 screenshot run names"}]}
    """#

    private static let conversation = ChatConversation(id: "01K7CHATFIXTURECONVERSATN1", messages: [
        ChatMessage(id: "01K7CHATFIXTUREMESSAGE0001", role: "user", content: "Can you name my screenshot events?",
                    metadata: nil, status: nil, createdAt: "2026-10-29T09:00:00+00:00"),
        ChatMessage(id: "01K7CHATFIXTUREMESSAGE0002", role: "assistant",
                    content: "I found two unnamed runs and staged a name for each — check them on the card.",
                    metadata: metadata, status: "done", createdAt: "2026-10-29T09:01:00+00:00"),
    ])
}
