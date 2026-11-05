import Foundation
import LunaschalCore

/// Saved places, Home/Work, the Journal's day cards and the weekly patterns.
/// All of it is the server's (backend/place_roles.py, backend/location/), so
/// it is fetched on a full sync and on demand, and cached here so the Places
/// screen, the geofences and the feed all work offline. Edits need the
/// server: a role change is checked against every other period there, and a
/// guess made offline could only be wrong in ways the user would not see.
extension CaptureModel {
    // Not `.json`: the capture store reads every .json in its root as a capture.
    private var placesCache: PlacesCache { PlacesCache(url: store.root.appendingPathComponent("places.cache")) }
    private var locationDaysURL: URL { store.root.appendingPathComponent("location-days.cache") }
    private var patternsURL: URL { store.root.appendingPathComponent("location-patterns.cache") }
    private var dayMapsRoot: URL { store.root.appendingPathComponent("day-maps", isDirectory: true) }

    func loadPlaceCaches() {
        places = placesCache.load()
        locationDays = (try? JSONDecoder().decode([LocationDay].self, from: Data(contentsOf: locationDaysURL))) ?? []
        locationPatterns = try? JSONDecoder().decode(LocationPatterns.self, from: Data(contentsOf: patternsURL))
        // The geofences follow what was last known, so a launch with no
        // server still watches the right places.
        trail.monitor(RegionPlan.regions(places, today: DayKey.of(Date())))
    }

    private func keep(_ snapshot: PlacesSnapshot) {
        places = snapshot
        placesCache.save(snapshot)
        trail.monitor(RegionPlan.regions(snapshot, today: DayKey.of(Date())))
    }

    func refreshPlaces(using api: JournalAPI) async {
        guard let snapshot = try? await api.places() else { return }
        keep(snapshot)
    }

    func refreshPlaces() async {
        guard let api = chatAPI() else { return }
        await refreshPlaces(using: api)
    }

    func refreshLocationDays(using api: JournalAPI) async {
        guard let days = try? await api.locationDays() else { return }
        locationDays = days
        try? JSONEncoder().encode(days).write(to: locationDaysURL, options: .atomic)
    }

    func refreshPatterns() async {
        guard let api = chatAPI(), let fresh = try? await api.locationPatterns() else { return }
        locationPatterns = fresh
        try? JSONEncoder().encode(fresh).write(to: patternsURL, options: .atomic)
    }

    /// A day's map picture, from disk when this version was fetched before.
    /// The server's URL names a version, so a cached file is never stale.
    func dayMap(_ day: LocationDay) async -> Data? {
        let version = URLComponents(string: day.imageUrl)?.queryItems?.first { $0.name == "v" }?.value ?? "current"
        let file = dayMapsRoot.appendingPathComponent("\(day.date)-\(version).png")
        if let data = try? Data(contentsOf: file) { return data }
        guard let api = chatAPI(), let data = try? await api.dayMap(day.imageUrl) else { return nil }
        let manager = FileManager.default
        try? manager.createDirectory(at: dayMapsRoot, withIntermediateDirectories: true)
        // Older versions of this day are never shown again.
        for old in (try? manager.contentsOfDirectory(atPath: dayMapsRoot.path)) ?? []
        where old.hasPrefix(day.date + "-") {
            try? manager.removeItem(at: dayMapsRoot.appendingPathComponent(old))
        }
        try? data.write(to: file, options: .atomic)
        return data
    }

    // MARK: Edits

    /// Runs one edit against the server and refetches. Returns the server's
    /// own wording when it refused, or a sign-in prompt offline.
    private func editPlaces(_ change: (JournalAPI) async throws -> Void) async -> String? {
        guard let api = chatAPI() else { return "Connect to your server to change places." }
        do {
            try await change(api)
            await refreshPlaces(using: api)
            // A role change moves the day cards' Home/Work lines and maps.
            await refreshLocationDays(using: api)
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    func savePlace(_ place: SavedPlace, isNew: Bool) async -> String? {
        await editPlaces { _ = try await $0.savePlace(place, isNew: isNew) }
    }

    func deletePlace(_ id: String) async -> String? {
        await editPlaces { try await $0.deletePlace(id) }
    }

    func assignRole(_ role: PlaceRole, placeID: String, since: String) async -> String? {
        await editPlaces { try await $0.assignRole(role, placeID: placeID, startedOn: since) }
    }

    func updateRole(_ id: String, startedOn: String? = nil, endedOn: String?? = nil) async -> String? {
        await editPlaces { try await $0.updateRole(id, startedOn: startedOn, endedOn: endedOn) }
    }

    func deleteRole(_ id: String) async -> String? {
        await editPlaces { try await $0.deleteRole(id) }
    }
}
