import SwiftUI
import WidgetKit
import LunaschalCore

@main
struct LunaschalComplications: WidgetBundle {
    var body: some Widget {
        PomodoroComplication()
        FocusComplication()
        TimeoutComplication()
        LauncherComplication()
        RecordComplication()
        TranscribeComplication()
        FocusControl()
        TimeoutControl()
        RecordControl()
        TranscribeControl()
    }
}

// MARK: - What the face reads

/// Everything a complication can show: the timer as the app (or a Control)
/// last wrote it, and the clip going, if any.
struct WatchEntry: TimelineEntry {
    let date: Date
    let timer: PomodoroTimer.State
    let recording: RecordingStatus?
}

/// Reads the shared container. The app and the Controls reload these
/// timelines whenever something changes, and a run's end is already an
/// entry, so nothing here needs to poll.
struct WatchProvider: TimelineProvider {
    func placeholder(in context: Context) -> WatchEntry { WatchEntry(date: Date(), timer: .idle, recording: nil) }

    func getSnapshot(in context: Context, completion: @escaping (WatchEntry) -> Void) {
        completion(context.isPreview ? sample() : entries().first!)
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<WatchEntry>) -> Void) {
        completion(Timeline(entries: entries(), policy: .never))
    }

    private func entries() -> [WatchEntry] {
        let timer = WatchComplication.timerURL.flatMap(PomodoroTimer.load(from:)) ?? PomodoroTimer()
        let recording = WatchComplication.recordingURL.flatMap(RecordingStatus.load(from:))
        return timer.glances().map { WatchEntry(date: $0.date, timer: $0.state, recording: recording) }
    }

    /// The face editor's preview: a run partway through reads better than a bare glyph.
    private func sample() -> WatchEntry {
        var timer = PomodoroTimer()
        timer.start(.work, now: Date().addingTimeInterval(-9 * 60))
        return WatchEntry(date: Date(), timer: timer.state, recording: nil)
    }
}

extension TimerChoice {
    var symbol: String { kind.symbol }
    var title: String { self == .focus ? "Focus" : "Timeout" }
    var widgetKind: String { self == .focus ? WatchComplication.focusKind : WatchComplication.timeoutKind }
    var controlKind: String { self == .focus ? WatchComplication.focusControl : WatchComplication.timeoutControl }
    var link: WatchLink { .start(kind) }

    /// The run this button owns: focus owns its breaks, since pressing Focus
    /// during one should show the timer, not start another.
    func owns(_ run: PomodoroTimer.Run) -> Bool {
        self == .focus ? run.kind != .timeout : run.kind == .timeout
    }
}

extension RecordingChoice {
    var symbol: String { self == .record ? "mic.fill" : "waveform" }
    var title: String { self == .record ? "Record" : "Transcribe" }
    var widgetKind: String { self == .record ? WatchComplication.recordKind : WatchComplication.transcribeKind }
    var controlKind: String { self == .record ? WatchComplication.recordControl : WatchComplication.transcribeControl }
    var link: WatchLink { self == .record ? .record : .transcribe }
}

/// A countdown ring, for the circular families.
struct CountdownRing: View {
    let run: PomodoroTimer.Run

    var body: some View {
        ProgressView(timerInterval: run.startedAt...run.endsAt, countsDown: true) {
            EmptyView()
        } currentValueLabel: {
            Text(timerInterval: run.startedAt...run.endsAt, countsDown: true).monospacedDigit()
        }
        .progressViewStyle(.circular)
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

// MARK: - Pomodoro status

struct PomodoroComplication: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: WatchComplication.pomodoroKind, provider: WatchProvider()) { entry in
            PomodoroComplicationView(entry: entry)
                .widgetURL(WatchLink.timer.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName("Pomodoro")
        .description("Time left on the focus or timeout timer.")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular])
    }
}

