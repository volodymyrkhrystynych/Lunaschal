import CoreLocation
import CoreMotion
import Foundation
import LunaschalCore

/// Records where the phone goes, all day, for the Journal's daily map
/// (backend/location/). Off until turned on in Settings, and needs "Always".
///
/// Two modes, chosen by `TrailPolicy` from Core Motion (see LocationTrail.swift):
/// visits + significant-change monitoring all the time, which costs almost
/// nothing and relaunches the app after it was killed; and live GPS only while
/// walking, running or cycling. Each relaunch lands in `init`, which is why
/// this is built during app launch (CaptureModel's init) and restarts
/// monitoring there: iOS delivers the event to whichever manager has a
/// delegate by the time launch finishes.
///
/// Separate from `LocationProvider`, the Capture tab's one-shot fix: that one
/// only ever asks for While Using, and must keep working when this is off.
///
/// It also watches the current Home and each Work as geofences (`CLMonitor`,
/// regions from `RegionPlan`), and records each stay inside one as a `region`
/// trail point: the exact minute of arriving and leaving, which is what the
/// server's work calendar moves the workday to. iOS keeps a monitor's
/// conditions across launches and relaunches the app for an event, so the
/// monitor is reopened by name in `init` along with everything else.
@MainActor
final class LocationTracker: NSObject, ObservableObject, CLLocationManagerDelegate {
    static let enabledKey = "locationTrailEnabled"
    /// How far back to ask Core Motion on a background wake, to learn whether
    /// a walk is already under way.
    private static let motionHistory: TimeInterval = 2 * 3600

    @Published private(set) var authorization: CLAuthorizationStatus
    /// Precise Location turned off: every fix is blurred to a few kilometres,
    /// which makes a map of the day meaningless.
    @Published private(set) var reducedAccuracy: Bool
    @Published private(set) var mode: TrailPolicy.Mode = .coarse
    @Published private(set) var status: TrailOutbox.Status
    @Published private(set) var pending: Int
    /// New points were saved; the model decides whether to upload now.
    var onPoints: (() -> Void)?

    let outbox: TrailOutbox
    private let manager = CLLocationManager()
    private let motion = CMMotionActivityManager()
    private var filter = TrailFilter()
    private var motionSamples: [TrailPolicy.Motion] = []
    private var monitoring = false
    private var placeMonitor: CLMonitor?
    private var placeEvents: Task<Void, Never>?
    /// What is being watched, and the stays still open, on disk: an exit may
    /// be the event iOS relaunched the app for.
    @Published private(set) var regions: [MonitoredRegion]
    private var ledger: RegionLedger
    private var regionsURL: URL { outbox.root.appendingPathComponent("regions.json") }
    private var ledgerURL: URL { outbox.root.appendingPathComponent("region-ledger.json") }

    init(outbox: TrailOutbox) {
        self.outbox = outbox
        status = outbox.status()
        pending = outbox.count
        regions = (try? JSONDecoder().decode([MonitoredRegion].self,
                                             from: Data(contentsOf: outbox.root.appendingPathComponent("regions.json")))) ?? []
        ledger = (try? JSONDecoder().decode(RegionLedger.self,
                                            from: Data(contentsOf: outbox.root.appendingPathComponent("region-ledger.json")))) ?? RegionLedger()
        authorization = manager.authorizationStatus
        reducedAccuracy = manager.accuracyAuthorization == .reducedAccuracy
        super.init()
        manager.delegate = self
        manager.activityType = .fitness
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = TrailFilter.minStep
        // iOS stops the GPS on its own when a walk stops moving, and says so
        // through didPauseLocationUpdates -- the safety net under the policy.
        manager.pausesLocationUpdatesAutomatically = true
        manager.showsBackgroundLocationIndicator = false
        startIfAllowed()
    }

    var enabled: Bool { UserDefaults.standard.bool(forKey: Self.enabledKey) }
    var hasAlways: Bool { authorization == .authorizedAlways }

