import SwiftUI
import LunaschalCore

struct SleepWeekView: View {
    let week: SleepWeek?
    let error: String?
    @State private var selected: String?

    private var days: [SleepNight] { week?.ending(on: DayKey.of(Date())) ?? [] }
    private var night: SleepNight? { days.first { $0.date == selected } ?? days.last }
    private var scale: Double { max(480, days.map { ($0.asleepMinutes ?? 0) + ($0.awakeMinutes ?? 0) }.max() ?? 0) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let error { Text(error).font(.footnote).foregroundStyle(.secondary) }
            if days.isEmpty {
                Text("No sleep readings yet. Enable sleep sharing in Apple Health and sync your iPhone.")
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                Text("Hours asleep and recorded awake time. Select a night for details.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack(alignment: .bottom, spacing: 6) {
                    ForEach(days) { day in
                        Button { selected = day.date } label: {
                            VStack(spacing: 4) {
                                VStack(spacing: 0) {
                                    Spacer(minLength: 0)
                                    if let minutes = day.asleepMinutes {
                                        Rectangle().fill(.orange).frame(height: (day.awakeMinutes ?? 0) / scale * 110)
                                        Rectangle().fill(.indigo).frame(height: minutes / scale * 110)
                                    } else {
                                        Text("—").foregroundStyle(.secondary)
                                    }
                                }.frame(height: 110)
                                Text(String(day.date.suffix(5))).font(.caption2)
                                Text(day.asleepMinutes.map { String(format: "%.1fh", $0 / 60) } ?? "—").font(.caption2)
                            }
                            .frame(maxWidth: .infinity).padding(3)
                            .background(night?.date == day.date ? Color.primary.opacity(0.08) : .clear)
                            .clipShape(RoundedRectangle(cornerRadius: 4))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("\(day.date): \(day.asleepMinutes.map(duration) ?? "no sleep reading"), \(day.interruptions.map { "\($0) recorded interruptions" } ?? "interruptions unknown")")
                        .accessibilityAddTraits(night?.date == day.date ? .isSelected : [])
                    }
                }
                HStack {
                    Label("Asleep", systemImage: "square.fill").foregroundStyle(.indigo)
                    Label("Awake", systemImage: "square.fill").foregroundStyle(.orange)
                }.font(.caption)
                if let night {
                    Text("Night ending \(night.date)").font(.subheadline.bold())
                    if let minutes = night.asleepMinutes {
                        Text("\(duration(minutes)) asleep · \(night.interruptions ?? 0) recorded interruptions · \(Int((night.awakeMinutes ?? 0).rounded())) min awake")
                            .font(.footnote)
                        Text(night.efficiency.map { "\(Int($0.rounded()))% sleep efficiency" }
                             ?? "Sleep efficiency unavailable: incomplete recording.")
                            .font(.footnote)
                    } else {
                        Text(night.start == nil ? "No sleep reading for this night." : "Only time in bed was recorded; sleep duration and quality are unknown.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if week?.days.last?.date != DayKey.of(Date()) {
                    Text("Cached readings · sync to refresh").font(.caption).foregroundStyle(.secondary)
                }
                Text("Efficiency is time asleep divided by the sleep window, a measure of continuity. It does not measure how rested you feel. Gaps in recording are not counted as interruptions.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func duration(_ minutes: Double) -> String {
        let total = Int(minutes.rounded())
        return "\(total / 60)h \(total % 60)m"
    }
}

#if DEBUG
/// Opt-in offline UI fixture, matching NewspaperFixture's simulator pattern.
enum SleepWeekFixture {
    static func seedIfAsked(at url: URL) throws {
        guard ProcessInfo.processInfo.arguments.contains("-sleepWeekFixture") else { return }
        let days: [[String: Any]] = (0..<7).reversed().map { offset in
            let date = Calendar.current.date(byAdding: .day, value: -offset, to: Date())!
            var day: [String: Any] = ["date": DayKey.of(date)]
            if offset < 2 {
                day.merge(["start": date.timeIntervalSince1970 - 28800, "end": date.timeIntervalSince1970,
                           "asleepMinutes": offset == 0 ? 480 : 465, "awakeMinutes": offset == 0 ? 0 : 15,
                           "interruptions": offset, "efficiency": offset == 0 ? 100.0 : 96.9,
                           "unobservedMinutes": 0]) { _, new in new }
            }
            return day
        }
        try JSONSerialization.data(withJSONObject: ["days": days]).write(to: url, options: .atomic)
    }
}
#endif
