import AuthenticationServices
import LunaschalCore
import SwiftUI
import UIKit
import UserNotifications

/// The Email tab: a view over the server's mirrored mailbox, the state of each
/// account's sign-in, and this phone's email notification switch. The last
/// page and the messages opened are kept (`EmailStore`), so it opens offline.
@MainActor
final class EmailModel: NSObject, ObservableObject {
    static let notificationsKey = "emailNotificationsEnabled"
    private static let tokenKey = "emailPushToken"

    @Published private(set) var accounts: [EmailAccount]
    @Published private(set) var messages: [EmailMessage]
    @Published var filter = EmailFilter.all
    @Published var search = ""
    @Published private(set) var loading = false
    @Published private(set) var hasMore = false
    @Published private(set) var problem: String?
    /// How the last sign-in or account action ended, when it didn't work.
    @Published var accountProblem: String?
    @Published private(set) var signingIn = false
    @Published private(set) var notifications = UserDefaults.standard.bool(forKey: EmailModel.notificationsKey)
    @Published private(set) var notificationStatus = ""

    let capture: CaptureModel
    private let fixture = EmailFixture.isOn
    private var loadedOffset = 0
    private var loadNumber = 0
    private var authentication: ASWebAuthenticationSession?

    init(capture: CaptureModel) {
        self.capture = capture
        accounts = capture.emailStore.accounts()
        messages = capture.emailStore.inbox()
        super.init()
        // A token can change between launches, and registering is idempotent.
        if notifications { UIApplication.shared.registerForRemoteNotifications() }
    }

    var needsReconnect: Bool { lost != nil }
    var lost: EmailAccount? { accounts.first(where: \.needsReconnect) }

    // MARK: Reading

    /// What the cache can show of the current filter, for an offline launch.
    private var cached: [EmailMessage] {
        capture.emailStore.inbox().filter { message in
            switch filter {
            case .all: true
            case .important: message.isImportant
            case .category(let category): message.category == category.rawValue
            }
        }.filter { search.isEmpty || "\($0.from) \($0.title) \($0.preview)".localizedCaseInsensitiveContains(search) }
    }

    func refreshAccounts() async {
        guard !fixture, let api = capture.chatAPI(), let fresh = try? await api.emailAccounts() else { return }
        accounts = fresh
        try? capture.emailStore.saveAccounts(fresh)
    }

    func load() async {
        loadNumber += 1
        let number = loadNumber
        if fixture { messages = cached; return }
        guard let api = capture.chatAPI() else {
            messages = cached
            problem = "Sign in to your server in Settings to load mail."
            return
        }
        loading = true
        defer { if number == loadNumber { loading = false } }
        do {
            let page = try await api.emails(filter, search: search)
            // A slower answer to an older filter must not replace this one's.
            guard number == loadNumber else { return }
            messages = page
            loadedOffset = page.count
            hasMore = page.count == JournalAPI.emailPage
            problem = nil
            if filter == .all, search.isEmpty { try? capture.emailStore.saveInbox(page) }
        } catch {
            guard number == loadNumber, !Self.isCancelled(error) else { return }
            messages = cached
            hasMore = false
            problem = "Can't reach your server. Showing the last mail loaded."
        }
        await refreshAccounts()
    }

    func loadMore() async {
        guard hasMore, !loading, let api = capture.chatAPI() else { return }
        let number = loadNumber
        loading = true
        defer { if number == loadNumber { loading = false } }
        guard let page = try? await api.emails(filter, search: search, offset: loadedOffset),
              number == loadNumber else { return }
        loadedOffset += page.count
        hasMore = page.count == JournalAPI.emailPage
        // Mail that arrived meanwhile shifts the pages, so one can repeat.
        let have = Set(messages.map(\.id))
        messages += page.filter { !have.contains($0.id) }
    }

    func message(_ id: String) async throws -> EmailMessage {
        if let kept = capture.emailStore.message(id), fixture || capture.chatAPI() == nil { return kept }
        do {
            guard let api = capture.chatAPI() else { throw URLError(.notConnectedToInternet) }
            let message = try await api.email(id)
            try? capture.emailStore.saveMessage(message)
            return message
        } catch {
            if let kept = capture.emailStore.message(id) { return kept }
            throw error
        }
    }

