import SwiftUI
import LunaschalCore

/// Settings → Coach → Gaming lock: the switch, the weekdays, where today
/// stands, and the override. The normal way to unlock is to tell the chat
/// you're done with work; the override is deliberately a typed reason, and
/// it shows up in tomorrow's review.
struct GamingLockScreen: View {
    @ObservedObject private var reminders = CoachReminders.shared
    @State private var status: GamingStatus?
    @State private var problem: String?
    @State private var overriding = false
    @State private var reason = ""
    @State private var pauseInference = true
    @State private var busy = false
    /// Why the last change or override didn't go through; cleared on the next try.
    @State private var failure: String?

    var body: some View {
        Form {
            if let status {
                Section {
                    Toggle("Lock games on workdays", isOn: Binding(
                        get: { status.settings.enabled },
                        set: { on in save(["enabled": on]) }))
                        .accessibilityIdentifier("gaming-lock-enabled")
                    if status.settings.enabled {
                        HStack(spacing: 6) {
                            ForEach(0..<7, id: \.self) { day in
                                let on = status.settings.days.contains(day)
                                Button(GamingStatus.weekdayLabels[day]) { toggle(day) }
                                    .buttonStyle(.bordered).tint(on ? .accentColor : .secondary)
                                    .font(.caption)
                                    .accessibilityAddTraits(on ? .isSelected : [])
                            }
                        }
                    }
                } footer: {
                    Text("On these days your server closes Steam, Battle.net, Lutris and Heroic until you tell the chat you're done with work. It goes over the day with you, then offers to unlock.")
                }
                Section("Today") {
                    Text(status.summary()).accessibilityIdentifier("gaming-lock-status")
                    if let failure, !overriding {
                        Text(failure).foregroundStyle(.red).accessibilityIdentifier("gaming-lock-failure")
                    }
                    if status.locked {
                        Button("Unlock without the review…") { overriding = true }
                            .foregroundStyle(.red)
                    }
                }
            } else if let problem {
                Text(problem).foregroundStyle(.secondary)
            } else {
                ProgressView()
            }
        }
        .navigationTitle("Gaming lock")
        .task { await load() }
        .sheet(isPresented: $overriding) { overrideSheet }
    }

    private var overrideSheet: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Why now, without closing out work?", text: $reason, axis: .vertical)
                        .lineLimit(2...5)
                    Toggle("Also pause GPU inference", isOn: $pauseInference)
                    if let failure {
                        Text(failure).foregroundStyle(.red).accessibilityIdentifier("gaming-override-failure")
                    }
                } footer: {
                    Text("This counts as a missed item in today's review, and the reason is shown with it.")
                }
            }
            .navigationTitle("Unlock anyway")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { overriding = false; failure = nil }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Unlock") { submitOverride() }
                        .disabled(busy || !GamingStatus.acceptableReason(reason))
                }
            }
        }
    }

    private func load() async {
        guard let api = reminders.api else { problem = "Sign in to change the gaming lock."; return }
        do {
            status = try await api.gamingStatus()
        } catch {
            problem = "Couldn't reach your server."
        }
    }

    private func toggle(_ day: Int) {
        guard var days = status?.settings.days else { return }
        if let index = days.firstIndex(of: day) { days.remove(at: index) } else { days.append(day) }
        save(["days": days.sorted()])
    }

    private func save(_ changes: [String: Any]) {
        failure = nil
        Task {
            guard let api = reminders.api else { failure = "Sign in to change the gaming lock."; return }
            do {
                status = try await api.updateGamingSettings(changes)
            } catch {
                failure = Self.describe(error)
            }
        }
    }

    private func submitOverride() {
        busy = true
        failure = nil
        Task {
            defer { busy = false }
            guard let api = reminders.api else { failure = "Sign in to change the gaming lock."; return }
            do {
                status = try await api.overrideGamingLock(reason: reason, pauseInference: pauseInference)
                overriding = false
                reason = ""
            } catch {
                failure = Self.describe(error)
            }
        }
    }

    /// The server's refusal in its own words; anything else is the network.
    private static func describe(_ error: Error) -> String {
        if let refusal = error as? GamingLockRefusal { return refusal.message }
        if error is HTTPFailure { return error.localizedDescription }
        return "Couldn't reach your server."
    }
}
