import Foundation
import XCTest
@testable import LunaschalCore

final class WorkoutTests: XCTestCase {
    private var root: URL!
    private var store: WorkoutStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try WorkoutStore(root: root)
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: root) }

    // The cases from backend/tests/test_workout_quick_entry.py.
    func testLinesParseLikeTheServers() throws {
        for (text, selected, weight, reps) in [("bicep curls 20, 10", nil, 20.0, 10), ("20,10", "bicep curl", 20.0, 10),
                                               ("squats 10", "bicep curl", nil, 10), ("curls 22.5 lbs x 8 reps", nil, 22.5, 8)]
            as [(String, String?, Double?, Int)] {
            let entry = try WorkoutEntry.parse(text, selected: selected)
            XCTAssertEqual(entry.weight, weight, text)
            XCTAssertEqual(entry.reps, reps, text)
        }
        XCTAssertEqual(try WorkoutEntry.parse("20,10", selected: "bicep curl").name, "bicep curl")
        let walk = try WorkoutEntry.parse("walking 30 minutes")
        XCTAssertEqual(walk, WorkoutEntry(name: "walking", kind: .outdoor, weight: nil, reps: nil, minutes: 30))
        XCTAssertEqual(try WorkoutEntry.parse("30", selected: "on the bike").name, "cycling")
    }

    func testWhatTheServerRefusesIsRefusedHere() {
        for text in ["20, 10", "squats -10", "curls 20 kg, 10", "curls 20, 1.5", "walking 30, 10",
                     "cycling 0", "curls 20, 10 garbage", "squat 10\ncurl 20,10", "  "] {
            XCTAssertThrowsError(try WorkoutEntry.parse(text), text)
        }
        XCTAssertThrowsError(try WorkoutEntry.parse("20, 10")) {
            XCTAssertEqual($0.localizedDescription, "Name an exercise or select a recent exercise first.")
        }
    }

    func testOnlyABareLineCarriesTheSelection() throws {
        let (bare, _) = try store.log("20, 10", selected: "Bicep Curl")
        XCTAssertEqual(bare.exercise, "bicep curl")
        let (named, _) = try store.log(" squats 10 ", selected: "bicep curl")
        XCTAssertNil(named.exercise)
        XCTAssertEqual(named.text, "squats 10")
        XCTAssertThrowsError(try store.log("curls 20 kg, 10", selected: nil))
        XCTAssertEqual(try store.list().count, 2)
    }

    @MainActor
    func testSyncKeepsOrderDropsWhatLandedAndSkipsWhatWasRefused() async throws {
        let first = try store.log("squats 10", selected: nil, now: Date(timeIntervalSince1970: 100)).0
        let refused = try store.log("squats 11", selected: nil, now: Date(timeIntervalSince1970: 200)).0
        let third = try store.log("squats 12", selected: nil, now: Date(timeIntervalSince1970: 300)).0
        let server = FakeGym(refuse: [refused.id: 400])
        try await WorkoutSync(store: store).run(using: server)
        XCTAssertEqual(server.sent, [first.id, refused.id, third.id])
        XCTAssertEqual(try store.list().map(\.id), [refused.id])
        XCTAssertEqual(try store.list().first?.state, .failed)
        // A refused line is not sent again on its own.
        try await WorkoutSync(store: store).run(using: server)
        XCTAssertEqual(server.sent.count, 3)
    }

    @MainActor
    func testAnUnreachableServerStopsThePassInOrder() async throws {
        let first = try store.log("squats 10", selected: nil, now: Date(timeIntervalSince1970: 100)).0
        _ = try store.log("squats 11", selected: nil, now: Date(timeIntervalSince1970: 200))
        do {
            try await WorkoutSync(store: store).run(using: FakeGym(refuse: [first.id: 503]))
            XCTFail("expected the 503 to stop the pass")
        } catch {}
        XCTAssertEqual(try store.list().map(\.state), [.pending, .pending])
    }

    func testTheReplyMustContainTheEntry() throws {
        let item = try store.log("squats 10", selected: nil).0
        let mine = Data(#"{"session": {"id": "S", "exercises": [{"sets": [{"id": "\#(item.id)"}]}]}, "exercise": "squat"}"#.utf8)
        XCTAssertNoThrow(try JournalAPI.validateWorkoutAcknowledgement(mine, for: item))
        let other = Data(#"{"session": {"id": "S", "exercises": [{"sets": [{"id": "X"}]}]}}"#.utf8)
        XCTAssertThrowsError(try JournalAPI.validateWorkoutAcknowledgement(other, for: item))
        let walk = try store.log("walking 30", selected: nil).0
        let outdoor = Data(#"{"session": {"id": "\#(walk.id)", "exercises": [{"sets": []}]}}"#.utf8)
        XCTAssertNoThrow(try JournalAPI.validateWorkoutAcknowledgement(outdoor, for: walk))
    }

    // The cases from formatSets in src/lib/lifestyle.test.ts.
    func testSetsReadLikeTheDesktops() {
        typealias S = WorkoutSession.Exercise.Set
        XCTAssertEqual(WorkoutLabels.sets(Array(repeating: S(weight: nil, reps: 10), count: 4)), "10 × 4 bodyweight")
        XCTAssertEqual(WorkoutLabels.sets([S(weight: nil, reps: 8)]), "8 bodyweight")
        XCTAssertEqual(WorkoutLabels.sets([S(weight: 60, reps: 8), S(weight: 60, reps: 8), S(weight: 65, reps: 6)]), "60×8 ×2  65×6")
        XCTAssertEqual(WorkoutLabels.sets([S(weight: 22.5, reps: 8)]), "22.5×8")
        XCTAssertEqual(WorkoutLabels.sets([S(weight: 60, reps: nil)]), "60×?")
        XCTAssertEqual(WorkoutLabels.sets([]), "")
    }

    func testWalkingAndCyclingAreAlwaysOffered() {
        let recent = [RecentExercise(name: "squat", displayName: "Squat"), RecentExercise(name: "walking", displayName: "Walking")]
        XCTAssertEqual(WorkoutLabels.pills(recent).map(\.name), ["squat", "walking", "cycling"])
    }

    func testTheExerciseJustLoggedLeadsThePills() throws {
        let recent = [RecentExercise(name: "squat", displayName: "Squat"),
                      RecentExercise(name: "bicep curl", displayName: "Bicep Curl")]
        _ = try store.log("lunge a 10", selected: nil, now: Date(timeIntervalSince1970: 100))
        _ = try store.log("lunge b 10", selected: nil, now: Date(timeIntervalSince1970: 200))
        XCTAssertEqual(WorkoutLabels.pills(recent, queued: try store.list()).map(\.name),
                       ["lunge b", "lunge a", "squat", "bicep curl", "walking", "cycling"])
        // Another set of the older one, bare, brings it back to the front.
        _ = try store.log("12", selected: "lunge a", now: Date(timeIntervalSince1970: 300))
        XCTAssertEqual(WorkoutLabels.pills(recent, queued: try store.list()).prefix(3).map(\.name),
                       ["lunge a", "lunge b", "squat"])
        // A queued set of a known exercise moves it up without a second pill.
        _ = try store.log("bicep curl 20, 10", selected: nil, now: Date(timeIntervalSince1970: 400))
        let pills = WorkoutLabels.pills(recent, queued: try store.list())
        XCTAssertEqual(pills.prefix(2).map(\.name), ["bicep curl", "lunge a"])
        XCTAssertEqual(pills.first?.displayName, "Bicep Curl")
        XCTAssertEqual(pills.filter { $0.name == "bicep curl" }.count, 1)
    }

    func testTheServersSessionShapeDecodes() throws {
        let json = #"[{"id": "S", "date": "2026-10-05", "locationType": "unassigned", "captureKind": "strength","#
            + #" "durationMinutes": 30, "intensityRating": null, "rawText": "squats 10", "parseStatus": "done","#
            + #" "exercises": [{"id": "E", "displayName": "Squat", "nameCanonical": "squat","#
            + #" "sets": [{"id": "1", "weight": null, "reps": 10, "setOrder": 0}]}]}]"#
        let sessions = try JSONDecoder().decode([WorkoutSession].self, from: Data(json.utf8))
        XCTAssertEqual(WorkoutLabels.sets(sessions[0].exercises[0].sets), "10 bodyweight")
        XCTAssertEqual(WorkoutLabels.location(sessions[0].locationType), "Location not set")
    }
}

private final class FakeGym: WorkoutTransport {
    var sent: [String] = []
    let refuse: [String: Int]
    init(refuse: [String: Int] = [:]) { self.refuse = refuse }
    func sendWorkout(_ item: WorkoutLog) async throws {
        sent.append(item.id)
        if let status = refuse[item.id] { throw HTTPFailure(status: status) }
    }
}
