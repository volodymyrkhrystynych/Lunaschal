import Foundation

/// The last inbox page and the messages opened recently, so the tab and what
/// was being read survive a lost connection. Deliberately not a replica: the
/// mailbox is years of mail and the server already holds it.
public final class EmailStore {
    public static let keep = 60
    public let root: URL
    private var inboxFile: URL { root.appendingPathComponent("inbox.json") }
    private var accountsFile: URL { root.appendingPathComponent("accounts.json") }
    private var messages: URL { root.appendingPathComponent("messages", isDirectory: true) }

    public init(root: URL) throws {
        self.root = root
        try FileManager.default.createDirectory(at: root.appendingPathComponent("messages"), withIntermediateDirectories: true)
    }

    public func inbox() -> [EmailMessage] { read(inboxFile) ?? [] }
    public func saveInbox(_ list: [EmailMessage]) throws { try write(list, inboxFile) }
    public func accounts() -> [EmailAccount] { read(accountsFile) ?? [] }
    public func saveAccounts(_ list: [EmailAccount]) throws { try write(list, accountsFile) }

    public func message(_ id: String) -> EmailMessage? {
        guard ULID.isValid(id) else { return nil }
        return read(messages.appendingPathComponent("\(id).json"))
    }

    public func saveMessage(_ message: EmailMessage) throws {
        guard ULID.isValid(message.id) else { throw CaptureError.invalidID }
        try write(message, messages.appendingPathComponent("\(message.id).json"))
        let files = try FileManager.default.contentsOfDirectory(at: messages, includingPropertiesForKeys: [.contentModificationDateKey])
        guard files.count > Self.keep else { return }
        func modified(_ url: URL) -> Date {
            (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
        }
        for old in files.sorted(by: { modified($0) > modified($1) }).dropFirst(Self.keep) {
            try? FileManager.default.removeItem(at: old)
        }
    }

    private func read<T: Decodable>(_ url: URL) -> T? {
        (try? Data(contentsOf: url)).flatMap { try? JSONDecoder().decode(T.self, from: $0) }
    }

    private func write<T: Encodable>(_ value: T, _ url: URL) throws {
        try JSONEncoder().encode(value).write(to: url, options: .atomic)
    }
}
