import Foundation
import LunaschalCore

/// Demo data for the Writing screens, since UI tests run with no server:
/// launched with `-writingFixture`, the screens show two made-up books — one
/// a fanfic with pinned notes, a note history and discussions holding pending
/// and accepted note cards. Nothing is fetched and nothing is saved.
struct WritingFixture {
    static let argument = "-writingFixture"

    static func ifAsked() -> WritingFixture? {
        #if DEBUG
        ProcessInfo.processInfo.arguments.contains(argument) ? WritingFixture() : nil
        #else
        nil
        #endif
    }

    private static let salt = "01K7WRITINGFIXTUREPROJECT1"
    private static let keeper = "01K7WRITINGFIXTUREPROJECT2"
    private static let mara = "01K7WRITINGFIXTURENOTE0001"

    let projects = [
        WritingProject(id: salt, title: "The Salt Roads",
                       description: "A smuggler story set in the canon's port city, ten years after the war."),
        WritingProject(id: keeper, title: "Notes on lighthouse keeping",
                       description: "A short piece inspired by research on 19th-century lighthouses."),
    ]

    let notes = [
        WritingNote(id: mara, projectId: salt, title: "Mara Vell", docType: "character", pinned: true, content: """
            Nineteen. Raised on the Port Ash docks by her uncle after the war took her parents.

            Owes the harbourmaster a favour she has not paid — and he has not asked for it yet, which frightens her more.

            She swims better than she walks: she grew up diving for what the wrecks left behind, and knows the reef by feel.
            """),
        WritingNote(id: "01K7WRITINGFIXTURENOTE0002", projectId: salt, title: "Harbourmaster Quell",
                    docType: "character", pinned: true, content: """
            Old, patient, corrupt in the quiet way. Keeps a ledger of favours instead of coin.
            In canon he is a minor clerk; here he runs the harbour. This is the story's biggest divergence.
            """),
        WritingNote(id: "01K7WRITINGFIXTURENOTE0003", projectId: salt, title: "Port Ash", docType: "worldbuilding",
                    content: """
            Tide-locked harbour; the reef closes the mouth at low water, so ships come and go twice a day.
            The Harbour Guild licenses every boat. Unlicensed boats run the reef at night.
            """),
        WritingNote(id: "01K7WRITINGFIXTURENOTE0004", projectId: salt, title: "Outline", docType: "outline",
                    content: """
            1. The wreck — Mara finds the sealed ledger in a sunken guild boat.
            2. The favour — Quell finally asks.
            3. The reef run — the night crossing.
            4. The ledger is opened.
            """),
        WritingNote(id: "01K7WRITINGFIXTURENOTE0005", projectId: salt, title: "Open questions", docType: "note",
                    content: "Does Mara's uncle know about the favour? Who sank the guild boat?"),
        WritingNote(id: "01K7WRITINGFIXTURENOTE0006", projectId: keeper, title: "Lyra Ashworth", docType: "character",
                    pinned: true,
                    content: "Forty years a keeper. Speaks little, writes everything down in a ledger she never shows anyone."),
        WritingNote(id: "01K7WRITINGFIXTURENOTE0007", projectId: keeper, title: "Outline", docType: "outline",
                    content: "1. The storm.\n2. What the light found.\n3. The ledger is opened."),
    ]

    private let discussionsByProject = [
        salt: [WritingDiscussion(id: "01K7WRITINGFIXTUREDISCUSS1", title: "Who is Mara?"),
               WritingDiscussion(id: "01K7WRITINGFIXTUREDISCUSS2", title: "Quell vs. canon")],
        keeper: [WritingDiscussion(id: "01K7WRITINGFIXTUREDISCUSS3", title: "The storm night")],
    ]

    var discussions: [WritingDiscussion] { discussionsByProject.values.flatMap { $0 } }

    func discussions(for project: String) -> [WritingDiscussion] { discussionsByProject[project] ?? [] }

    func revisions(_ noteID: String) -> [WritingNoteRevision] {
        guard noteID == Self.mara else { return [] }
        let json = """
        [{"id": "01K7WRITINGFIXTUREREVISION2", "title": "Mara Vell", "source": "accept",
          "createdAt": "2026-10-14T18:01:00+00:00",
          "content": "Nineteen. Raised on the Port Ash docks by her uncle after the war took her parents.\\n\\nOwes the harbourmaster a favour she has not paid — and he has not asked for it yet, which frightens her more."},
         {"id": "01K7WRITINGFIXTUREREVISION1", "title": "Mara Vell", "source": "edit",
          "createdAt": "2026-10-13T21:30:00+00:00",
          "content": "Raised on the docks. Owes the harbourmaster a favour she has not paid."}]
        """
        return (try? JSONDecoder().decode([WritingNoteRevision].self, from: Data(json.utf8))) ?? []
    }

