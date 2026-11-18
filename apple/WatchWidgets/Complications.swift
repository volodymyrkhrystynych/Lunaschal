import SwiftUI
import WidgetKit
import LunaschalCore

@main
struct LunaschalComplications: WidgetBundle {
    var body: some Widget {
        LauncherComplication()
        StateComplication()
        HabitsComplication()
        RecordComplication()
        TranscribeComplication()
        TodoComplication()
        RecordControl()
        TranscribeControl()
        TodoControl()
    }
}

// MARK: - What the face reads

/// Everything a complication can show: the clip going, if any, the work /
/// leisure / mix state, and how many habits are left today.
struct WatchEntry: TimelineEntry {
    let date: Date
    let recording: RecordingStatus?
    var state: ActivityState?
    /// Nil until the phone has sent its habits.
    var habitsLeft: Int?
    var habitsTotal = 0
}

/// Reads the shared container. The app reloads these timelines whenever a
/// clip starts or stops, the state switches, or the phone sends a glance; the
/// one change nothing announces is the 4am rollover, so that is an entry.
struct WatchProvider: TimelineProvider {
    func placeholder(in context: Context) -> WatchEntry { WatchEntry(date: Date(), recording: nil) }

    func getSnapshot(in context: Context, completion: @escaping (WatchEntry) -> Void) {
        completion(context.isPreview ? sample() : entries().first!)
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<WatchEntry>) -> Void) {
        completion(Timeline(entries: entries(), policy: .never))
    }

    private func entries() -> [WatchEntry] {
        let now = Date()
        let recording = WatchComplication.recordingURL.flatMap(RecordingStatus.load(from:))
        let glance = WatchComplication.glanceURL.flatMap(WatchGlance.load(from:)) ?? WatchGlance()
        let local = WatchComplication.stateURL.flatMap(ActivitySwitch.load(from:))
        let state = ActivitySwitch.latest([local, glance.state])?.state
        return [now, TodoWidgetContent.nextRollover(after: now)].map { date in
            WatchEntry(date: date, recording: recording, state: state,
                       habitsLeft: glance.left(on: DayKey.of(date)), habitsTotal: glance.habitsTotal)
        }
    }

    /// The face editor's preview.
    private func sample() -> WatchEntry {
        WatchEntry(date: Date(), recording: nil, state: .work, habitsLeft: 3, habitsTotal: 5)
    }
}

/// A glyph on the face's own background, for the circular families.
struct Glyph: View {
    let symbol: String
    var tint: Color?

    var body: some View {
        ZStack {
            AccessoryWidgetBackground()
            Image(systemName: symbol).font(.title2).foregroundStyle(tint ?? .primary)
        }
    }
}

// MARK: - State

/// Work, leisure or mix in the circular slot. In full colour it is the same
/// glyph the app and the phone draw; on a tinted face colour is gone, so mix
/// keeps a half-filled disc behind its briefcase to stay unlike work.
struct StateFace: View {
    let state: ActivityState?
    @Environment(\.widgetRenderingMode) private var mode

    var body: some View {
        if mode == .fullColor {
            ZStack {
                AccessoryWidgetBackground()
                ActivityGlyph(state: state).padding(3)
            }
        } else {
            ZStack {
                AccessoryWidgetBackground()
                if state == .mix {
                    Circle().fill(.white.opacity(0.35))
                        .mask(HStack(spacing: 0) { Rectangle(); Color.clear })
                        .widgetAccentable()
                }
                Image(systemName: state?.symbol ?? "circle.dashed").font(.title3.weight(.semibold))
                    .widgetAccentable()
            }
        }
    }
}

/// Opens the app on its grid, where the state tiles are: switching is never
/// one stray tap on the face.
struct StateComplication: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: WatchComplication.stateKind, provider: WatchProvider()) { entry in
            StateView(state: entry.state)
                .widgetURL(WatchLink.home.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName("Work or leisure")
        .description("Shows whether you're working, at leisure, or both.")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline])
    }
}

