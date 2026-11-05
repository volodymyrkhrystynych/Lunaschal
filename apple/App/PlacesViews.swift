import LunaschalCore
import SwiftUI

/// Saved places, which of them is Home and Work, their history, and the
/// weekly patterns that follow from it — the web's Settings → Saved Places
/// plus what the trail makes of them. Reads the cached snapshot, so it opens
/// offline; changes go to the server and come back as a fresh snapshot.
struct PlacesScreen: View {
    @ObservedObject var model: CaptureModel
    @State private var editing: PlaceDraft?
    @State private var assigning = false
    @State private var period: PlaceRolePeriod?
    @State private var error: String?

    private var today: String { DayKey.of(Date()) }

    var body: some View {
        List {
            currentSection
            placesSection
            ForEach(PlaceRole.allCases, id: \.self) { role in
                let history = model.places.history(role)
                if !history.isEmpty {
                    Section("\(role.label) history") {
                        ForEach(history) { p in
                            Button { period = p } label: {
                                HStack {
                                    Text(p.placeName).foregroundStyle(.primary)
                                    Spacer()
                                    Text(p.range).font(.footnote).foregroundStyle(.secondary).monospacedDigit()
                                }
                            }
                            .accessibilityIdentifier("place-period-\(p.id)")
                        }
                    }
                }
            }
            PatternsSection(patterns: model.locationPatterns)
        }
        .navigationTitle("Places")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { editing = PlaceDraft() } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Add place")
                    .accessibilityIdentifier("places-add")
            }
        }
        .task { await model.refreshPlaces(); await model.refreshPatterns() }
        .refreshable { await model.refreshPlaces(); await model.refreshPatterns() }
        .sheet(item: $editing) { draft in
            PlaceEditor(model: model, draft: draft) { error = $0 }
        }
        .sheet(isPresented: $assigning) {
            RoleAssigner(model: model, today: today) { error = $0 }
        }
        .sheet(item: $period) { p in
            PeriodEditor(model: model, period: p) { error = $0 }
        }
        .alert("Places", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
            Button("OK") { error = nil }
        } message: { Text(error ?? "") }
    }

    @ViewBuilder private var currentSection: some View {
        Section {
            LabeledContent("Home", value: model.places.home(on: today)?.placeName ?? "Not set")
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("places-home")
            let works = model.places.works(on: today)
            if works.isEmpty {
                LabeledContent("Work", value: "Not set")
            } else {
                ForEach(works) { LabeledContent("Work", value: $0.placeName) }
            }
            Button("Set Home or Work…") { assigning = true }
                .disabled(!model.places.places.contains(where: \.isLocated))
                .accessibilityIdentifier("places-assign")
        } header: {
            Text("Home & Work")
        } footer: {
            Text("Moving? Add the new place and set it as Home from the move date; the old one stays in the history. Editing a place's pin corrects it for all of its history.")
        }
    }

    @ViewBuilder private var placesSection: some View {
        Section("Saved places") {
            if model.places.places.isEmpty {
                Text("No places yet. Add Home, Work, or anywhere you go often.").foregroundStyle(.secondary)
            }
            ForEach(model.places.places) { place in
                Button { editing = PlaceDraft(place) } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(place.name).foregroundStyle(.primary)
                            if let role = model.places.role(of: place.id, on: today) {
                                Text(role.role.label)
                                    .font(.caption.weight(.semibold))
                                    .padding(.horizontal, 6).padding(.vertical, 2)
                                    .background(.tint.opacity(0.15), in: Capsule())
                            }
                        }
                        if !place.isLocated {
                            Text("No pin — can't be Home or Work").font(.caption).foregroundStyle(.secondary)
                        } else if !place.notes.isEmpty {
                            Text(place.notes).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                }
                .accessibilityIdentifier("place-\(place.name)")
            }
            .onDelete { offsets in
                let ids = offsets.map { model.places.places[$0].id }
                Task {
                    for id in ids { if let message = await model.deletePlace(id) { error = message } }
                }
            }
        }
    }
}

