import LunaschalCore
import SwiftUI
import UIKit
import WebKit

/// The Email tab: the inbox, with the connection's state above it.
struct EmailInboxView: View {
    @ObservedObject var email: EmailModel

    private struct Query: Equatable { var filter: EmailFilter; var search: String }

    var body: some View {
        List {
            if let lost = email.lost { ReconnectBanner(email: email, account: lost) }
            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(EmailFilter.choices, id: \.self) { choice in
                            let chosen = email.filter == choice
                            Button(choice.label) { email.filter = choice }
                                .buttonStyle(.bordered).buttonBorderShape(.capsule).controlSize(.small)
                                .tint(chosen ? .accentColor : .secondary)
                                .accessibilityAddTraits(chosen ? .isSelected : [])
                                .accessibilityIdentifier("email-filter-\(choice.label)")
                        }
                    }
                    .padding(.horizontal)
                }
                .listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
            }
            if let problem = email.problem {
                Text(problem).font(.footnote).foregroundStyle(.secondary)
            }
            ForEach(EmailDays.group(email.messages), id: \.title) { day in
                Section(day.title) {
                    ForEach(day.messages) { message in
                        NavigationLink { EmailMessageView(email: email, id: message.id) } label: { EmailRow(message: message) }
                            .accessibilityIdentifier("email-row-\(message.id)")
                    }
                }
            }
            if email.hasMore {
                ProgressView().frame(maxWidth: .infinity).task { await email.loadMore() }
                    // A new identity per page, so the next one is asked for too.
                    .id(email.messages.count)
            } else if email.messages.isEmpty && !email.loading {
                Text(email.accounts.isEmpty && email.problem == nil ? "No email account is connected yet." : "No mail here.")
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Email")
        .searchable(text: $email.search, prompt: "Search mail")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                NavigationLink { EmailAccountsView(email: email) } label: { Label("Accounts", systemImage: "person.crop.circle") }
                    .accessibilityIdentifier("email-accounts")
            }
        }
        .refreshable { await email.syncNow() }
        .task(id: Query(filter: email.filter, search: email.search)) {
            // Typing: wait for a pause rather than searching every letter.
            if !email.search.isEmpty { do { try await Task.sleep(for: .milliseconds(350)) } catch { return } }
            await email.load()
        }
    }
}

private struct EmailRow: View {
    let message: EmailMessage

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(message.from).font(.subheadline.weight(.semibold)).lineLimit(1)
                Spacer()
                if let date = message.date {
                    Text(date, style: .time).font(.caption).foregroundStyle(.secondary)
                }
            }
            Text(message.title).font(.subheadline).lineLimit(2)
            if !message.preview.isEmpty {
                Text(message.preview).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            if message.isImportant {
                Label(message.reason?.label ?? "Important", systemImage: "exclamationmark.circle.fill")
                    .font(.caption.weight(.medium)).foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct ReconnectBanner: View {
    @ObservedObject var email: EmailModel
    let account: EmailAccount

    var body: some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                Label("\(account.label) is disconnected", systemImage: "exclamationmark.triangle.fill")
                    .font(.headline).foregroundStyle(.red)
                    .accessibilityIdentifier("email-disconnected")
                Text("\(account.emailAddress) needs you to sign in again. Mail that arrived meanwhile is fetched afterwards.")
                    .font(.footnote)
                EmailReconnectButton(email: email, account: account).buttonStyle(.borderedProminent)
                if let problem = email.accountProblem { Text(problem).font(.footnote).foregroundStyle(.red) }
            }
            .padding(.vertical, 4)
        }
    }
}

/// Signing in again: the provider's page, or for IMAP the password form.
private struct EmailReconnectButton: View {
    @ObservedObject var email: EmailModel
    let account: EmailAccount
    @State private var showIMAP = false

    var body: some View {
        Button {
            if account.usesOAuth { Task { await email.reconnect(account.provider) } } else { showIMAP = true }
        } label: {
            if email.signingIn { ProgressView() } else { Text(account.connected ? "Renew sign-in" : "Reconnect") }
        }
        .disabled(email.signingIn)
        .accessibilityIdentifier("email-reconnect-\(account.provider)")
        .sheet(isPresented: $showIMAP) { IMAPForm(email: email, address: account.emailAddress) }
    }
}

struct EmailAccountsView: View {
    @ObservedObject var email: EmailModel
    @State private var disconnecting: EmailAccount?
    @State private var addingIMAP = false

    private var missing: [String] { ["gmail", "outlook"].filter { id in !email.accounts.contains { $0.provider == id } } }