struct PomodoroComplicationView: View {
    let entry: WatchEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        switch entry.timer {
        case .idle: idle
        case .running(let run): running(run)
        case .finished(let kind): finished(kind)
        }
    }

    @ViewBuilder private var idle: some View {
        switch family {
        case .accessoryCorner:
            Image(systemName: "timer").font(.title2).widgetLabel("Pomodoro")
        case .accessoryInline:
            Label("Pomodoro", systemImage: "timer")
        case .accessoryRectangular:
            VStack(alignment: .leading) {
                Label("Pomodoro", systemImage: "timer").font(.headline)
                Text("Focus 25 · Timeout 10").foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
        default:
            Glyph(symbol: "timer")
        }
    }

    @ViewBuilder private func running(_ run: PomodoroTimer.Run) -> some View {
        let span = run.startedAt...run.endsAt
        switch family {
        case .accessoryCorner:
            Image(systemName: run.kind.symbol).font(.title3)
                .widgetLabel {
                    ProgressView(timerInterval: span, countsDown: true,
                                 label: { EmptyView() }, currentValueLabel: { EmptyView() })
                }
        case .accessoryInline:
            Label { Text(timerInterval: span, countsDown: true) } icon: { Image(systemName: run.kind.symbol) }
        case .accessoryRectangular:
            VStack(alignment: .leading, spacing: 2) {
                Label(run.kind.label, systemImage: run.kind.symbol).font(.headline)
                Text(timerInterval: span, countsDown: true)
                    .font(.system(.title2, design: .rounded)).monospacedDigit()
                ProgressView(timerInterval: span, countsDown: true,
                             label: { EmptyView() }, currentValueLabel: { EmptyView() })
            }.frame(maxWidth: .infinity, alignment: .leading)
        default:
            CountdownRing(run: run)
        }
    }

    @ViewBuilder private func finished(_ kind: PomodoroKind) -> some View {
        let title = kind == .break ? "Break's over" : "\(kind.label) done"
        switch family {
        case .accessoryCorner:
            Image(systemName: "bell.fill").font(.title3).widgetLabel(title)
        case .accessoryInline:
            Label(title, systemImage: "bell.fill")
        case .accessoryRectangular:
            VStack(alignment: .leading) {
                Label(title, systemImage: "bell.fill").font(.headline)
                Text(kind == .work ? "Continue or take a break" : "Tap to continue").foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
        default:
            Glyph(symbol: "bell.fill")
        }
    }
}

// MARK: - Focus and Timeout buttons

/// One button per timer. On the face a tap has to open the app, which then
/// starts the timer at once; pressed again, it shows the timer.
struct TimerButtonComplication {
    let choice: TimerChoice

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: choice.widgetKind, provider: WatchProvider()) { entry in
            TimerButtonView(choice: choice, state: entry.timer)
                .widgetURL(choice.link.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName(choice == .focus ? "Focus 25" : "Timeout 10")
        .description(choice == .focus ? "Starts 25 minutes of focus." : "Starts a 10-minute timeout.")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline])
    }
}

struct TimerButtonView: View {
    let choice: TimerChoice
    let state: PomodoroTimer.State
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if case .running(let run) = state, choice.owns(run) {
            switch family {
            case .accessoryCorner:
                Image(systemName: run.kind.symbol).font(.title3)
                    .widgetLabel {
                        ProgressView(timerInterval: run.startedAt...run.endsAt, countsDown: true,
                                     label: { EmptyView() }, currentValueLabel: { EmptyView() })
                    }
            case .accessoryInline:
                Label { Text(timerInterval: run.startedAt...run.endsAt, countsDown: true) }
                    icon: { Image(systemName: run.kind.symbol) }
            default:
                CountdownRing(run: run)
            }
        } else {
            let minutes = choice.kind.minutes
            switch family {
            case .accessoryCorner:
                Image(systemName: choice.symbol).font(.title2).widgetLabel("\(choice.title) \(minutes)")
            case .accessoryInline:
                Label("\(choice.title) \(minutes)", systemImage: choice.symbol)
            default:
                Glyph(symbol: choice.symbol)
            }
        }
    }
}

// MARK: - Launcher

