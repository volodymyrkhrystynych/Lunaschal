import AVFAudio
import XCTest
@testable import Lunaschal

final class RecordingSessionTests: XCTestCase {
    /// Without a Bluetooth option `.playAndRecord` ignores the AirPods mic entirely.
    func testRecordingRoutesToAirPodsWithHighQualityAndHFPFallback() {
        let options = RecordingSession.options
        XCTAssertTrue(options.contains(.bluetoothHighQualityRecording))
        XCTAssertTrue(options.contains(.allowBluetoothHFP))
        XCTAssertTrue(options.contains(.defaultToSpeaker))
    }
}