    var body: some View {
        Form {
            ForEach(email.accounts) { account in
                let health = account.health()
                Section(account.label) {
                    LabeledContent(account.emailAddress) {
                        Text(health.label).foregroundStyle(health == .needsReconnect ? .red : .secondary)
                    }
                    if let synced = account.lastSyncedAt {
                        LabeledContent("Last synced", value: Date(timeIntervalSince1970: TimeInterval(synced))
                            .formatted(date: .abbreviated, time: .shortened))
                    }
                    if let error = account.lastSyncError {
                        Text(error).font(.footnote).foregroundStyle(.secondary)
                    }
                    EmailReconnectButton(email: email, account: account)
                    if account.syncEnabled {
                        Button("Disconnect", role: .destructive) { disconnecting = account }
                    }
                }
            }
            Section {
                ForEach(missing, id: \.self) { provider in
                    Button("Connect \(EmailAccount.label(provider))") { Task { await email.reconnect(provider) } }
                        .disabled(email.signingIn)
                }
                if !email.accounts.contains(where: { $0.provider == "imap" }) {
                    Button("Connect another account (IMAP)") { addingIMAP = true }
                }
                Button("Sync now") { Task { await email.syncNow() } }.disabled(email.loading)
            } footer: {
                Text("Google and Microsoft sign-in use the OAuth client set up in Settings on the desktop. Renewing before a sign-in ends restarts its time.")
            }
            if let problem = email.accountProblem { Text(problem).foregroundStyle(.red) }
        }
        .navigationTitle("Email accounts")
        .task { await email.refreshAccounts() }
        .sheet(isPresented: $addingIMAP) { IMAPForm(email: email, address: "") }
        .confirmationDialog("Disconnect \(disconnecting?.emailAddress ?? "")?",
                            isPresented: Binding(get: { disconnecting != nil }, set: { if !$0 { disconnecting = nil } }),
                            titleVisibility: .visible, presenting: disconnecting) { account in
            Button("Disconnect", role: .destructive) { Task { await email.disconnect(account.provider) } }
        } message: { _ in Text("Mail already on your server stays. New mail stops arriving.") }
    }
}

private struct IMAPForm: View {
    @ObservedObject var email: EmailModel
    @State var address: String
    @State private var host = ""
    @State private var port = "993"
    @State private var username = ""
    @State private var password = ""
    @State private var working = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Group {
                    TextField("Email address", text: $address).keyboardType(.emailAddress)
                    TextField("IMAP server", text: $host).keyboardType(.URL)
                    TextField("Port", text: $port).keyboardType(.numberPad)
                    TextField("Username (the address if empty)", text: $username)
                }
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Password or app password", text: $password)
                if let problem = email.accountProblem { Text(problem).foregroundStyle(.red) }
            }
            .navigationTitle("IMAP account")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Connect") {
                        working = true
                        Task {
                            let done = await email.connectIMAP(host: host, port: Int(port) ?? 993,
                                                               username: username.isEmpty ? address : username,
                                                               password: password, address: address)
                            working = false
                            if done { dismiss() }
                        }
                    }
                    .disabled(working || address.isEmpty || host.isEmpty || password.isEmpty)
                }
            }
        }
    }
}

struct EmailMessageView: View {
    @ObservedObject var email: EmailModel
    let id: String
    @State private var message: EmailMessage?
    @State private var problem: String?
    @State private var images = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let message {
                header(message)
                Divider()
                if let html = message.bodyHtml, !html.isEmpty {
                    let count = EmailHTML.imageCount(html)
                    if count > 0, !images {
                        HStack {
                            Text("\(count) image\(count == 1 ? "" : "s") not shown").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button("Load images") { images = true }.font(.caption)
                                .accessibilityIdentifier("email-load-images")
                        }
                        .padding(.horizontal).padding(.vertical, 6)
                    }
                    EmailBodyView(document: EmailHTML.document(html, images: images), api: email.capture.chatAPI())
                        .ignoresSafeArea(edges: .bottom)
                        .accessibilityIdentifier("email-body")
                } else {
                    ScrollView {
                        Text(message.bodyText.flatMap { $0.isEmpty ? nil : $0 } ?? "(no text)")
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding()
                    }
                }
            } else if let problem {
                ContentUnavailableView("Couldn't open this email", systemImage: "envelope", description: Text(problem))
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .task(id: id) {
            do { message = try await email.message(id) }
            catch { if !Task.isCancelled { problem = error.localizedDescription } }
        }
    }