/// The long slot on the Modular faces: every Watch action in one row, each
/// its own tap target, showing what is already going.
struct LauncherComplication: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: WatchComplication.launcherKind, provider: WatchProvider()) { entry in
            LauncherView(entry: entry)
                .widgetURL(WatchLink.timer.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName("Lunaschal")
        .description("Focus, timeout, record and transcribe in one row.")
        .supportedFamilies([.accessoryRectangular])
    }
}

struct LauncherView: View {
    let entry: WatchEntry

    var body: some View {
        HStack(spacing: 4) {
            timer(.focus)
            timer(.timeout)
            recording(.record)
            recording(.transcribe)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder private func timer(_ choice: TimerChoice) -> some View {
        if case .running(let run) = entry.timer, choice.owns(run) {
            button(choice.link, symbol: run.kind.symbol) {
                Text(timerInterval: run.startedAt...run.endsAt, countsDown: true).monospacedDigit()
            }
        } else {
            button(choice.link, symbol: choice.symbol) { Text(choice.title) }
        }
    }

    @ViewBuilder private func recording(_ choice: RecordingChoice) -> some View {
        if let status = entry.recording, status.mode == choice.mode {
            button(choice.link, symbol: "stop.fill", tint: .red) {
                Text(status.startedAt, style: .timer).monospacedDigit()
            }
        } else {
            button(choice.link, symbol: choice.symbol) { Text(choice == .record ? "Record" : "Text") }
        }
    }

    private func button(_ link: WatchLink, symbol: String, tint: Color? = nil,
                        @ViewBuilder caption: () -> Text) -> some View {
        Link(destination: link.url) {
            VStack(spacing: 2) {
                ZStack {
                    AccessoryWidgetBackground().clipShape(Circle())
                    Image(systemName: symbol).font(.body.weight(.semibold)).foregroundStyle(tint ?? .primary)
                }
                .aspectRatio(1, contentMode: .fit)
                caption().font(.system(size: 11)).lineLimit(1).minimumScaleFactor(0.6)
            }
            .frame(maxWidth: .infinity)
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
            RecordingGlyph(choice: choice, status: entry.recording?.mode == choice.mode ? entry.recording : nil)
                .widgetURL(choice.link.url)
                .containerBackground(.clear, for: .widget)
        }
        .configurationDisplayName(choice.title)
        .description(choice == .record ? "Starts and stops a journal clip." : "Starts and stops a clip that becomes text.")
        .supportedFamilies([.accessoryCircular, .accessoryCorner, .accessoryInline])
    }
}

struct RecordingGlyph: View {
    let choice: RecordingChoice
    let status: RecordingStatus?
    @Environment(\.widgetFamily) private var family

    var body: some View {
        let symbol = status == nil ? choice.symbol : "stop.fill"
        switch family {
        case .accessoryCorner:
            if let status {
                Image(systemName: symbol).font(.title2).foregroundStyle(.red)
                    .widgetLabel { Text(status.startedAt, style: .timer) }
            } else {
                Image(systemName: symbol).font(.title2).widgetLabel(choice.title)
            }
        case .accessoryInline:
            if let status {
                Label { Text(status.startedAt, style: .timer) } icon: { Image(systemName: symbol) }
            } else {
                Label(choice.title, systemImage: symbol)
            }
        default:
            Glyph(symbol: symbol, tint: status == nil ? nil : .red)
                .accessibilityLabel(status == nil ? choice.title : "Stop \(choice.title.lowercased())")
        }
    }
}

// WidgetKit makes each widget with `init()`, so every choice is its own type.
struct FocusComplication: Widget { var body: some WidgetConfiguration { TimerButtonComplication(choice: .focus).body } }
struct TimeoutComplication: Widget { var body: some WidgetConfiguration { TimerButtonComplication(choice: .timeout).body } }
struct RecordComplication: Widget { var body: some WidgetConfiguration { RecordingComplication(choice: .record).body } }
struct TranscribeComplication: Widget { var body: some WidgetConfiguration { RecordingComplication(choice: .transcribe).body } }