/// A place being added or edited.
struct PlaceDraft: Identifiable {
    let id: String
    let isNew: Bool
    var name = ""
    var notes = ""
    var latitude = ""
    var longitude = ""
    var radius = 150

    init() { id = ULID.make(); isNew = true }

    init(_ place: SavedPlace) {
        id = place.id; isNew = false; name = place.name; notes = place.notes; radius = place.radiusM
        latitude = place.latitude.map { String($0) } ?? ""
        longitude = place.longitude.map { String($0) } ?? ""
    }

    var coordinates: (Double, Double)?? {
        let lat = latitude.trimmingCharacters(in: .whitespaces), lon = longitude.trimmingCharacters(in: .whitespaces)
        if lat.isEmpty && lon.isEmpty { return .some(nil) }
        guard let a = Double(lat), let b = Double(lon), abs(a) <= 90, abs(b) <= 180 else { return nil }
        return .some((a, b))
    }
}

private struct PlaceEditor: View {
    @ObservedObject var model: CaptureModel
    @State var draft: PlaceDraft
    let failed: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var saving = false
    @State private var locating = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name (Home, Office, Gym…)", text: $draft.name)
                        .accessibilityIdentifier("place-name")
                    TextField("Notes, address or aliases", text: $draft.notes, axis: .vertical)
                }
                Section {
                    TextField("Latitude", text: $draft.latitude).keyboardType(.numbersAndPunctuation)
                        .accessibilityIdentifier("place-latitude")
                    TextField("Longitude", text: $draft.longitude).keyboardType(.numbersAndPunctuation)
                        .accessibilityIdentifier("place-longitude")
                    Button(locating ? "Locating…" : "Use current location") {
                        locating = true
                        model.location.refresh()
                    }
                    .disabled(locating)
                    Stepper("Radius: \(draft.radius) m", value: $draft.radius, in: 50...2000, step: 50)
                } header: {
                    Text("Pin")
                } footer: {
                    Text("A pin lets the trail, meals and weather match this place. Without one it's only a name for chat.")
                }
            }
            .navigationTitle(draft.isNew ? "New place" : draft.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(saving || draft.name.trimmingCharacters(in: .whitespaces).isEmpty
                                  || draft.coordinates == nil)
                        .accessibilityIdentifier("place-save")
                }
            }
            .onReceive(model.location.$latest.compactMap { $0 }) { fix in
                guard locating else { return }
                locating = false
                draft.latitude = String(format: "%.6f", fix.coordinate.latitude)
                draft.longitude = String(format: "%.6f", fix.coordinate.longitude)
            }
        }
    }

    private func save() {
        guard let coordinates = draft.coordinates else { return }
        saving = true
        let place = SavedPlace(id: draft.id, name: draft.name.trimmingCharacters(in: .whitespaces),
                               notes: draft.notes.trimmingCharacters(in: .whitespaces),
                               latitude: coordinates?.0, longitude: coordinates?.1, radiusM: draft.radius)
        Task {
            let message = await model.savePlace(place, isNew: draft.isNew)
            saving = false
            if let message { failed(message) } else { dismiss() }
        }
    }
}

private struct RoleAssigner: View {
    @ObservedObject var model: CaptureModel
    let today: String
    let failed: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var placeID = ""
    @State private var role: PlaceRole = .home
    @State private var since = DayKey.date(DayKey.of(Date())) ?? Date()
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                Picker("Place", selection: $placeID) {
                    Text("Choose…").tag("")
                    ForEach(model.places.places.filter(\.isLocated)) { Text($0.name).tag($0.id) }
                }
                .accessibilityIdentifier("role-place")
                Picker("Role", selection: $role) {
                    ForEach(PlaceRole.allCases, id: \.self) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                DatePicker("Since", selection: $since, displayedComponents: .date)
                Text(role == .home
                     ? "Your current Home ends the day before this one starts."
                     : "You can have several Works at once, each a different place.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .navigationTitle("Set Home or Work")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(role == .home ? "Set as Home" : "Add as Work") {
                        saving = true
                        Task {
                            let message = await model.assignRole(role, placeID: placeID, since: DayKey.picked(since))
                            saving = false
                            if let message { failed(message) } else { dismiss() }
                        }
                    }
                    .disabled(placeID.isEmpty || saving)
                    .accessibilityIdentifier("role-save")
                }
            }
        }
    }
}

