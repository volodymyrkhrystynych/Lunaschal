import SwiftUI
import LunaschalCore

private func degrees(_ celsius: Double) -> String { "\(Int(celsius.rounded()))°" }

/// Today's forecast, or nil when what's cached is another day's.
private func today(_ weather: WeatherDay?) -> WeatherDay? {
    guard let weather, weather.isFor(day: DayKey.of(Date())) else { return nil }
    return weather
}

/// The hour happening now, said the way an entry's weather is.
private func now(_ weather: WeatherDay?) -> (day: WeatherDay, index: Int, reading: EntryWeather)? {
    guard let day = today(weather), let index = day.currentHourIndex() else { return nil }
    return (day, index, EntryWeather(day.hours[index], in: day))
}

/// Top left of the Entry page: the conditions now, small. Tapping it shows the
/// whole reading — feels-like, wind, whether the sun is up.
struct CurrentWeatherButton: View {
    let weather: WeatherDay?
    @State private var showing = false

    var body: some View {
        if let current = now(weather) {
            Button { showing = true } label: {
                Text("\(current.reading.condition.icon) \(degrees(current.reading.temperatureC))").monospacedDigit()
            }
            .accessibilityLabel("Weather now: \(current.reading.summary)")
            .accessibilityIdentifier("current-weather")
            .popover(isPresented: $showing) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(current.reading.headline).font(.headline)
                    ForEach(current.reading.details, id: \.self) { Text($0) }
                }
                .padding()
                .presentationCompactAdaptation(.popover)
            }
        } else {
            // Holds the place so the page switch beside it doesn't move when a forecast arrives.
            Text("— °").monospacedDigit().foregroundStyle(.secondary)
                .accessibilityLabel("No forecast yet")
                .accessibilityIdentifier("current-weather")
        }
    }
}

/// An entry's weather as one muted line, once the server has looked it up.
struct EntryWeatherText: View {
    let weather: EntryWeather?

    var body: some View {
        if let weather {
            Text(weather.summary).font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("entry-weather")
        }
    }
}

/// The Daily page's weather: now, sun times and the day hour by hour, as the
/// desktop's Lifestyle card shows it.
struct WeatherCard: View {
    let weather: WeatherDay?

    var body: some View {
        if weather?.location == nil && weather != nil {
            Text("No location yet. Allow location access, or set a default location in Lunaschal Settings.")
                .foregroundStyle(.secondary)
        } else if let current = now(weather) {
            let day = current.day
            let hour = day.hours[current.index]
            HStack(spacing: 12) {
                Text(current.reading.condition.icon).font(.largeTitle).accessibilityHidden(true)
                VStack(alignment: .leading) {
                    Text("\(degrees(hour.temperatureC))C").font(.title2.weight(.semibold)).monospacedDigit()
                    Text(current.reading.condition.label + (hour.wetBulbC.map { " · wet bulb \(degrees($0))C" } ?? ""))
                        .font(.subheadline).foregroundStyle(.secondary)
                    if !current.reading.details.isEmpty {
                        Text(current.reading.details.joined(separator: " · "))
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                }
            }
            .accessibilityElement(children: .combine)
            if let rise = day.sunriseTs, let set = day.sunsetTs {
                HStack(spacing: 16) {
                    Text("🌅 \(rise.formatted(date: .omitted, time: .shortened))")
                    Text("🌇 \(set.formatted(date: .omitted, time: .shortened))")
                }
                .font(.caption).foregroundStyle(.secondary)
            }
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(day.hours.enumerated()), id: \.element.id) { offset, item in
                            VStack(spacing: 2) {
                                Text(item.hourTs.formatted(.dateTime.hour())).font(.caption2).foregroundStyle(.secondary)
                                Text(day.condition(item).icon)
                                Text(degrees(item.temperatureC)).font(.caption).monospacedDigit()
                            }
                            .padding(.horizontal, 6).padding(.vertical, 4)
                            .background(offset == current.index ? Color.accentColor.opacity(0.2) : .clear,
                                        in: RoundedRectangle(cornerRadius: 6))
                            // Forecast hours are faded; elapsed ones are what happened.
                            .opacity(item.isActual ? 1 : 0.7)
                            .id(item.id)
                        }
                    }
                }
                .onAppear { proxy.scrollTo(hour.id, anchor: .center) }
            }
        } else {
            Text("No forecast yet. It appears after the next sync.").foregroundStyle(.secondary)
        }
    }
}
