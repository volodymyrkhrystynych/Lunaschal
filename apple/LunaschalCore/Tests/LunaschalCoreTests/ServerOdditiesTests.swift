import XCTest
@testable import LunaschalCore

final class ServerOdditiesTests: XCTestCase {
    let content = "Your morning plan.\n\n**Server oddities (past day)**\n- **New** · lunaschal-deploy · 120× — `Failed to start`\n  The deployment timed out.\n- **Ongoing** · lunaschal · 2× — `Error`\n- …and 3 more.\n_The server journal could not be read._"

    func testSavedReportPreservesEntriesAndNotices() throws {
        let report = try XCTUnwrap(ServerOddities.parse(content: content, metadata: "{\"briefing\":true}"))
        XCTAssertEqual(report.prose, "Your morning plan.")
        XCTAssertEqual(report.items.count, 2)
        XCTAssertTrue(report.items[0].title.contains("120×"))
        XCTAssertEqual(report.items[0].note, "The deployment timed out.")
        XCTAssertEqual(report.items[1].note, "")
        XCTAssertTrue(report.notices.contains("3 more"))
        XCTAssertTrue(report.notices.contains("journal could not be read"))
    }

    func testOnlyBriefingsWithAReportAreExtracted() {
        let values: [String?] = [nil, "{", "{}", "{\"briefing\":false}"]
        for metadata in values {
            XCTAssertNil(ServerOddities.parse(content: content, metadata: metadata))
        }
        XCTAssertNil(ServerOddities.parse(content: "**Server oddities:** none in the past day.", metadata: "{\"briefing\":true}"))
    }
}
