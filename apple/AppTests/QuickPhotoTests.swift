import XCTest
@testable import Lunaschal

@MainActor
final class QuickPhotoTests: XCTestCase {
    override func tearDown() { QuickPhoto.shared.requested = false }

    func testTakePhotoIntentLeavesARequestForTheCamera() async throws {
        QuickPhoto.shared.requested = false
        _ = try await TakePhotoIntent().perform()
        XCTAssertTrue(QuickPhoto.shared.requested)
    }

    func testThePhoneIsPortraitOnlyAndTheIPadTurns() {
        // The file itself: at runtime `infoDictionary` keeps only this device's keys.
        let url = try! XCTUnwrap(Bundle.main.url(forResource: "Info", withExtension: "plist"))
        let info = try! XCTUnwrap(NSDictionary(contentsOf: url) as? [String: Any])
        XCTAssertEqual(info["UISupportedInterfaceOrientations"] as? [String], ["UIInterfaceOrientationPortrait"])
        XCTAssertEqual(Set(info["UISupportedInterfaceOrientations~ipad"] as? [String] ?? []), [
            "UIInterfaceOrientationPortrait", "UIInterfaceOrientationPortraitUpsideDown",
            "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight",
        ])
    }
}
