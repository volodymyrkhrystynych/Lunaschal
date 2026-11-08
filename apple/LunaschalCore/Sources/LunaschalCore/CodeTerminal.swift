import Foundation

/// One line of a task's terminal: what the task screen draws, top to bottom.
public struct CodeTerminalLine: Equatable, Identifiable, Sendable {
    public enum Style: String, Equatable, Sendable {
        /// Something you said: the request, a reply, an approval.
        case prompt
        /// The agent's own words.
        case text
        /// A tool the agent ran.
        case tool
        /// Command output: the test run's tail.
        case output
        /// Its thinking, shown folded.
        case thought
        /// Plumbing: model calls, tool results, phase changes.
        case dim
        case success
        case failure
    }

    public let id: String
    public let style: Style
    public let text: String

    public init(id: String, style: Style, text: String) {
        self.id = id; self.style = style; self.text = text
    }
}

/// The task's event log, laid out as a terminal session. Same lines as
/// src/lib/codeTasks.ts's `terminalLines`, so the phone and the desktop show
/// one transcript.
///
/// Everything is in it, plumbing included: the screen is the place to watch
/// the agent work, so a model call or a refused write is a dim line rather
/// than something behind a toggle. What would only be noise — a tool still
/// pending, a write that was allowed — is left out.
public enum CodeTerminal {
    public static func lines(_ events: [CodeTaskEvent]) -> [CodeTerminalLine] {
        var out: [CodeTerminalLine] = []
        for event in events {
            let id = "e\(event.seq)"
            func add(_ style: CodeTerminalLine.Style, _ text: String, _ suffix: String = "") {
                out.append(CodeTerminalLine(id: id + suffix, style: style, text: text))
            }
            let p = event.payload
            switch event.kind {
            case "user":
                add(.prompt, "❯ " + (string(p["text"]) ?? ""))
            case "message":
                let text = (string(p["text"]) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty { add(.text, text) }
            case "thought":
                let text = (string(p["text"]) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty { add(.thought, text) }
            case "tool_call":
                add(.tool, "● " + (string(p["title"]) ?? "tool"))
            case "tool_result":
                switch string(p["status"]) {
                case "failed": add(.failure, "  ⎿ failed")
                case "completed": add(.dim, "  ⎿ done")
                default: break
                }
            case "permission":
                if string(p["decision"]) != "allowed" {
                    add(.dim, "  ⎿ refused: \(string(p["title"]) ?? string(p["kind"]) ?? "a tool") (the plan step is read-only)")
                }
            case "plan":
                guard case let .array(entries)? = p["entries"], !entries.isEmpty else { break }
                add(.dim, entries.map { entry -> String in
                    guard case let .object(e) = entry else { return "  ☐" }
                    let mark = string(e["status"]) == "completed" ? "☒" : string(e["status"]) == "in_progress" ? "◐" : "☐"
                    return "  \(mark) \(string(e["content"]) ?? "")"
                }.joined(separator: "\n"))
            case "proxy":
                let failed = ["stuck", "paused_out"].contains(string(p["action"]) ?? "")
                add(failed ? .failure : .dim, "  · " + event.label.lowercasedFirst)
            case "test":
                let exit = number(p["exit"]).map { Int($0) } ?? -1
                add(exit == 0 ? .success : .failure, exit == 0 ? "✓ tests passed" : "✗ tests failed (exit \(exit))")
                let tail = (string(p["tail"]) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !tail.isEmpty { add(.output, tail, "-tail") }
            case "status":
                if let note = string(p["note"]) {
                    add(.dim, "  ! " + note)
                } else if let error = string(p["error"]) {
                    add(.failure, "✗ " + error)
                } else if let status = string(p["status"]) {
                    add(.dim, "── \(CodeTaskStatus.label(status).lowercased()) ──")
                }
            default:
                add(.dim, event.label)
            }
        }
        return out
    }

    private static func string(_ value: JSONValue?) -> String? {
        if case let .string(s)? = value { return s }
        return nil
    }

    private static func number(_ value: JSONValue?) -> Double? {
        if case let .number(n)? = value { return n }
        return nil
    }
}

public enum CodeDraft {
    /// Dictation adds to what is typed rather than replacing it, so a second
    /// clip carries on the first.
    public static func appending(_ words: String, to draft: String) -> String {
        let words = words.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else { return draft }
        guard let last = draft.last else { return words }
        return draft + (last.isWhitespace ? "" : " ") + words
    }
}

private extension String {
    var lowercasedFirst: String { prefix(1).lowercased() + dropFirst() }
}
