import Foundation

/// `GET /api/lifestyle/weather/today`: the day's hourly rows and sun times for
/// wherever the server last knew the user to be. The pure helpers below are a
/// port of `src/lib/weather.ts`; keep the two in step.
public struct WeatherDay: Codable, Equatable {
    public struct Hour: Codable, Equatable, Identifiable {
        public let id: String
        public let dayKey: String
        public let hourTs: Date
        public let weatherCode: Int
        public let temperatureC: Double
        public let wetBulbC: Double?
        public let isActual: Bool
        // Absent from rows synced before the server fetched them.
        public var apparentC: Double? = nil
        public var windKmh: Double? = nil
        public var gustKmh: Double? = nil
        public var isDay: Bool? = nil

        public init(id: String, dayKey: String, hourTs: Date, weatherCode: Int,
                    temperatureC: Double, wetBulbC: Double? = nil, isActual: Bool = false) {
            self.id = id; self.dayKey = dayKey; self.hourTs = hourTs; self.weatherCode = weatherCode
            self.temperatureC = temperatureC; self.wetBulbC = wetBulbC; self.isActual = isActual
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            id = try c.decode(String.self, forKey: .id)
            dayKey = try c.decode(String.self, forKey: .dayKey)
            hourTs = try c.decode(Date.self, forKey: .hourTs)
            weatherCode = try c.decode(Int.self, forKey: .weatherCode)
            temperatureC = try c.decode(Double.self, forKey: .temperatureC)
            wetBulbC = try c.decodeIfPresent(Double.self, forKey: .wetBulbC)
            // SQLite hands the flag over as 0/1.
            if let flag = try? c.decode(Bool.self, forKey: .isActual) { isActual = flag }
            else { isActual = (try c.decodeIfPresent(Int.self, forKey: .isActual) ?? 0) != 0 }
            apparentC = try c.decodeIfPresent(Double.self, forKey: .apparentC)
            windKmh = try c.decodeIfPresent(Double.self, forKey: .windKmh)
            gustKmh = try c.decodeIfPresent(Double.self, forKey: .gustKmh)
            if let flag = try? c.decode(Bool.self, forKey: .isDay) { isDay = flag }
            else { isDay = try c.decodeIfPresent(Int.self, forKey: .isDay).map { $0 != 0 } }
        }

        enum CodingKeys: String, CodingKey {
            case id, dayKey, hourTs, weatherCode, temperatureC, wetBulbC, isActual, apparentC, windKmh, gustKmh, isDay
        }
    }

    public struct Location: Codable, Equatable {
        public let source: String
    }

    public let hours: [Hour]
    public let location: Location?
    public let sunriseTs: Date?
    public let sunsetTs: Date?

    public init(hours: [Hour], location: Location? = nil, sunriseTs: Date? = nil, sunsetTs: Date? = nil) {
        self.hours = hours; self.location = location; self.sunriseTs = sunriseTs; self.sunsetTs = sunsetTs
    }

    /// The server's timestamps are ISO 8601 with an offset (`+00:00`).
    public static func decode(_ data: Data) throws -> WeatherDay {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            for options: ISO8601DateFormatter.Options in [[.withInternetDateTime, .withFractionalSeconds], [.withInternetDateTime]] {
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = options
                if let date = formatter.date(from: text) { return date }
            }
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Not a timestamp: \(text)"))
        }
        return try decoder.decode(WeatherDay.self, from: data)
    }

    /// Whether this is the given day's forecast; a cached copy from yesterday is not today's weather.
    public func isFor(day: String) -> Bool { hours.first?.dayKey == day }

    /// The latest hour that has already started; the first row if all are
    /// still ahead; nil for no rows.
    public func currentHourIndex(now: Date = Date()) -> Int? {
        guard !hours.isEmpty else { return nil }
        var best = 0
        for (index, hour) in hours.enumerated() where hour.hourTs <= now { best = index }
        return best
    }

    /// Outside [sunrise, sunset). Never night without real sun times.
    public func isNight(_ time: Date) -> Bool {
        guard let sunriseTs, let sunsetTs else { return false }
        return time < sunriseTs || time >= sunsetTs
    }

    public func condition(_ hour: Hour) -> WeatherCondition {
        WeatherCondition.describe(hour.weatherCode, night: isNight(hour.hourTs), moon: MoonPhase.at(hour.hourTs))
    }
}