    private static func message(_ id: String, _ role: String, _ minute: Int, _ content: String,
                                metadata: String? = nil) -> ChatMessage {
        ChatMessage(id: id, role: role, content: content, metadata: metadata, status: role == "assistant" ? "done" : nil,
                    createdAt: String(format: "2026-10-14T18:%02d:00+00:00", minute))
    }

    func conversation(_ id: String) -> ChatConversation? {
        switch id {
        case "01K7WRITINGFIXTUREDISCUSS1":
            let accepted = #"""
            {"proposals": [{"id": "p0", "kind": "writing_note_update", "status": "accepted",
              "data": {"noteId": "01K7WRITINGFIXTURENOTE0001", "title": "Mara Vell", "mode": "append",
                       "content": "She swims better than she walks: she grew up diving for what the wrecks left behind."}}],
             "steps": [{"tool": "writing_read", "ok": true, "title": "Mara Vell", "kind": "note"},
                       {"tool": "propose_note_update", "ok": true, "arg": "addition to \"Mara Vell\""}]}
            """#
            let pending = #"""
            {"proposals": [
              {"id": "p1", "kind": "writing_note_update", "status": "pending",
               "data": {"noteId": "01K7WRITINGFIXTURENOTE0001", "title": "Mara Vell", "mode": "append",
                        "content": "Her uncle does not know about the favour. She is keeping it from him because he would pay it himself.",
                        "reason": "settled why Mara keeps the favour secret"}},
              {"id": "p2", "kind": "writing_note", "status": "pending",
               "data": {"title": "Tobin Vell", "docType": "character",
                        "content": "Mara's uncle. Ex-guild pilot, lost his licence after the war. Would pay any debt of hers without asking what it was."}}],
             "steps": [{"tool": "writing_search", "ok": true, "arg": "uncle", "count": 2},
                       {"tool": "canon_search", "ok": true, "arg": "Vell family", "count": 3},
                       {"tool": "canon_read", "ok": true, "title": "Port Ash — Notable families"},
                       {"tool": "propose_note_update", "ok": true, "arg": "addition to \"Mara Vell\""},
                       {"tool": "propose_note", "ok": true, "arg": "new character note \"Tobin Vell\""}],
             "thinking": "Canon only mentions the Vells as a pilot family; an uncle who lost his licence fits that."}
            """#
            return ChatConversation(id: id, messages: [
                Self.message("01K7WRITINGFIXTUREMESSAGE1", "user", 0, "What if Mara grew up diving for wrecks?"),
                Self.message("01K7WRITINGFIXTUREMESSAGE2", "assistant", 1, """
                    That gives her a reason to know the reef better than anyone licensed to sail it — and it ties her to \
                    the sunken guild boat in your outline. I've staged it as an addition to her note.
                    """, metadata: accepted),
                Self.message("01K7WRITINGFIXTUREMESSAGE3", "user", 5,
                             "Her uncle shouldn't know about the favour. He'd pay it himself."),
                Self.message("01K7WRITINGFIXTUREMESSAGE4", "assistant", 6, """
                    That works, and it fits canon: the **Vells** are an old pilot family in Port Ash, so an uncle who \
                    lost his licence after the war gives him a reason to feel he owes the harbour something.

                    Two things worth deciding next:
                    - Does Quell *know* the uncle would pay? If so, the favour is really leverage over both of them.
                    - When Mara finds the ledger, is her own name in it?

                    I've staged the secret on Mara's note, and a new note for her uncle.
                    """, metadata: pending),
            ])
        case "01K7WRITINGFIXTUREDISCUSS2":
            let steps = #"""
            {"steps": [{"tool": "canon_search", "ok": true, "arg": "Quell", "count": 2},
                       {"tool": "canon_read", "ok": true, "title": "Quell"}]}
            """#
            return ChatConversation(id: id, messages: [
                Self.message("01K7WRITINGFIXTUREMESSAGE5", "user", 10, "What is Quell in the original?"),
                Self.message("01K7WRITINGFIXTUREMESSAGE6", "assistant", 11, """
                    In canon Quell is a harbour clerk who appears in two chapters and records ship arrivals. Your note \
                    makes him the harbourmaster, which your notes already mark as the main divergence — so I'll treat \
                    yours as the version that counts.
                    """, metadata: steps),
            ])
        case "01K7WRITINGFIXTUREDISCUSS3":
            return ChatConversation(id: id, messages: [
                Self.message("01K7WRITINGFIXTUREMESSAGE7", "user", 20, "How long would a keeper go without sleep in a storm?"),
                Self.message("01K7WRITINGFIXTUREMESSAGE8", "assistant", 21, """
                    Through the whole storm, usually — the lamp had to be trimmed every few hours. Lyra would likely \
                    write in her ledger between trims to stay awake.
                    """, metadata: #"{"steps": [{"tool": "local_knowledge_search", "ok": true, "arg": "lighthouse keeper storm watch", "count": 4}]}"#),
            ])
        default:
            return nil
        }
    }
}