struct StateView: View {
    let state: ActivityState?
    @Environment(\.widgetFamily) private var family

    var body: some View {
        let title = state?.title ?? "No state"
        switch family {
        case .accessoryCorner:
            Image(systemName: state?.symbol ?? "circle.dashed").font(.title2).widgetAccentable()
                .widgetLabel(title)
        case .accessoryInline:
            Label(title, systemImage: state?.symbol ?? "circle.dashed")
        default:
            StateFace(state: state)
        }
    }
}

// MARK: - Habits

struct HabitsComplication: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: WatchComplication.habitsKind, provider: WatchProvider()) { entry in
            HabitsView(left: entry.habitsLeft, total: entry.habitsTotal)
                .widgetURL(WatchLink.home.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName("Habits left")
        .description("How many of today's habits are still to do.")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline])
    }
}

struct HabitsView: View {
    let left: Int?
    let total: Int
    @Environment(\.widgetFamily) private var family

    private var text: String {
        guard let left else { return "Habits" }
        return left == 0 ? "Habits done" : "\(left) habit\(left == 1 ? "" : "s") left"
    }

    var body: some View {
        switch family {
        case .accessoryCorner:
            Text(left.map(String.init) ?? "–")
                .font(.system(size: 18, weight: .semibold, design: .rounded))
                .widgetLabel {
                    if let left, total > 0 {
                        Gauge(value: Double(total - left), in: 0...Double(total)) { Text("Habits") }
                    } else { Text("Habits") }
                }
        case .accessoryInline:
            Label(text, systemImage: "checkmark.circle")
        default:
            HabitsRing(left: left, total: total)
        }
    }
}

/// A ring that fills as habits are ticked, with the number left inside.
struct HabitsRing: View {
    let left: Int?
    let total: Int

    var body: some View {
        if let left, total > 0 {
            Gauge(value: Double(total - left), in: 0...Double(total)) {
                Text("Habits")
            } currentValueLabel: {
                if left == 0 { Image(systemName: "checkmark") } else { Text("\(left)") }
            }
            .gaugeStyle(.accessoryCircularCapacity)
            .widgetAccentable()
            .accessibilityLabel(left == 0 ? "Habits done" : "\(left) habits left")
        } else {
            Glyph(symbol: "checkmark.circle")
        }
    }
}

// MARK: - Launcher

/// The long slot on the Modular faces: To-do, Habits, Record, State,
/// Transcribe. The three microphones never touch, so a tap that lands a
/// button off opens something harmless rather than the wrong recording.
struct LauncherComplication: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: WatchComplication.launcherKind, provider: WatchProvider()) { entry in
            LauncherView(entry: entry)
                .widgetURL(WatchLink.home.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName("Lunaschal")
        .description("To-do, habits, record, state and transcribe in one row.")
        .supportedFamilies([.accessoryRectangular])
    }
}

struct LauncherView: View {
    let entry: WatchEntry