public struct WeatherCondition: Equatable {
    public let label: String
    public let icon: String

    // Open-Meteo's hourly `weather_code` is WMO code table 4677.
    static let codes: [Int: WeatherCondition] = [
        0: .init(label: "Clear", icon: "☀️"), 1: .init(label: "Mainly clear", icon: "🌤️"),
        2: .init(label: "Partly cloudy", icon: "⛅"), 3: .init(label: "Overcast", icon: "☁️"),
        45: .init(label: "Fog", icon: "🌫️"), 48: .init(label: "Freezing fog", icon: "🌫️"),
        51: .init(label: "Light drizzle", icon: "🌦️"), 53: .init(label: "Drizzle", icon: "🌦️"),
        55: .init(label: "Dense drizzle", icon: "🌦️"), 56: .init(label: "Freezing drizzle", icon: "🌧️"),
        57: .init(label: "Dense freezing drizzle", icon: "🌧️"), 61: .init(label: "Light rain", icon: "🌧️"),
        63: .init(label: "Rain", icon: "🌧️"), 65: .init(label: "Heavy rain", icon: "🌧️"),
        66: .init(label: "Freezing rain", icon: "🌨️"), 67: .init(label: "Heavy freezing rain", icon: "🌨️"),
        71: .init(label: "Light snow", icon: "🌨️"), 73: .init(label: "Snow", icon: "🌨️"),
        75: .init(label: "Heavy snow", icon: "❄️"), 77: .init(label: "Snow grains", icon: "❄️"),
        80: .init(label: "Light showers", icon: "🌦️"), 81: .init(label: "Showers", icon: "🌦️"),
        82: .init(label: "Violent showers", icon: "⛈️"), 85: .init(label: "Snow showers", icon: "🌨️"),
        86: .init(label: "Heavy snow showers", icon: "🌨️"), 95: .init(label: "Thunderstorm", icon: "⛈️"),
        96: .init(label: "Thunderstorm with hail", icon: "⛈️"),
        99: .init(label: "Severe thunderstorm with hail", icon: "⛈️"),
    ]

    /// Never fails: an unmapped code is "Unknown". At night, clear skies show
    /// the real moon phase and partly cloudy the moon behind a cloud; anything
    /// cloudier looks the same at any hour and is left alone.
    public static func describe(_ code: Int, night: Bool = false, moon: MoonPhase? = nil) -> WeatherCondition {
        let base = codes[code] ?? WeatherCondition(label: "Unknown", icon: "❔")
        guard night else { return base }
        let moon = moon ?? MoonPhase.at(Date())
        switch code {
        case 0, 1: return WeatherCondition(label: base.label, icon: moon.emoji)
        case 2: return WeatherCondition(label: base.label, icon: moon.emoji + "☁️")
        default: return base
        }
    }
}

public struct MoonPhase: Equatable {
    public let index: Int
    public let name: String
    public let emoji: String

    static let phases: [MoonPhase] = [
        .init(index: 0, name: "New moon", emoji: "🌑"), .init(index: 1, name: "Waxing crescent", emoji: "🌒"),
        .init(index: 2, name: "First quarter", emoji: "🌓"), .init(index: 3, name: "Waxing gibbous", emoji: "🌔"),
        .init(index: 4, name: "Full moon", emoji: "🌕"), .init(index: 5, name: "Waning gibbous", emoji: "🌖"),
        .init(index: 6, name: "Last quarter", emoji: "🌗"), .init(index: 7, name: "Waning crescent", emoji: "🌘"),
    ]
    static let synodicMonthDays = 29.530588853
    /// 2000-01-06T18:14Z, a documented new moon.
    static let referenceNewMoon = Date(timeIntervalSince1970: 947_182_440)