private struct PeriodEditor: View {
    @ObservedObject var model: CaptureModel
    let period: PlaceRolePeriod
    let failed: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var start = Date()
    @State private var ended = false
    @State private var end = Date()
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Place", value: period.placeName)
                    LabeledContent("Role", value: period.role.label)
                }
                Section {
                    DatePicker("From", selection: $start, displayedComponents: .date)
                    Toggle("Ended", isOn: $ended)
                    if ended { DatePicker("Until", selection: $end, in: start..., displayedComponents: .date) }
                } footer: {
                    Text("The period runs up to the day before “Until”.")
                }
                Section {
                    Button("Remove this period", role: .destructive) {
                        Task {
                            if let message = await model.deleteRole(period.id) { failed(message) } else { dismiss() }
                        }
                    }
                }
            }
            .navigationTitle(period.range)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        saving = true
                        Task {
                            let message = await model.updateRole(period.id, startedOn: DayKey.picked(start),
                                                                 endedOn: .some(ended ? DayKey.picked(end) : nil))
                            saving = false
                            if let message { failed(message) } else { dismiss() }
                        }
                    }.disabled(saving)
                }
            }
            .onAppear {
                start = DayKey.date(period.startedOn) ?? Date()
                if let endedOn = period.endedOn, let date = DayKey.date(endedOn) { ended = true; end = date }
            }
        }
    }
}

/// Office days, hours and commute times by week, from the trail.
private struct PatternsSection: View {
    let patterns: LocationPatterns?

    var body: some View {
        if let patterns, patterns.observedDays > 0 {
            Section {
                LabeledContent("Office days",
                               value: "\(patterns.officeDays) of \(patterns.observedDays) recorded days")
                if let avg = patterns.commute.toWork.avgSeconds {
                    LabeledContent("Commute to work", value: "\(Whereabouts.duration(avg)) average")
                }
                if let avg = patterns.commute.toHome.avgSeconds {
                    LabeledContent("Commute home", value: "\(Whereabouts.duration(avg)) average")
                }
                if !patterns.commute.modes.isEmpty {
                    LabeledContent("Usually", value: patterns.commute.modes
                        .sorted { $0.value > $1.value }
                        .map { "\(Whereabouts.modeLabel($0.key)) ×\($0.value)" }
                        .joined(separator: ", "))
                }
                HStack(alignment: .bottom, spacing: 6) {
                    ForEach(patterns.weekdays) { day in
                        VStack(spacing: 4) {
                            let share = day.observedDays == 0 ? 0 : Double(day.officeDays) / Double(day.observedDays)
                            RoundedRectangle(cornerRadius: 3).fill(.tint)
                                .frame(height: max(2, 44 * share))
                                .opacity(day.observedDays == 0 ? 0.2 : 1)
                            Text(day.weekday.prefix(1)).font(.caption2).foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel("\(day.weekday): at work \(day.officeDays) of \(day.observedDays) days")
                    }
                }
                .frame(height: 64)
            } header: {
                Text("Last \(patterns.days / 7) weeks")
            } footer: {
                Text("Only days the phone recorded a trail count; a day with no trail is unknown, not a day at home.")
            }
            Section("By week") {
                ForEach(patterns.weeks) { week in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Week of \(week.weekStart)").font(.subheadline.weight(.semibold))
                        Text(weekLine(week)).font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func weekLine(_ week: LocationPatterns.Week) -> String {
        var parts = ["\(week.officeDays) office day\(week.officeDays == 1 ? "" : "s")"]
        if week.workSeconds > 0 { parts.append("\(Whereabouts.duration(week.workSeconds)) at work") }
        if let a = week.avgArrival, let d = week.avgDeparture { parts.append("\(a)–\(d)") }
        if let c = week.commute.toWork.avgSeconds { parts.append("commute \(Whereabouts.duration(c))") }
        return parts.joined(separator: " · ")
    }
}
