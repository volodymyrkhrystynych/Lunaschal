import Foundation

/// A position in one specific content version. Device-local; never an implicit
/// server mutation or a replacement for cross-device reading conflict handling.
public struct ReadingPosition: Codable, Equatable {
    public let version: String
    public let offset: Int
}