    private func header(_ message: EmailMessage) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(message.title).font(.headline).textSelection(.enabled)
            // The address too: a display name is whatever the sender chose.
            Text(message.senderEmail.map { $0 == message.from ? $0 : "\(message.from) <\($0)>" } ?? message.from)
                .font(.subheadline).foregroundStyle(.secondary).textSelection(.enabled)
            if let date = message.date {
                Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
            }
            if message.isImportant {
                Label(message.reason?.label ?? "Important", systemImage: "exclamationmark.circle.fill")
                    .font(.caption.weight(.medium)).foregroundStyle(.orange)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
    }
}

/// Sanitized mail in its own box: no scripts, no network. Images come through
/// the app, which holds the session; links open in the browser.
private struct EmailBodyView: UIViewRepresentable {
    let document: String
    let api: JournalAPI?

    func makeCoordinator() -> Coordinator { Coordinator(api: api) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.setURLSchemeHandler(context.coordinator, forURLScheme: EmailHTML.imageScheme)
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        guard context.coordinator.shown != document else { return }
        context.coordinator.shown = document
        view.loadHTMLString(document, baseURL: nil)
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKURLSchemeHandler {
        var shown: String?
        private let api: JournalAPI?
        private var running: [ObjectIdentifier: Task<Void, Never>] = [:]

        init(api: JournalAPI?) { self.api = api }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            let url = action.request.url
            if action.navigationType == .linkActivated, let url, ["http", "https", "mailto"].contains(url.scheme?.lowercased()) {
                UIApplication.shared.open(url)
            }
            decisionHandler(url?.scheme == "about" && action.navigationType != .linkActivated ? .allow : .cancel)
        }

        func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
            guard let url = task.request.url, let hash = EmailHTML.imageHash(url), let api else {
                task.didFailWithError(URLError(.badURL))
                return
            }
            let key = ObjectIdentifier(task)
            running[key] = Task {
                let result: Result<(data: Data, type: String), Error>
                do { result = .success(try await api.emailImage(hash)) } catch { result = .failure(error) }
                // A stopped task must never be answered: WebKit raises.
                guard running.removeValue(forKey: key) != nil else { return }
                switch result {
                case .success(let image):
                    task.didReceive(URLResponse(url: url, mimeType: image.type,
                                                expectedContentLength: image.data.count, textEncodingName: nil))
                    task.didReceive(image.data)
                    task.didFinish()
                case .failure(let error): task.didFailWithError(error)
                }
            }
        }

        func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
            running.removeValue(forKey: ObjectIdentifier(task))?.cancel()
        }
    }
}

/// Where a notification or a chat card leads, over whatever was open.
struct EmailRouteSheet: View {
    @ObservedObject var email: EmailModel
    let route: EmailRoute
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                switch route {
                case .inbox: EmailInboxView(email: email)
                case .reconnect: EmailAccountsView(email: email)
                case .message(let id): EmailMessageView(email: email, id: id)
                }
            }
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } } }
        }
    }
}

/// The agent's message about mail, in Chat: a row per email, or the way back in.
struct EmailCard: View {
    let notice: EmailNotice
    @Environment(\.openURL) private var openURL

    var body: some View {
        switch notice {
        case .important(let items):
            ForEach(items) { item in
                Button { openURL(EmailRoute.message(item.id).url) } label: {
                    HStack {
                        Image(systemName: "envelope")
                        VStack(alignment: .leading) {
                            Text(item.sender).font(.subheadline.weight(.semibold)).lineLimit(1)
                            Text(item.subject).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer(minLength: 8)
                        if let reason = item.reason { Text(reason.label).font(.caption2).foregroundStyle(.orange) }
                    }
                    .padding(10)
                    .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("email-card-\(item.id)")
            }
        case .disconnected(let provider, _):
            Button("Reconnect \(EmailAccount.label(provider))", systemImage: "arrow.clockwise") {
                openURL(EmailRoute.reconnect.url)
            }
            .font(.subheadline)
            .accessibilityIdentifier("email-card-reconnect")
        }
    }
}

/// Settings → Email notifications: a lost connection, and mail worth a look.
struct EmailNotificationSettings: View {
    @ObservedObject var email: EmailModel

    var body: some View {
        Section {
            Toggle("Email notifications", isOn: Binding(get: { email.notifications },
                                                        set: { on in Task { await email.setNotifications(on) } }))
                .accessibilityIdentifier("settings-email-notifications")
            if !email.notificationStatus.isEmpty {
                Text(email.notificationStatus).font(.footnote).foregroundStyle(.secondary)
            }
            NavigationLink { EmailAccountsView(email: email) } label: { Label("Email accounts", systemImage: "envelope") }
                .accessibilityIdentifier("settings-email-accounts")
        } header: {
            Text("Email")
        } footer: {
            Text("When a mail account disconnects and needs you to sign in again, and when new mail looks important. Both also appear in Chat.")
        }
    }
}