    /// Asks the server to check the providers, then reloads.
    func syncNow() async {
        if !fixture, let api = capture.chatAPI() { try? await api.syncEmail() }
        await load()
    }

    // MARK: The connection

    /// Signs in on the provider's own page in a browser sheet, which keeps the
    /// Google session from last time. The server redirects back here.
    func reconnect(_ provider: String) async {
        accountProblem = nil
        guard let api = capture.chatAPI() else {
            accountProblem = "Sign in to your server in Settings first."
            return
        }
        guard !signingIn else { return }
        signingIn = true
        defer { signingIn = false }
        do {
            let url = try await api.startEmailSignIn(provider: provider)
            guard let ended = try await browse(url) else { return }
            switch EmailSignIn.parse(ended) {
            case .connected?: await load()
            case .failed(let why)?: accountProblem = why
            case nil: accountProblem = "The sign-in didn't finish. Try again."
            }
        } catch {
            if !Self.isCancelled(error) { accountProblem = error.localizedDescription }
        }
        await refreshAccounts()
    }

    /// The URL the sheet ended on, or nil when it was closed.
    private func browse(_ url: URL) async throws -> URL? {
        defer { authentication = nil }
        return try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(url: url, callback: .customScheme("lunaschal")) { url, error in
                if let url { continuation.resume(returning: url) }
                else if (error as? ASWebAuthenticationSessionError)?.code == .canceledLogin { continuation.resume(returning: nil) }
                else { continuation.resume(throwing: error ?? URLError(.unknown)) }
            }
            session.presentationContextProvider = self
            authentication = session
            // A false start still calls the handler with the reason.
            session.start()
        }
    }

    func connectIMAP(host: String, port: Int, username: String, password: String, address: String) async -> Bool {
        accountProblem = nil
        guard let api = capture.chatAPI() else { accountProblem = "Sign in to your server in Settings first."; return false }
        do {
            try await api.connectIMAP(host: host, port: port, username: username, password: password, address: address)
            await load()
            return true
        } catch {
            accountProblem = error.localizedDescription
            return false
        }
    }

    func disconnect(_ provider: String) async {
        accountProblem = nil
        do { try await capture.chatAPI()?.disconnectEmail(provider: provider) }
        catch { accountProblem = error.localizedDescription }
        await refreshAccounts()
    }

    // MARK: Notifications

    func setNotifications(_ on: Bool) async {
        if on {
            let granted = (try? await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])) ?? false
            guard granted else {
                notificationStatus = "Notifications are off for Lunaschal in iOS Settings."
                return
            }
        }
        notifications = on
        UserDefaults.standard.set(on, forKey: Self.notificationsKey)
        if let token = PushRouter.shared.deviceToken ?? UserDefaults.standard.string(forKey: Self.tokenKey) {
            await send(token: token)
        } else if on {
            notificationStatus = "Registering…"
            UIApplication.shared.registerForRemoteNotifications()
        }
    }

    /// iOS handed over this phone's token; says nothing while the switch is off.
    func upload(token: String) async {
        UserDefaults.standard.set(token, forKey: Self.tokenKey)
        if notifications { await send(token: token) }
    }

    private func send(token: String) async {
        do {
            guard let api = capture.chatAPI() else { throw URLError(.userAuthenticationRequired) }
            try await api.registerEmailPush(token: token, environment: CoachReminders.environment, on: notifications)
            notificationStatus = notifications ? "On. They also appear in Chat." : "Off. Email still appears in Chat."
        } catch {
            notificationStatus = notifications ? "Couldn't reach your server; it will retry next launch."
                : "Couldn't reach your server, so notifications may still arrive."
        }
    }

    private static func isCancelled(_ error: Error) -> Bool {
        Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled
    }
}

extension EmailModel: ASWebAuthenticationPresentationContextProviding {
    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        MainActor.assumeIsolated {
            let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            return scenes.flatMap(\.windows).first(where: \.isKeyWindow) ?? ASPresentationAnchor()
        }
    }
}