    /// Turn recording on, asking for Always if it isn't granted yet. iOS shows
    /// that prompt at most once; after that, only the Settings app can change it.
    func enable() {
        UserDefaults.standard.set(true, forKey: Self.enabledKey)
        objectWillChange.send()
        switch authorization {
        case .notDetermined, .authorizedWhenInUse: manager.requestAlwaysAuthorization()
        default: break
        }
        startIfAllowed()
    }

    func disable() {
        UserDefaults.standard.set(false, forKey: Self.enabledKey)
        objectWillChange.send()
        stop()
    }

    func refreshStatus() {
        status = outbox.status()
        pending = outbox.count
    }

    private func startIfAllowed() {
        guard enabled, hasAlways || authorization == .authorizedWhenInUse else { stop(); return }
        // Background delivery is only possible -- and only allowed to be asked
        // for -- with Always; While Using still records while the app is open.
        manager.allowsBackgroundLocationUpdates = hasAlways
        guard !monitoring else { return }
        monitoring = true
        manager.startMonitoringVisits()
        manager.startMonitoringSignificantLocationChanges()
        if CMMotionActivityManager.isActivityAvailable() {
            motion.startActivityUpdates(to: .main) { [weak self] activity in
                guard let activity else { return }
                MainActor.assumeIsolated { self?.receive([activity]) }
            }
        }
        askMotionHistory()
        if hasAlways { startPlaceMonitor() }
    }

    private func stop() {
        guard monitoring else { return }
        monitoring = false
        manager.stopMonitoringVisits()
        manager.stopMonitoringSignificantLocationChanges()
        manager.stopUpdatingLocation()
        motion.stopActivityUpdates()
        placeEvents?.cancel()
        placeEvents = nil
        if let monitor = placeMonitor {
            placeMonitor = nil
            Task { for id in await monitor.identifiers { await monitor.remove(id) } }
        }
        mode = .coarse
    }

    // MARK: Home and Work geofences

    /// Replace what is watched with `next` (the current Home and Works). Called
    /// whenever the places are refreshed from the server.
    func monitor(_ next: [MonitoredRegion]) {
        guard next != regions else { return }
        regions = next
        ledger.keepOnly(next)
        try? JSONEncoder().encode(next).write(to: regionsURL, options: .atomic)
        saveLedger()
        guard let monitor = placeMonitor else { return }
        Task { await Self.apply(next, to: monitor) }
    }

    private func startPlaceMonitor() {
        guard placeEvents == nil else { return }
        placeEvents = Task { [weak self] in
            let monitor = await CLMonitor(RegionPlan.monitorName)
            guard let self else { return }
            placeMonitor = monitor
            await Self.apply(regions, to: monitor)
            do {
                for try await event in await monitor.events {
                    handle(identifier: event.identifier, inside: event.state == .satisfied, at: event.date)
                }
            } catch {}
        }
    }

    private static func apply(_ regions: [MonitoredRegion], to monitor: CLMonitor) async {
        let wanted = Dictionary(uniqueKeysWithValues: regions.map { ($0.placeID, $0) })
        for id in await monitor.identifiers where wanted[id] == nil { await monitor.remove(id) }
        for region in regions {
            let condition = CLMonitor.CircularGeographicCondition(
                center: CLLocationCoordinate2D(latitude: region.latitude, longitude: region.longitude),
                radius: region.radius)
            // Re-adding replaces the condition: a corrected radius or a moved
            // pin takes effect without forgetting the rest.
            await monitor.add(condition, identifier: region.placeID, assuming: .unsatisfied)
        }
    }

    private func handle(identifier: String, inside: Bool, at date: Date) {
        guard let region = regions.first(where: { $0.placeID == identifier }) else { return }
        let ts = date.timeIntervalSince1970
        let point = inside ? ledger.enter(region, at: ts) : ledger.exit(region, at: ts)
        saveLedger()
        guard let point else { return }
        do { try outbox.append([point]) } catch { return }
        refreshStatus()
        onPoints?()
    }

    private func saveLedger() {
        try? JSONEncoder().encode(ledger).write(to: ledgerURL, options: .atomic)
    }