    var body: some View {
        HStack(spacing: 3) {
            recording(.todo)
            button(.home, caption: entry.habitsLeft.map { $0 == 0 ? "Done" : "\($0) left" } ?? "Habits") {
                HabitsRing(left: entry.habitsLeft, total: entry.habitsTotal)
            }
            recording(.record)
            button(.home, caption: entry.state?.title ?? "State") { StateFace(state: entry.state) }
            recording(.transcribe)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder private func recording(_ choice: RecordingChoice) -> some View {
        if let status = entry.recording, choice.matches(status) {
            button(choice.link, caption: nil, timer: status.startedAt) {
                Glyph(symbol: "stop.fill", tint: .red)
            }
        } else {
            button(choice.link, caption: choice == .transcribe ? "Text" : choice.title) {
                Glyph(symbol: choice.symbol)
            }
        }
    }

    private func button<Icon: View>(_ link: WatchLink, caption: String?, timer: Date? = nil,
                                    @ViewBuilder icon: () -> Icon) -> some View {
        Link(destination: link.url) {
            VStack(spacing: 2) {
                icon().clipShape(Circle()).aspectRatio(1, contentMode: .fit)
                Group {
                    if let timer { Text(timer, style: .timer).monospacedDigit() } else { Text(caption ?? "") }
                }
                .font(.system(size: 10)).lineLimit(1).minimumScaleFactor(0.6)
            }
            .frame(maxWidth: .infinity)
        }
    }
}

extension RecordingChoice {
    var widgetKind: String {
        switch self {
        case .record: return WatchComplication.recordKind
        case .transcribe: return WatchComplication.transcribeKind
        case .todo: return WatchComplication.todoKind
        }
    }
    var controlKind: String {
        switch self {
        case .record: return WatchComplication.recordControl
        case .transcribe: return WatchComplication.transcribeControl
        case .todo: return WatchComplication.todoControl
        }
    }
    var link: WatchLink {
        switch self {
        case .record: return .record
        case .transcribe: return .transcribe
        case .todo: return .todo
        }
    }
}

// MARK: - Record and Transcribe

/// A tap opens the app, which starts a clip at once; tapped while that clip
/// is going, it stops and saves it.
struct RecordingComplication {
    let choice: RecordingChoice

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: choice.widgetKind, provider: WatchProvider()) { entry in
            RecordingGlyph(choice: choice, status: choice.matches(entry.recording) ? entry.recording : nil)
                .widgetURL(choice.link.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName(choice.title)
        .description(Text(choice.summary))
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline])
    }
}

struct RecordingGlyph: View {
    let choice: RecordingChoice
    let status: RecordingStatus?
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if let status { recording(since: status.startedAt) } else { idle }
    }

    @ViewBuilder private var idle: some View {
        switch family {
        case .accessoryCorner:
            Image(systemName: choice.symbol).font(.title2).widgetLabel(choice.title)
        case .accessoryInline:
            Label(choice.title, systemImage: choice.symbol)
        default:
            Glyph(symbol: choice.symbol).accessibilityLabel(choice.title)
        }
    }

    /// On: the elapsed time takes the glyph's place. Red alone is not enough —
    /// most faces draw complications tinted or accented, which drops the
    /// colour, and a red mic then looked exactly like an idle one.
    @ViewBuilder private func recording(since start: Date) -> some View {
        let going = choice.going
        switch family {
        case .accessoryCorner:
            Text(start, style: .timer)
                .font(.system(size: 15, weight: .semibold, design: .rounded)).monospacedDigit()
                .minimumScaleFactor(0.6)
                .foregroundStyle(.red)
                .widgetAccentable()
                .widgetLabel { Text(going).foregroundStyle(.red) }
        case .accessoryInline:
            Label { Text(start, style: .timer).monospacedDigit() } icon: { Image(systemName: choice.symbol) }
        default:
            ZStack {
                AccessoryWidgetBackground()
                // A full ring, the shape a running timer has, so the face
                // reads "going" in any rendering mode.
                Circle().strokeBorder(.red, lineWidth: 3).widgetAccentable()
                VStack(spacing: 0) {
                    Image(systemName: choice.symbol).font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.red).widgetAccentable()
                    Text(start, style: .timer)
                        .font(.system(size: 14, weight: .semibold, design: .rounded)).monospacedDigit()
                        .minimumScaleFactor(0.5)
                        .lineLimit(1)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                }
                .padding(.horizontal, 6)
            }
            .accessibilityLabel("Stop \(choice.title.lowercased())")
        }
    }
}

// WidgetKit makes each widget with `init()`, so every choice is its own type.
struct RecordComplication: Widget { var body: some WidgetConfiguration { RecordingComplication(choice: .record).body } }
struct TranscribeComplication: Widget { var body: some WidgetConfiguration { RecordingComplication(choice: .transcribe).body } }
struct TodoComplication: Widget { var body: some WidgetConfiguration { RecordingComplication(choice: .todo).body } }
