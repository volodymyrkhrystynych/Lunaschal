import Foundation

/// Server-derived Health sleep, named by the morning the night ended.
/// Nil readings are unknown, never a zero-hour night.
public struct SleepNight: Codable, Equatable, Identifiable {
    public let date: String
    public var id: String { date }
    public let start: Double?
    public let end: Double?
    public let asleepMinutes: Double?
    public let awakeMinutes: Double?
    public let interruptions: Int?
    public let efficiency: Double?
    public let unobservedMinutes: Double?
}

public struct SleepWeek: Codable, Equatable {
    public let days: [SleepNight]

    /// Keep a cached week useful offline without presenting yesterday as today.
    public func ending(on day: String) -> [SleepNight] {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = TimeZone(secondsFromGMT: 0)
        format.dateFormat = "yyyy-MM-dd"
        guard let end = format.date(from: day) else { return [] }
        return (0..<7).reversed().map { offset in
            let key = format.string(from: end.addingTimeInterval(-Double(offset) * 86400))
            return days.first { $0.date == key } ?? SleepNight(date: key, start: nil, end: nil,
                asleepMinutes: nil, awakeMinutes: nil, interruptions: nil, efficiency: nil, unobservedMinutes: nil)
        }
    }
}
