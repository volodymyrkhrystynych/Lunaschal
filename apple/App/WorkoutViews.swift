import SwiftUI
import LunaschalCore

/// The Capture tab's Workout page: the desktop's workout log (Lifestyle →
/// WorkoutLog.tsx) on the phone. One set or activity per line, a pill for each
/// recent exercise so "20, 10" means the selected one, and the last few
/// workouts underneath to rate afterwards. Lines are checked here, saved on
/// the device and uploaded in order with the time they were logged, so sets
/// done offline still group into the workout they belong to.
struct WorkoutView: View {
    @ObservedObject var model: CaptureModel
    // Kept across launches, like the desktop's workout draft.
    @AppStorage("workoutDraft") private var text = ""
    @AppStorage("workoutSelected") private var selected = ""
    @State private var error: String?
    @State private var rating: WorkoutSession?
    @FocusState private var typing: Bool

    /// Recent exercises, with anything queued here but not yet on the server first.
    private var pills: [RecentExercise] {
        var recent = model.recentExercises
        for item in model.workoutLogs.reversed() {
            guard let name = try? WorkoutEntry.parse(item.text, selected: item.exercise).name,
                  !recent.contains(where: { $0.name == name }) else { continue }
            recent.insert(RecentExercise(name: name, displayName: name.capitalized), at: 0)
        }
        return WorkoutLabels.pills(recent)
    }
    private var active: String? { selected.isEmpty ? pills.first?.name : selected }
    private var waiting: [WorkoutLog] { model.workoutLogs }

    var body: some View {
        Form {
            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack {
                        ForEach(pills) { pill in
                            Button(pill.displayName) { selected = pill.name; typing = true }
                                .buttonStyle(.bordered)
                                .tint(active == pill.name ? .accentColor : .secondary)
                                .accessibilityAddTraits(active == pill.name ? .isSelected : [])
                        }
                    }
                }
                .accessibilityLabel("Recent exercises")
                TextField("bicep curls 20, 10", text: $text)
                    .focused($typing)
                    .submitLabel(.done)
                    .onSubmit(submit)
                    .onChange(of: text) { _, _ in error = nil }
                    .accessibilityLabel("Exercise entry")
                if let error { Text(error).font(.footnote).foregroundStyle(.red) }
                Button("Log set / activity", action: submit)
                    .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
            } footer: {
                Text((active.map { name in "Selected: \(pills.first { $0.name == name }?.displayName ?? name). " }
                      ?? "Name your first exercise. ")
                     + "Weight in lb, reps: 20, 10 · Bodyweight: 10 · Walking / cycling: minutes. "
                     + "Sets join one workout until an hour passes without a set. Add intensity and location below afterward.")
            }
            if !waiting.isEmpty {
                Section("Saved on this device") {
                    ForEach(waiting) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.exercise.map { "\($0) · \(item.text)" } ?? item.text)
                            Text(item.state == .failed ? (item.lastError ?? "Not accepted by the server")
                                 : "Waiting to sync")
                                .font(.caption).foregroundStyle(item.state == .failed ? .red : .secondary)
                        }
                        .swipeActions {
                            if item.state == .failed { Button("Remove", role: .destructive) { model.discard(item) } }
                        }
                    }
                }
            }
            if !model.recentWorkouts.isEmpty {
                Section("Recent") {
                    ForEach(model.recentWorkouts) { session in
                        SessionRow(session: session) { rating = session }
                    }
                }
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .refreshable { model.requestSync(manual: true) }
        .onAppear { model.requestSync() }
        .sheet(item: $rating) { session in
            RateWorkout(model: model, session: session)
        }
    }

    private func submit() {
        guard !text.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        do {
            _ = try WorkoutEntry.parse(text, selected: active)
        } catch {
            self.error = error.localizedDescription
            return
        }
        guard let entry = model.logWorkout(text, selected: active) else { return }
        text = ""
        selected = entry.name
        typing = true
    }
}

private struct SessionRow: View {
    let session: WorkoutSession
    let rate: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("\(session.date) · \(WorkoutLabels.location(session.locationType))").font(.subheadline)
                Spacer()
                Button("Rate / location", action: rate).font(.caption).buttonStyle(.borderless)
            }
            let meta = [session.durationMinutes.map { "\($0) min" },
                        session.intensityRating.map { "\(String(repeating: "★", count: $0)) \(WorkoutLabels.intensity[$0] ?? "")" }]
                .compactMap { $0 }
            if !meta.isEmpty { Text(meta.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary) }
            ForEach(session.exercises) { exercise in
                Text("\(exercise.displayName)  ").font(.callout)
                    + Text(WorkoutLabels.sets(exercise.sets)).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// The desktop's "Rate / location": where it was, and how hard, from the five
/// written meanings rather than bare stars.
private struct RateWorkout: View {
    @ObservedObject var model: CaptureModel
    let session: WorkoutSession
    @Environment(\.dismiss) private var dismiss
    @State private var location = ""
    @State private var intensity = 0
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                if session.captureKind != "outdoor" {
                    Picker("Location", selection: $location) {
                        Text("Choose location").tag("")
                        ForEach(WorkoutLabels.locations, id: \.id) { Text($0.label).tag($0.id) }
                    }
                }
                Picker("Intensity", selection: $intensity) {
                    Text("Not rated").tag(0)
                    ForEach(1...5, id: \.self) { stars in
                        Text("\(String(repeating: "★", count: stars)) \(WorkoutLabels.intensity[stars] ?? "")").tag(stars)
                    }
                }
                .pickerStyle(.inline)
            }
            .navigationTitle(session.date)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save details") {
                        saving = true
                        Task {
                            let saved = await model.updateWorkout(session.id, location: location.isEmpty ? nil : location,
                                                                  intensity: intensity == 0 ? nil : intensity)
                            saving = false
                            if saved { dismiss() }
                        }
                    }
                    .disabled(saving || (location.isEmpty && intensity == 0))
                }
            }
            .onAppear {
                location = session.locationType == "unassigned" ? "" : session.locationType
                intensity = session.intensityRating ?? 0
            }
        }
    }
}
