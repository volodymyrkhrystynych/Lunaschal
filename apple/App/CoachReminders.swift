import LunaschalCore
import SwiftUI
import UIKit
import UserNotifications

/// The coach's push notifications on this phone: asking permission, telling
/// the server where to send them, the buttons on each kind, and what a tap or
/// a typed reply does. Off until switched on in Settings — never asked for at
/// first launch. The rules for reading a response are Core's `CoachRoute`.
///
/// The Code page uses the same device token, so this never gives it up: the
/// switch is a flag on the server's device row. `AppDelegate` owns the
/// notification-center delegate and hands over what isn't a Code task.
@MainActor
final class CoachReminders: ObservableObject {
    static let shared = CoachReminders()
    static let enabledKey = "coachRemindersEnabled"
    private static let tokenKey = "coachPushToken"

    /// Debug builds get sandbox device tokens, release builds production
    /// ones; the server sends each to the matching APNs host.
    static var environment: PushEnvironment {
        #if DEBUG
        return .sandbox
        #else
        return .production
        #endif
    }

    @Published private(set) var status = ""
    @Published var enabled = UserDefaults.standard.bool(forKey: CoachReminders.enabledKey)
    private weak var model: CaptureModel?

    /// The server, for the settings screen.
    var api: JournalAPI? { model?.chatAPI() }

    func start(model: CaptureModel?) {
        self.model = model
        UNUserNotificationCenter.current().setNotificationCategories(Self.categories)
        // A token can change between launches; registering is cheap and the
        // server's upsert makes it idempotent.
        if enabled { UIApplication.shared.registerForRemoteNotifications() }
    }

    static var categories: Set<UNNotificationCategory> {
        Set(CoachCategory.allCases.map { category in
            UNNotificationCategory(identifier: category.rawValue, actions: category.actions.map(action),
                                   intentIdentifiers: [], options: [])
        })
    }

    private static func action(_ action: CoachAction) -> UNNotificationAction {
        if action.takesText {
            return UNTextInputNotificationAction(identifier: action.rawValue, title: action.title, options: [],
                                                 textInputButtonTitle: "Send",
                                                 textInputPlaceholder: action.placeholder ?? "")
        }
        return UNNotificationAction(identifier: action.rawValue, title: action.title, options: [])
    }

    func setEnabled(_ on: Bool) async {
        if on {
            let granted = (try? await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .sound, .badge])) ?? false
            guard granted else {
                enabled = false
                status = "Notifications are off for Lunaschal in iOS Settings."
                return
            }
            enabled = true
            UserDefaults.standard.set(true, forKey: Self.enabledKey)
            status = "Registering…"
            UIApplication.shared.registerForRemoteNotifications()
        } else {
            enabled = false
            UserDefaults.standard.set(false, forKey: Self.enabledKey)
            status = "Off. The coach still writes in Chat."
            if let token = UserDefaults.standard.string(forKey: Self.tokenKey) {
                do {
                    try await upload(token: token, coach: false)
                } catch {
                    status = "Couldn't reach your server, so reminders may still arrive."
                }
            }
        }
    }

    func registered(token data: Data) {
        let token = PushToken.hex(data)
        UserDefaults.standard.set(token, forKey: Self.tokenKey)
        // The Code page registers this phone too; that says nothing about reminders.
        guard enabled else { return }
        Task {
            guard model?.chatAPI() != nil else {
                status = "Sign in to receive reminders."
                return
            }
            do {
                try await upload(token: token, coach: true)
                status = "On. Reminders arrive here and in Chat."
            } catch {
                status = "Couldn't reach your server; it will retry next launch."
            }
        }
    }

    private func upload(token: String, coach: Bool) async throws {
        try await model?.chatAPI()?.registerPushDevice(
            token: token, environment: Self.environment, bundleID: Bundle.main.bundleIdentifier,
            appVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String, coach: coach)
    }

    func failed(_ error: Error) {
        guard enabled else { return }
        status = "iOS couldn't register for notifications: \(error.localizedDescription)"
    }

    // MARK: A notification was answered

    nonisolated func handle(_ response: UNNotificationResponse) async {
        let userInfo = response.notification.request.content.userInfo
        let text = (response as? UNTextInputNotificationResponse)?.userText
        let action = response.actionIdentifier
        let content = response.notification.request.content
        await MainActor.run {
            guard let payload = CoachPayload(userInfo: userInfo) else { return }
            let route = CoachRoute.resolve(payload, action: action, text: text, today: DayKey.of(Date()))
            perform(route, content: content)
            if let decision = payload.decisionID, action != UNNotificationDismissActionIdentifier {
                let at = Date()
                Task { try? await self.model?.chatAPI()?.coachResponse(decisionID: decision, action: route.response, at: at) }
            }
        }
    }

    private func perform(_ route: CoachRoute, content: UNNotificationContent) {
        switch route {
        case .openChat: open("chat")
        case .openToday: open("todo")
        case .openDaily, .retype: open("daily")
        case let .tickHabit(id, day):
            model?.queueTodo(.tickTask(id: id, day: day, done: true))
            model?.requestSync()
        case let .logWeight(kg):
            if model?.logWeight(kg) == true { model?.requestSync() } else { open("daily") }
        case let .logCalories(line):
            if model?.logCalories(line.calories, description: line.description) == true { model?.requestSync() }
            else { open("daily") }
        case let .reply(text):
            Task { try? await model?.chatAPI()?.coachReply(id: ULID.make(), text: text) }
        case .snooze:
            // The same reminder again in an hour, from the phone itself, so it
            // works offline and asks nothing of the server.
            let request = UNNotificationRequest(identifier: ULID.make(), content: content,
                                                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 3600, repeats: false))
            UNUserNotificationCenter.current().add(request)
        }
    }

    private func open(_ host: String) {
        guard let url = URL(string: "lunaschal://\(host)") else { return }
        UIApplication.shared.open(url)
    }
}