    public static func at(_ date: Date) -> MoonPhase {
        let cycles = date.timeIntervalSince(referenceNewMoon) / 86_400 / synodicMonthDays
        let fraction = cycles - cycles.rounded(.down)
        return phases[Int(fraction * 8) % 8]
    }
}

/// The weather the server stored on a journal or food entry
/// (backend/weather/entry.py). `summary` says it the way the desktop's
/// src/lib/entryWeather.ts does; keep the two in step.
public struct EntryWeather: Codable, Equatable {
    public let hourTs: Double
    public let weatherCode: Int
    public let temperatureC: Double
    public let apparentC: Double?
    public let windKmh: Double?
    public let gustKmh: Double?
    public let windy: Bool?
    public let isDay: Bool?

    /// Sustained wind or gusts strong enough to be the thing you notice;
    /// the same thresholds as backend/weather/entry.py.
    public static func isWindy(_ wind: Double?, _ gust: Double?) -> Bool { (wind ?? 0) >= 30 || (gust ?? 0) >= 50 }

    public init(hourTs: Double, weatherCode: Int, temperatureC: Double, apparentC: Double? = nil,
                windKmh: Double? = nil, gustKmh: Double? = nil, windy: Bool? = nil, isDay: Bool? = nil) {
        self.hourTs = hourTs; self.weatherCode = weatherCode; self.temperatureC = temperatureC
        self.apparentC = apparentC; self.windKmh = windKmh; self.gustKmh = gustKmh
        self.windy = windy; self.isDay = isDay
    }

    /// An hour of today's forecast, said the same way as an entry's weather.
    /// Day or night comes from the hour itself, else from the day's sun times.
    public init(_ hour: WeatherDay.Hour, in day: WeatherDay) {
        self.init(hourTs: hour.hourTs.timeIntervalSince1970, weatherCode: hour.weatherCode,
                  temperatureC: hour.temperatureC, apparentC: hour.apparentC, windKmh: hour.windKmh,
                  gustKmh: hour.gustKmh, windy: Self.isWindy(hour.windKmh, hour.gustKmh),
                  isDay: hour.isDay ?? (day.sunriseTs == nil ? nil : !day.isNight(hour.hourTs)))
    }

    /// The column is JSON text, or nil before the lookup has run.
    public static func parse(_ text: String?) -> EntryWeather? {
        guard let text, let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(EntryWeather.self, from: data)
    }

    /// "☁️ Overcast 3°C · feels -3°C · windy 34 km/h, gusts 58 · sun up"
    public var summary: String { ([headline] + details).joined(separator: " · ") }

    public var condition: WeatherCondition {
        WeatherCondition.describe(weatherCode, night: isDay == false, moon: MoonPhase.at(Date(timeIntervalSince1970: hourTs)))
    }

    /// "☁️ Overcast 3°C"
    public var headline: String { "\(condition.icon) \(condition.label) \(Self.degrees(temperatureC))" }

    /// Feels-like, wind and the sun, each only when recorded.
    public var details: [String] {
        var parts: [String] = []
        if let apparentC { parts.append("feels \(Self.degrees(apparentC))") }
        if let windKmh {
            let wind = "\(Int(windKmh.rounded())) km/h"
            if windy == true {
                parts.append("windy \(wind)" + (gustKmh.map { ", gusts \(Int($0.rounded()))" } ?? ""))
            } else {
                parts.append("wind \(wind)")
            }
        }
        if let isDay { parts.append(isDay ? "sun up" : "sun down") }
        return parts
    }

    // JavaScript's Math.round: halves go up, so -2.5 is -2 on both sides.
    static func degrees(_ celsius: Double) -> String { "\(Int((celsius + 0.5).rounded(.down)))°C" }
}
