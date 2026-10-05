import CoreLocation
import Foundation

/// One-shot location fixes for the Capture tab: asked for when the tab is
/// shown, kept for a while, and attached to whatever is saved next. Save never
/// waits for the GPS; an entry saved without a recent fix is simply unlocated.
@MainActor
final class LocationProvider: NSObject, ObservableObject, CLLocationManagerDelegate {
    /// Older than this, the fix no longer says where an entry was written.
    static let freshFor: TimeInterval = 15 * 60

    @Published private(set) var latest: CLLocation?
    var onFix: ((CLLocation) -> Void)?
    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    /// The latest fix, if it is recent enough to stand for "here".
    var recent: (latitude: Double, longitude: Double)? {
        guard let latest, -latest.timestamp.timeIntervalSinceNow < Self.freshFor else { return nil }
        return (latest.coordinate.latitude, latest.coordinate.longitude)
    }

    /// Asks once for permission, then for a single fix. Denied is not an error.
    func refresh() {
        switch manager.authorizationStatus {
        case .notDetermined: manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways: manager.requestLocation()
        default: break
        }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor in
            if [.authorizedWhenInUse, .authorizedAlways].contains(manager.authorizationStatus) { manager.requestLocation() }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let fix = locations.last else { return }
        Task { @MainActor in
            latest = fix
            onFix?(fix)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {}
}