    /// The newest confident Core Motion reading, attached to each fix.
    private var currentActivity: TrailPolicy.Activity? {
        motionSamples.last { $0.confident }?.activity
    }

    /// A background wake doesn't replay the activity updates missed while the
    /// app was suspended, so ask for them.
    private func askMotionHistory() {
        guard CMMotionActivityManager.isActivityAvailable() else { return }
        let now = Date()
        motion.queryActivityStarting(from: now.addingTimeInterval(-Self.motionHistory), to: now, to: .main) { [weak self] activities, _ in
            MainActor.assumeIsolated { self?.receive(activities ?? []) }
        }
    }

    private func receive(_ activities: [CMMotionActivity]) {
        for activity in activities {
            let kind: TrailPolicy.Activity =
                activity.running ? .running : activity.cycling ? .cycling : activity.walking ? .walking
                : activity.automotive ? .automotive : activity.stationary ? .stationary : .unknown
            let sample = TrailPolicy.Motion(kind, confident: activity.confidence != .low, at: activity.startDate)
            if !motionSamples.contains(sample) { motionSamples.append(sample) }
        }
        motionSamples.sort { $0.at < $1.at }
        motionSamples = Array(motionSamples.suffix(50))
        reconsider(speed: nil)
    }

    private func reconsider(speed: Double?) {
        guard monitoring else { return }
        let next = TrailPolicy.next(current: mode, motion: motionSamples, speed: speed, now: Date())
        guard next != mode else { return }
        mode = next
        if next == .fine { manager.startUpdatingLocation() } else { manager.stopUpdatingLocation() }
    }

    private func record(_ points: [TrailPoint]) {
        let kept = points.filter { filter.admit($0) }
        guard !kept.isEmpty else { return }
        do { try outbox.append(kept) } catch { return }
        refreshStatus()
        onPoints?()
    }

    // MARK: CLLocationManagerDelegate

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        let reduced = manager.accuracyAuthorization == .reducedAccuracy
        Task { @MainActor in
            authorization = status
            reducedAccuracy = reduced
            startIfAllowed()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        Task { @MainActor in
            let kind: TrailPoint.Kind = mode == .fine ? .fine : .coarse
            let activity = currentActivity
            record(locations.map { loc in
                TrailPoint(id: ULID.make(now: loc.timestamp), ts: loc.timestamp.timeIntervalSince1970,
                           latitude: loc.coordinate.latitude, longitude: loc.coordinate.longitude,
                           accuracy: loc.horizontalAccuracy,
                           altitude: loc.verticalAccuracy >= 0 ? loc.altitude : nil,
                           speed: loc.speed >= 0 ? loc.speed : nil, kind: kind, activity: activity)
            })
            // A coarse wake is the moment to notice a walk has begun.
            if kind == .coarse {
                askMotionHistory()
                reconsider(speed: locations.last.flatMap { $0.speed >= 0 ? $0.speed : nil })
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didVisit visit: CLVisit) {
        // An arrival iOS didn't see (distantPast) has no time to file it under.
        guard visit.arrivalDate != .distantPast else { return }
        let arrival = visit.arrivalDate.timeIntervalSince1970
        let departed = visit.departureDate == .distantFuture ? nil : visit.departureDate.timeIntervalSince1970
        let point = TrailPoint(id: TrailPoint.visitID(arrival: arrival), ts: arrival,
                               latitude: visit.coordinate.latitude, longitude: visit.coordinate.longitude,
                               accuracy: visit.horizontalAccuracy, kind: .visit, departed: departed)
        Task { @MainActor in
            record([point])
            askMotionHistory()
        }
    }

    nonisolated func locationManagerDidPauseLocationUpdates(_ manager: CLLocationManager) {
        Task { @MainActor in
            // iOS saw no movement. Recorded as a stationary reading so the
            // policy doesn't switch straight back on the strength of a walking
            // sample Core Motion hasn't superseded yet; a new walk is a new one.
            motionSamples.append(.init(.stationary, at: Date()))
            mode = .coarse
            manager.stopUpdatingLocation()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {}
}