/// Settings → Coach reminders.
struct CoachReminderSettings: View {
    @ObservedObject var reminders = CoachReminders.shared

    var body: some View {
        Section {
            Toggle("Coach reminders", isOn: Binding(get: { reminders.enabled },
                                                    set: { on in Task { await reminders.setEnabled(on) } }))
                .accessibilityIdentifier("settings-coach-reminders")
            if !reminders.status.isEmpty {
                Text(reminders.status).font(.footnote).foregroundStyle(.secondary)
            }
            NavigationLink { CoachSettingsScreen() } label: {
                Label("Check-ins and reminders", systemImage: "figure.mind.and.body")
            }
            .accessibilityIdentifier("settings-coach")
        } header: {
            Text("Coach")
        } footer: {
            Text("The agent's check-ins and reminders — weigh-in, calories, habits, new goals — as notifications you can answer from the lock screen. What gets sent, and how often, is set on your server.")
        }
    }
}

/// The coach's server-side settings and what reminders have changed so far —
/// the same switches as the web's Settings → Coach, read fresh on each open.
struct CoachSettingsScreen: View {
    @ObservedObject private var reminders = CoachReminders.shared
    @State private var settings: CoachSettings?
    @State private var stats: [CoachStat] = []
    @State private var problem: String?

    var body: some View {
        Form {
            if let settings {
                Section {
                    Toggle("Evening check-in", isOn: bind(\.checkinEnabled, "checkinEnabled"))
                    if settings.checkinEnabled {
                        Picker("At", selection: bind(\.checkinHour, "checkinHour")) {
                            ForEach(0..<24, id: \.self) { Text(String(format: "%02d:00", $0)).tag($0) }
                        }
                    }
                } footer: {
                    Text("One message asking how the day went. Tell it about a busy day and yesterday's review and your streaks take it into account.")
                }
                Section {
                    Toggle("Reminders that learn", isOn: bind(\.enabled, "enabled"))
                        .accessibilityIdentifier("coach-enabled")
                    Toggle("Weigh-in", isOn: behavior(\.weight, "weight"))
                    Toggle("Calories", isOn: behavior(\.calories, "calories"))
                    Toggle("Habits", isOn: behavior(\.habits, "habits"))
                    Stepper("At most \(settings.dailyCap) a day", value: bind(\.dailyCap, "dailyCap"), in: 1...6)
                    Picker("Quiet from", selection: bind(\.quietStart, "quietStart")) {
                        ForEach(0..<24, id: \.self) { Text(String(format: "%02d:00", $0)).tag($0) }
                    }
                    Picker("Until", selection: bind(\.quietEnd, "quietEnd")) {
                        ForEach(0..<24, id: \.self) { Text(String(format: "%02d:00", $0)).tag($0) }
                    }
                } footer: {
                    Text("Each reminder tries a framing and a time; a quarter are held back so the coach can tell what a reminder changed. Nothing is sent for something already logged, and a habit you keep doing on your own stops being prompted.")
                }
                if !stats.isEmpty {
                    Section("What reminders changed") {
                        ForEach(stats) { stat in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(stat.label)
                                Text("Done \(percent(stat.doneWhenSent)) with a reminder (n=\(stat.sent)), \(percent(stat.doneWhenHeld)) without (n=\(stat.held))")
                                    .font(.caption).foregroundStyle(.secondary)
                                if let lead = stat.leading {
                                    Text("Leaning toward: \(lead.arm == "none" ? "not reminding" : lead.arm)")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            } else if let problem {
                Text(problem).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Coach")
        .task { await load() }
    }

    private func load() async {
        guard let api = reminders.api else { problem = "Sign in to change the coach's settings."; return }
        do {
            settings = try await api.coachSettings()
            stats = (try? await api.coachStats()) ?? []
        } catch {
            problem = "Couldn't reach your server."
        }
    }

    private func save(_ changes: [String: Any]) {
        Task {
            guard let api = reminders.api else { return }
            if let updated = try? await api.updateCoachSettings(changes) { settings = updated }
        }
    }

    private func bind<T>(_ path: WritableKeyPath<CoachSettings, T>, _ key: String) -> Binding<T> {
        Binding(get: { settings![keyPath: path] }, set: { value in
            settings?[keyPath: path] = value
            save([key: value])
        })
    }

    private func behavior(_ path: WritableKeyPath<CoachSettings.Behaviors, Bool>, _ key: String) -> Binding<Bool> {
        Binding(get: { settings!.behaviors[keyPath: path] }, set: { value in
            settings?.behaviors[keyPath: path] = value
            save(["behaviors": [key: value]])
        })
    }

    private func percent(_ value: Double?) -> String {
        value.map { "\(Int(($0 * 100).rounded()))%" } ?? "—"
    }
}
