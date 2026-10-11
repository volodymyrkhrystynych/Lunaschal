import XCTest
@testable import LunaschalCore

final class LocationTrailTests: XCTestCase {
    private func fix(_ id: String, _ ts: Double, lat: Double = 43.65, lon: Double = -79.38,
                     accuracy: Double? = 5, kind: TrailPoint.Kind = .fine) -> TrailPoint {
        TrailPoint(id: id, ts: ts, latitude: lat, longitude: lon, accuracy: accuracy, kind: kind)
    }

    private func tempDir() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    // MARK: filter

    func testStandingStillWithTheGPSOnKeepsOnlyAnOccasionalPoint() {
        var filter = TrailFilter()
        XCTAssertTrue(filter.admit(fix("a", 0)))
        // Jitter a few metres around the same spot.
        XCTAssertFalse(filter.admit(fix("b", 5, lat: 43.65003)))
        XCTAssertFalse(filter.admit(fix("c", 60, lon: -79.38004)))
        // ...until the keep-alive passes.
        XCTAssertTrue(filter.admit(fix("d", 130, lat: 43.65003)))
        // Real movement is always kept.
        XCTAssertTrue(filter.admit(fix("e", 135, lat: 43.6505)))
    }

    func testInaccurateAndInvalidFixesAreDroppedByKind() {
        var filter = TrailFilter()
        XCTAssertFalse(filter.admit(fix("a", 0, accuracy: 200)))
        XCTAssertFalse(filter.admit(fix("b", 0, accuracy: -1)))
        XCTAssertTrue(filter.admit(fix("c", 0, accuracy: 200, kind: .coarse)))
        XCTAssertTrue(filter.admit(fix("d", 0, accuracy: 50, kind: .visit)))
    }

    func testAVisitKeepsOneIDAcrossArrivalAndDeparture() {
        XCTAssertEqual(TrailPoint.visitID(arrival: 1_783_000_000.6), "visit-1783000000")
    }

    // MARK: policy

    private let now = Date(timeIntervalSince1970: 1_783_000_000)
    private func ago(_ s: TimeInterval) -> Date { now.addingTimeInterval(-s) }

    func testAWalkTurnsTheGPSOnAndKeepsItOnHoweverLongItLasts() {
        // Core Motion reported the walk once, forty minutes ago, and nothing since.
        let walk = [TrailPolicy.Motion(.stationary, at: ago(3600)), .init(.walking, at: ago(40 * 60))]
        XCTAssertEqual(TrailPolicy.next(current: .coarse, motion: walk, speed: nil, now: now), .fine)
        XCTAssertEqual(TrailPolicy.next(current: .fine, motion: walk, speed: nil, now: now), .fine)
    }

    func testDrivingIsCoarse() {
        let drive = [TrailPolicy.Motion(.automotive, at: ago(60))]
        XCTAssertEqual(TrailPolicy.next(current: .fine, motion: drive, speed: 15, now: now), .coarse)
    }

    func testABriefStopDoesntEndAWalkButASettledOneDoes() {
        let light = [TrailPolicy.Motion(.walking, at: ago(600)), .init(.stationary, at: ago(60))]
        XCTAssertEqual(TrailPolicy.next(current: .fine, motion: light, speed: 0, now: now), .fine)
        let cafe = [TrailPolicy.Motion(.walking, at: ago(900)), .init(.stationary, at: ago(400)),
                    .init(.stationary, at: ago(30))]
        XCTAssertEqual(TrailPolicy.next(current: .fine, motion: cafe, speed: 0, now: now), .coarse)
        // Being still never turns the GPS on.
        XCTAssertEqual(TrailPolicy.next(current: .coarse, motion: light, speed: 0, now: now), .coarse)
    }

    func testUnconfidentAndUnknownReadingsChangeNothing() {
        let unsure = [TrailPolicy.Motion(.walking, confident: false, at: ago(10))]
        XCTAssertEqual(TrailPolicy.next(current: .coarse, motion: unsure, speed: nil, now: now), .coarse)
        let unknown = [TrailPolicy.Motion(.unknown, at: ago(10))]
        XCTAssertEqual(TrailPolicy.next(current: .fine, motion: unknown, speed: nil, now: now), .fine)
    }

    func testWithoutCoreMotionAWalkingSpeedTurnsTheGPSOn() {
        XCTAssertEqual(TrailPolicy.next(current: .coarse, motion: [], speed: 1.4, now: now), .fine)
        XCTAssertEqual(TrailPolicy.next(current: .coarse, motion: [], speed: 20, now: now), .coarse)
        XCTAssertEqual(TrailPolicy.next(current: .fine, motion: [], speed: 0, now: now), .fine)
    }

    // MARK: outbox + sync

    private final class FakeTransport: TrailTransport {
        var batches: [[TrailPoint]] = []
        var reply: (([TrailPoint]) -> TrailAck)?
        var fail: Error?
        func sendTrail(_ points: [TrailPoint]) async throws -> TrailAck {
            if let fail { throw fail }
            batches.append(points)
            return reply?(points) ?? TrailAck(points: points.count, rejectedCount: 0)
        }
    }

    func testOutboxKeepsOrderAndSurvivesATornLastLine() throws {
        let outbox = try TrailOutbox(root: tempDir())
        try outbox.append([fix("a", 1), fix("b", 2)])
        try outbox.append([fix("c", 3)])
        // The app was killed mid-write.
        let handle = try FileHandle(forWritingTo: outbox.root.appendingPathComponent("outbox.jsonl"))
        try handle.seekToEnd(); try handle.write(contentsOf: Data("{\"id\":\"tor".utf8)); try handle.close()
        let (points, lines) = outbox.peek(10)
        XCTAssertEqual(points.map(\.id), ["a", "b", "c"])
        XCTAssertEqual(lines, 4)
        try outbox.drop(2)
        XCTAssertEqual(outbox.peek(10).points.map(\.id), ["c"])
        XCTAssertEqual(outbox.status().recorded, 3)
    }

    @MainActor
    func testSyncDrainsInBatchesAndCountsWhatTheServerKept() async throws {
        let outbox = try TrailOutbox(root: tempDir())
        try outbox.append((0..<(TrailSync.batchLimit + 5)).map { fix("p\($0)", Double($0)) })
        let transport = FakeTransport()
        transport.reply = { TrailAck(points: $0.count - 1, rejectedCount: 1) }
        try await TrailSync(outbox: outbox).run(using: transport)
        XCTAssertEqual(transport.batches.map(\.count), [TrailSync.batchLimit, 5])
        XCTAssertEqual(outbox.count, 0)
        XCTAssertEqual(outbox.status().sent, TrailSync.batchLimit + 3)
        XCTAssertNotNil(outbox.status().lastSuccess)
    }

    @MainActor
    func testAnUnaccountedReplyOrAFailureKeepsThePoints() async throws {
        let outbox = try TrailOutbox(root: tempDir())
        try outbox.append([fix("a", 1), fix("b", 2)])
        let transport = FakeTransport()
        transport.reply = { _ in TrailAck(points: 1, rejectedCount: 0) }  // a captive portal, say
        do {
            try await TrailSync(outbox: outbox).run(using: transport)
            XCTFail("expected unaccounted")
        } catch let error as TrailSyncError {
            XCTAssertEqual(error, .unaccounted)
        }
        XCTAssertEqual(outbox.count, 2)
        transport.reply = nil
        transport.fail = URLError(.notConnectedToInternet)
        do { try await TrailSync(outbox: outbox).run(using: transport) } catch {}
        XCTAssertEqual(outbox.count, 2)
        XCTAssertNotNil(outbox.status().lastError)
    }
}
