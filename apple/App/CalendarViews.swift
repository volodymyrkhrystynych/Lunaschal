import SwiftUI
import LunaschalCore

/// The Journal tab's other page: the web calendar's phone day view, drawn
/// natively from the replica so it works offline. A 4am-to-4am hour grid,
/// events as thin lines with their label at the foot, all-day chips above,
/// and "+" for a new event that is saved here first and sent when it can be.
struct CalendarPage: View {
    @ObservedObject var model: CaptureModel
    @State private var day = DayKey.of(Date())
    @State private var picking = false
    @State private var creating: CalendarDraft?
    @State private var opened: CalendarOccurrence?
    @State private var editingSleep = false
    /// What dragging an event does: move it, or (toggled at the bottom left) change its length.
    @AppStorage("calendarDragChangesLength") private var dragChangesLength = false
    /// The event being dragged and where it would land, in offset minutes.
    @State private var dragging: (id: String, start: Int, end: Int)?

    /// Points per minute: the web's default 2x zoom, a 120pt hour.
    private let scale: CGFloat = 2

    private var today: String { DayKey.of(Date()) }
    private var plan: CalendarDayPlan {
        CalendarTimeline.plan(day: day, events: model.calendarEvents, exceptions: model.calendarExceptions,
                              labelMinutes: Int((labelHeight / scale).rounded(.up)))
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            if !plan.allDay.isEmpty { chips }
            Divider()
            timeline
        }
        .overlay(alignment: .bottomLeading) {
            Button { dragChangesLength.toggle() } label: {
                Label(dragChangesLength ? "Length" : "Move",
                      systemImage: dragChangesLength ? "arrow.up.and.down.square" : "hand.draw")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 14).frame(height: 44)
                    .background(Capsule().fill(dragChangesLength ? AnyShapeStyle(.tint) : AnyShapeStyle(.regularMaterial)))
                    .foregroundStyle(dragChangesLength ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
                    .shadow(radius: 2)
            }
            .accessibilityLabel("Dragging an event")
            .accessibilityValue(dragChangesLength ? "changes its length" : "moves it")
            .accessibilityIdentifier("calendar-drag-mode")
            .padding(16)
        }
        .overlay(alignment: .bottomTrailing) {
            Button {
                let slot = CalendarTimeline.newEventSlot(day: day)
                creating = CalendarDraft(title: "", date: slot.date, time: slot.time, endTime: slot.endTime)
            } label: {
                Image(systemName: "plus").font(.title2.weight(.semibold)).foregroundStyle(.white)
                    .frame(width: 56, height: 56).background(Circle().fill(.tint)).shadow(radius: 4)
            }
            .accessibilityLabel("New event")
            .accessibilityIdentifier("calendar-new-event")
            .padding(16)
        }
        .navigationTitle("Calendar")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(item: $opened) { occurrence in
            CalendarEventDetail(model: model, occurrence: occurrence)
        }
        .sheet(item: $creating) { draft in
            EventForm(draft: draft) { draft, _ in model.queueCalendar(.create(draft)) }
        }
        .sheet(isPresented: $picking) {
            DayPicker(day: $day).presentationDetents([.medium])
        }
        .sheet(isPresented: $editingSleep) {
            SleepEditor(day: day, sleep: model.sleepDays[day]) { wake, sleep in
                model.queueCalendar(.sleep(date: day, wake: wake, sleep: sleep))
            }
            .presentationDetents([.medium, .large])
        }
        .task(id: day) { await model.refreshSleep(day) }
    }

    private var bands: [SleepBand] { model.sleepDays[day].map { CalendarSleep.bands($0) } ?? [] }

    private var header: some View {
        HStack {
            Button { day = CalendarTimeline.addingDays(-1, to: day) } label: { Image(systemName: "chevron.left") }
                .accessibilityLabel("Previous day")
            Spacer()
            Button { picking = true } label: {
                Text(Self.label(day, today: today)).font(.headline)
            }
            .accessibilityIdentifier("calendar-day")
            .accessibilityHint("Choose a day")
            if day != today {
                Button("Today") { day = today }.font(.subheadline)
            }
            Spacer()
            Button { editingSleep = true } label: { Image(systemName: "moon.zzz") }
                .accessibilityLabel("Wake and sleep times")
                .accessibilityIdentifier("calendar-sleep")
                .padding(.trailing, 12)
            Button { day = CalendarTimeline.addingDays(1, to: day) } label: { Image(systemName: "chevron.right") }
                .accessibilityLabel("Next day")
        }
        .padding(.horizontal, 16).padding(.vertical, 8)
    }

    private var chips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(plan.allDay) { occurrence in
                    Button { opened = occurrence } label: {
                        Text(occurrence.event.title.isEmpty ? "Untitled" : occurrence.event.title)
                            .font(.caption).padding(.horizontal, 10).padding(.vertical, 5)
                            .background(Capsule().fill(Color.accentColor.opacity(0.15)))
                    }
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 8)
        }
    }

    private var timeline: some View {
        ScrollViewReader { proxy in
            ScrollView {
                ZStack(alignment: .topLeading) {
                    VStack(spacing: 0) {
                        ForEach(Array(CalendarTimeline.displayHours.enumerated()), id: \.offset) { index, hour in
                            HourRow(hour: hour, first: index == 0, height: 60 * scale).id(index)
                        }
                    }
                    // Under the grid and the events: a band is somewhere to tap, never in the way of a drag.
                    ForEach(bands) { band in
                        SleepBandView(band: band, scale: scale) { editingSleep = true }
                    }
                    .allowsHitTesting(dragging == nil)
                    if day == today { NowLine(scale: scale) }
                    ForEach(plan.timed) { item in
                        let live = dragging.flatMap { $0.id == item.id ? ($0.start, $0.end) : nil }
                        EventLine(item: item, start: live?.0 ?? item.startMinutes, end: live?.1 ?? item.endMinutes,
                                  scale: scale, pending: model.pendingCalendarIDs.contains(item.occurrence.event.id),
                                  changesLength: dragChangesLength, dragged: live != nil)
                            .gesture(drag(item))
                            .accessibilityAction { opened = item.occurrence }
                    }
                }
                .frame(height: CGFloat(CalendarTimeline.minutesPerDay) * scale, alignment: .top)
                .padding(.trailing, 8)
                // Room for "+" below the last hour.
                .padding(.bottom, 80)
            }
            .onAppear { scroll(proxy) }
            .onChange(of: day) { _, _ in scroll(proxy) }
        }
        .simultaneousGesture(DragGesture(minimumDistance: 40).onEnded { drag in
            // A sideways swipe pages the day, as a calendar's does.
            guard dragging == nil, abs(drag.translation.width) > 80, abs(drag.translation.width) > 2 * abs(drag.translation.height) else { return }
            day = CalendarTimeline.addingDays(drag.translation.width < 0 ? 1 : -1, to: day)
        })
    }

    /// One gesture for tap and drag, as on the web: a touch that hardly moves
    /// opens the event; one that moves drags it, on the 5-minute grid, and
    /// is saved on release (queued, so it works offline).
    private func drag(_ item: TimedOccurrence) -> some Gesture {
        DragGesture(minimumDistance: 0, coordinateSpace: .global)
            .onChanged { value in
                guard abs(value.translation.height) > 6 || dragging?.id == item.id else { return }
                let delta = CalendarTimeline.snapped(Double(value.translation.height / scale))
                let range = dragChangesLength
                    ? CalendarTimeline.resized(start: item.startMinutes, end: item.endMinutes, by: delta)
                    : CalendarTimeline.moved(start: item.startMinutes, end: item.endMinutes, by: delta)
                dragging = (item.id, range.start, range.end)
            }
            .onEnded { _ in
                guard let landed = dragging, landed.id == item.id else {
                    opened = item.occurrence
                    return
                }
                dragging = nil
                if let change = CalendarTimeline.reschedule(item, day: day, start: landed.start, end: landed.end) {
                    model.queueCalendar(change)
                }
            }
    }

    /// Now on today, 8am on any other day, with an hour of headroom.
    private func scroll(_ proxy: ScrollViewProxy) {
        var wall = CalendarTimeline.defaultHour * 60
        if day == today {
            let parts = Calendar.current.dateComponents([.hour, .minute], from: Date())
            wall = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
        }
        let hour = max(0, CalendarTimeline.offset(fromWall: wall) / 60 - 1)
        DispatchQueue.main.async { proxy.scrollTo(hour, anchor: .top) }
    }

    static func label(_ day: String, today: String) -> String {
        if day == today { return "Today" }
        guard let date = date(day) else { return day }
        return date.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }

    static func date(_ iso: String) -> Date? {
        let parts = iso.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return Calendar.current.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }

    static func iso(_ date: Date) -> String {
        let parts = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}

private let gutter: CGFloat = 52
private let lineWidth: CGFloat = 28
private let laneGutter: CGFloat = 6
/// Long enough to tap, whatever the duration.
private let minLineLength: CGFloat = 44
/// One label's height: the plan stacks labels in rows this tall.
private let labelHeight: CGFloat = 28

private struct HourRow: View {
    let hour: Int
    let first: Bool
    let height: CGFloat

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            // The top rule sits on the scroll edge, where a label would be cut in half.
            Text(first ? "" : CalendarTimeline.hourLabel(hour))
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .frame(width: gutter - 4, alignment: .trailing).offset(y: -6)
            // Midnight is brighter: it's where the date under the grid changes.
            Rectangle().fill(Color.primary.opacity(hour == 0 ? 0.3 : 0.08)).frame(height: 1)
        }
        .frame(height: height, alignment: .top)
    }
}

private struct NowLine: View {
    let scale: CGFloat

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            let parts = Calendar.current.dateComponents([.hour, .minute], from: context.date)
            let offset = CalendarTimeline.offset(fromWall: (parts.hour ?? 0) * 60 + (parts.minute ?? 0))
            HStack(spacing: 0) {
                Circle().fill(.red).frame(width: 8, height: 8)
                Rectangle().fill(.red).frame(height: 1.5)
            }
            .padding(.leading, gutter - 4)
            .offset(y: CGFloat(offset) * scale - 4)
            .accessibilityHidden(true)
        }
    }
}

/// One event: a vertical line as long as it lasts, its title and time at the
/// foot, beside it — the web's L. Overlaps share their hours in lanes side by
/// side. Coloured by its categories, one stripe each.
private struct EventLine: View {
    let item: TimedOccurrence
    /// Where it's drawn, in offset minutes — moved along while it's dragged.
    let start: Int
    let end: Int
    let scale: CGFloat
    let pending: Bool
    let changesLength: Bool
    let dragged: Bool

    private var event: CalendarEvent { item.occurrence.event }
    private var length: CGFloat { max(minLineLength, CGFloat(end - start) * scale) }
    private var categories: [String] { event.categoryTags.filter { CalendarCategory.colors[$0] != nil } }
    private var time: String {
        dragged ? "\(CalendarTimeline.time(CalendarTimeline.wall(fromOffset: start))) – "
            + CalendarTimeline.time(CalendarTimeline.wall(fromOffset: end))
            : eventTimeLabel(item.occurrence)
    }

    var body: some View {
        // The line is the event: what's tapped and dragged, and its frame.
        // The label hangs beside its group's last line, at the foot, moved
        // up a row when another label already sits there.
        stripes.frame(width: lineWidth, height: length).clipShape(Capsule())
            .overlay {
                if pending { Capsule().strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])).foregroundStyle(.secondary) }
            }
            .overlay(alignment: .bottom) {
                // Where a length drag pulls from.
                if changesLength { Capsule().fill(.black.opacity(0.45)).frame(width: 16, height: 4).padding(.bottom, 6) }
            }
            // The line alone is the accessible element, so its frame is the line's.
            .accessibilityElement(children: .ignore)
            .accessibilityAddTraits(.isButton)
            .accessibilityLabel("\(event.title), \(time)")
            .accessibilityIdentifier("calendar-event")
            .overlay(alignment: .bottomLeading) {
                label.accessibilityHidden(true)
                    .offset(x: CGFloat(item.labelLane - item.lane) * (lineWidth + laneGutter) + lineWidth + 4,
                            y: -CGFloat(item.labelRow) * labelHeight)
            }
            .scaleEffect(dragged ? 1.04 : 1, anchor: .top)
            .shadow(color: .black.opacity(dragged ? 0.25 : 0), radius: 6)
            .offset(x: gutter + CGFloat(item.lane) * (lineWidth + laneGutter), y: CGFloat(start) * scale)
            .zIndex(dragged ? 100 : Double(item.lane + 1))
    }

    private var label: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 3) {
                Text(event.title.isEmpty ? "Untitled" : event.title)
                    .font(.caption.weight(.medium)).foregroundStyle(.primary).lineLimit(1)
                if item.occurrence.isRecurring { Image(systemName: "repeat").font(.system(size: 9)).foregroundStyle(.secondary) }
                if pending { Image(systemName: "icloud.slash").font(.system(size: 9)).foregroundStyle(.secondary) }
            }
            Text(time).font(.system(size: 10).monospacedDigit()).foregroundStyle(.secondary).lineLimit(1)
        }
        .frame(width: 170, height: labelHeight, alignment: .bottomLeading)
        .fixedSize()
        // The label is grabbed and tapped like the line, as on the web.
        .contentShape(Rectangle())
    }

    /// One vertical stripe per category, in the web's colours; none yet is a flat neutral line.
    @ViewBuilder private var stripes: some View {
        if categories.isEmpty {
            Rectangle().fill(Color.primary.opacity(0.25))
        } else {
            HStack(spacing: 0) {
                ForEach(categories, id: \.self) { Rectangle().fill(Color(hex: CalendarCategory.colors[$0]!)) }
            }
        }
    }
}

/// An asleep span, shaded across the row; tapping it corrects the times.
private struct SleepBandView: View {
    let band: SleepBand
    let scale: CGFloat
    let edit: () -> Void

    var body: some View {
        Rectangle().fill(Color.indigo.opacity(0.14))
            .overlay(alignment: .topLeading) {
                Label(band.label, systemImage: band.kind == .morning ? "sunrise" : "moon")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .padding(.leading, gutter + 4).padding(.top, 3)
            }
            .frame(height: CGFloat(band.endMinutes - band.startMinutes) * scale)
            .offset(y: CGFloat(band.startMinutes) * scale)
            .onTapGesture(perform: edit)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("sleep-band-\(band.kind.rawValue)")
    }
}

/// The day's wake and sleep, corrected by hand. Each starts from what's shown,
/// derived or not; switching one off hands it back to the server's guess from
/// the day's activity.
private struct SleepEditor: View {
    let day: String
    let sleep: SleepDay?
    let save: (String?, String?) -> Bool
    @Environment(\.dismiss) private var dismiss
    @State private var setWake: Bool
    @State private var wake: Date
    @State private var setSleep: Bool
    @State private var bedtime: Date

    init(day: String, sleep: SleepDay?, save: @escaping (String?, String?) -> Bool) {
        self.day = day
        self.sleep = sleep
        self.save = save
        let noon = CalendarPage.date(day) ?? Date()
        func clock(_ ts: Int?, _ fallback: Int) -> Date {
            let wall = ts.map { CalendarTimeline.minutes(CalendarSleep.clock($0)) ?? fallback } ?? fallback
            return Calendar.current.date(bySettingHour: wall / 60, minute: wall % 60, second: 0, of: noon) ?? noon
        }
        _setWake = State(initialValue: sleep?.wakeSource == "manual")
        _wake = State(initialValue: clock(sleep?.wakeAt, 7 * 60))
        _setSleep = State(initialValue: sleep?.sleepSource == "manual")
        _bedtime = State(initialValue: clock(sleep?.sleepAt, 23 * 60))
    }

    private func hhmm(_ date: Date) -> String {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
        return CalendarTimeline.time((parts.hour ?? 0) * 60 + (parts.minute ?? 0))
    }

    /// The server refuses a bedtime that isn't after waking, on the 4am day.
    private var problem: String? {
        guard setWake, setSleep, let w = CalendarTimeline.minutes(hhmm(wake)),
              let s = CalendarTimeline.minutes(hhmm(bedtime)) else { return nil }
        return CalendarTimeline.offset(fromWall: s) <= CalendarTimeline.offset(fromWall: w)
            ? "Bedtime has to come after waking up." : nil
    }

    private func note(_ source: String?) -> String {
        source == "manual" ? "set by you" : source == "auto" ? "from activity" : "not known yet"
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Toggle(isOn: $setWake) {
                        VStack(alignment: .leading) {
                            Text("Woke up")
                            Text(sleep?.wakeAt.map { "\(CalendarSleep.clock($0)) · \(note(sleep?.wakeSource))" }
                                 ?? note(nil)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("sleep-set-wake")
                    if setWake { DatePicker("Time", selection: $wake, displayedComponents: .hourAndMinute) }
                }
                Section {
                    Toggle(isOn: $setSleep) {
                        VStack(alignment: .leading) {
                            Text("Went to sleep")
                            Text(sleep?.sleepAt.map { "\(CalendarSleep.clock($0)) · \(note(sleep?.sleepSource))" }
                                 ?? note(nil)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("sleep-set-sleep")
                    if setSleep { DatePicker("Time", selection: $bedtime, displayedComponents: .hourAndMinute) }
                } footer: {
                    Text(problem ?? "A time before 4am counts as this day — a 01:30 bedtime is tonight. Switched off, a time comes from when you were active.")
                        .foregroundStyle(problem == nil ? Color.secondary : Color.red)
                }
            }
            .navigationTitle("Sleep")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if save(setWake ? hhmm(wake) : nil, setSleep ? hhmm(bedtime) : nil) { dismiss() }
                    }
                    .disabled(problem != nil)
                }
            }
        }
    }
}

private extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255, blue: Double(hex & 0xff) / 255)
    }
}

private func eventTimeLabel(_ occurrence: CalendarOccurrence) -> String {
    if occurrence.event.allDay { return "All day" }
    guard let start = occurrence.time else { return "Any time" }
    return occurrence.endTime.map { "\(start) – \($0)" } ?? start
}

private struct DayPicker: View {
    @Binding var day: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            DatePicker("Day", selection: Binding(
                get: { CalendarPage.date(day) ?? Date() },
                set: { day = CalendarPage.iso($0); dismiss() }
            ), displayedComponents: .date)
            .datePickerStyle(.graphical)
            .padding()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Today") { day = DayKey.of(Date()); dismiss() }
                }
            }
        }
    }
}

/// Which occurrences of a repeating event an edit applies to — the web's
/// "This and future" and "All events".
enum EditScope { case future, all }

/// The six categories as checkboxes, in the web's colours. At most three, as
/// the server keeps.
struct CategoryChecklist: View {
    let selected: Set<String>
    let toggle: (String) -> Void

    var body: some View {
        ForEach(CalendarCategory.all, id: \.self) { category in
            let on = selected.contains(category)
            Button { toggle(category) } label: {
                HStack {
                    Image(systemName: on ? "checkmark.square.fill" : "square")
                        .foregroundStyle(Color(hex: CalendarCategory.colors[category]!))
                    Text(category.capitalized).foregroundStyle(.primary)
                    Spacer()
                    Circle().fill(Color(hex: CalendarCategory.colors[category]!)).frame(width: 10, height: 10)
                }
                // The whole row ticks, not only the words.
                .contentShape(Rectangle())
            }
            // Plain, so the name reads as text rather than a blue link.
            .buttonStyle(.plain)
            .disabled(!on && selected.count >= 3)
            .accessibilityIdentifier("calendar-category-\(category)")
            .accessibilityAddTraits(on ? .isSelected : [])
        }
    }
}

/// Removing an event, offered at the foot of its Edit form: the web's
/// choices, which for a repeating event depend on the occurrence opened.
struct EventDeletion {
    let id: String
    let recurring: Bool
    /// The occurrence the form was opened from.
    let date: String
    let perform: (CalendarChange) -> Bool
}

/// The web's event form, natively, for creating and for editing: title, all
/// day, times, description, tags and the repeat rule. A new event also takes
/// its categories here; an existing one has them on its page, and its Delete here.
struct EventForm: View {
    let id: String
    let editing: Bool
    /// Editing a repeating event: Save asks which occurrences, and the date
    /// stays the series' own.
    let series: Bool
    let save: (CalendarDraft, EditScope) -> Bool
    let deletion: EventDeletion?
    @Environment(\.dismiss) private var dismiss
    @State private var deleting = false
    @State private var title: String
    @State private var date: Date
    @State private var allDay: Bool
    @State private var start: Date
    @State private var end: Date
    @State private var details: String
    @State private var tags: String
    @State private var categories: Set<String>
    @State private var repeatFreq: String
    @State private var repeatInterval: Int
    @State private var weekdays: Set<Int>
    @State private var ends: Bool
    @State private var until: Date
    @State private var askingScope = false
    @FocusState private var titleFocused: Bool

    init(draft: CalendarDraft, editing: Bool = false, series: Bool = false, deletion: EventDeletion? = nil,
         save: @escaping (CalendarDraft, EditScope) -> Bool) {
        id = draft.id
        self.editing = editing
        self.series = series
        self.deletion = deletion
        self.save = save
        let day = CalendarPage.date(draft.date) ?? Date()
        _title = State(initialValue: draft.title)
        _date = State(initialValue: day)
        _allDay = State(initialValue: draft.allDay)
        _start = State(initialValue: Self.clock(draft.time, on: day))
        _end = State(initialValue: Self.clock(draft.endTime ?? draft.time.map {
            CalendarTimeline.time(((CalendarTimeline.minutes($0) ?? 0) + CalendarTimeline.defaultDurationMinutes) % CalendarTimeline.minutesPerDay)
        }, on: day))
        _details = State(initialValue: draft.description ?? "")
        _tags = State(initialValue: (draft.tags ?? []).joined(separator: ", "))
        _categories = State(initialValue: Set(draft.categoryTags ?? []))
        _repeatFreq = State(initialValue: draft.repeatFreq ?? "")
        _repeatInterval = State(initialValue: draft.repeatInterval ?? 1)
        _weekdays = State(initialValue: Set(draft.repeatByweekday ?? []))
        _ends = State(initialValue: draft.repeatUntil != nil)
        _until = State(initialValue: draft.repeatUntil.flatMap(CalendarPage.date)
            ?? Calendar.current.date(byAdding: .month, value: 1, to: day) ?? day)
    }

    private var draft: CalendarDraft {
        CalendarDraft(id: id, title: title, date: CalendarPage.iso(date), time: Self.hhmm(start), endTime: Self.hhmm(end),
                      allDay: allDay, description: details, tags: CalendarDraft.tags(from: tags),
                      // None ticked on a new event is left to the classifier; an edit
                      // leaves them alone, since they're ticked on the event's page.
                      categoryTags: !editing && !categories.isEmpty ? Array(categories) : nil,
                      repeatFreq: repeatFreq.isEmpty ? nil : repeatFreq, repeatInterval: repeatInterval,
                      repeatByweekday: Array(weekdays), repeatUntil: ends ? CalendarPage.iso(until) : nil)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Event title", text: $title).focused($titleFocused)
                        .accessibilityIdentifier("calendar-event-title")
                    Toggle("All day", isOn: $allDay)
                    if !series { DatePicker("Date", selection: $date, displayedComponents: .date) }
                    if !allDay {
                        DatePicker("Starts", selection: $start, displayedComponents: .hourAndMinute)
                            .onChange(of: start) { old, new in
                                // Moving the start keeps the length, as the web's voice edit does.
                                end = end.addingTimeInterval(new.timeIntervalSince(old))
                            }
                        DatePicker("Ends", selection: $end, displayedComponents: .hourAndMinute)
                    }
                }
                Section {
                    TextField("Description (optional)", text: $details, axis: .vertical).lineLimit(2...6)
                    TextField("Tags (comma-separated)", text: $tags)
                        .textInputAutocapitalization(.never)
                }
                // A new event has no page yet to tick them on; an existing
                // one ticks them on its page, outside this form.
                if !editing {
                    Section {
                        CategoryChecklist(selected: categories) { category in
                            if categories.contains(category) { categories.remove(category) } else { categories.insert(category) }
                        }
                    } header: {
                        Text("Categories")
                    } footer: {
                        Text("Left empty, the server picks them from the description.")
                    }
                }
                Section("Repeat") {
                    Picker("Repeats", selection: $repeatFreq) {
                        Text("Never").tag("")
                        Text("Daily").tag("daily")
                        Text("Weekly").tag("weekly")
                        Text("Monthly").tag("monthly")
                        Text("Yearly").tag("yearly")
                    }
                    .onChange(of: repeatFreq) { _, freq in
                        // A bare "weekly" means the date's own weekday.
                        if freq == "weekly" && weekdays.isEmpty {
                            weekdays = [Calendar.current.component(.weekday, from: date) - 1]
                        }
                    }
                    if !repeatFreq.isEmpty {
                        Stepper("Every \(repeatInterval) \(unit)", value: $repeatInterval, in: 1...99)
                        if repeatFreq == "weekly" {
                            HStack {
                                ForEach(0..<7, id: \.self) { weekday in
                                    let on = weekdays.contains(weekday)
                                    Button(Calendar.current.veryShortWeekdaySymbols[weekday]) {
                                        if on { weekdays.remove(weekday) } else { weekdays.insert(weekday) }
                                    }
                                    .buttonStyle(.bordered).tint(on ? .accentColor : .secondary)
                                    .accessibilityAddTraits(on ? .isSelected : [])
                                    .accessibilityLabel(Calendar.current.weekdaySymbols[weekday])
                                }
                            }
                        }
                        Toggle("Ends", isOn: $ends)
                        if ends { DatePicker("Until", selection: $until, in: date..., displayedComponents: .date) }
                    }
                }
                if deletion != nil {
                    Section {
                        Button(series ? "Delete…" : "Delete event", role: .destructive) { deleting = true }
                            .frame(maxWidth: .infinity)
                            .accessibilityIdentifier("calendar-event-delete")
                    }
                }
            }
            .navigationTitle(editing ? "Edit event" : "New event")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        if series { askingScope = true } else if save(draft, .all) { dismiss() }
                    }
                    .disabled(draft.problem != nil)
                }
            }
            // Past days are a record of what happened, so "This and future"
            // comes first and rewriting the past is the deliberate choice.
            .confirmationDialog("Apply the change to which occurrences?", isPresented: $askingScope, titleVisibility: .visible) {
                Button("This and future") { if save(draft, .future) { dismiss() } }
                Button("All events") { if save(draft, .all) { dismiss() } }
            }
            .confirmationDialog(series ? "Remove which occurrences?" : "Delete this event?",
                                isPresented: $deleting, titleVisibility: .visible) {
                if let deletion {
                    if deletion.recurring {
                        Button("This occurrence", role: .destructive) { remove(.skip(id: deletion.id, date: deletion.date)) }
                        Button("This and future", role: .destructive) { remove(.endSeries(id: deletion.id, date: deletion.date)) }
                        // Erases the past occurrences too.
                        Button("Whole series", role: .destructive) { remove(.delete(id: deletion.id)) }
                    } else {
                        Button("Delete event", role: .destructive) { remove(.delete(id: deletion.id)) }
                    }
                }
            }
            .onAppear { if !editing { titleFocused = true } }
        }
    }

    private func remove(_ change: CalendarChange) {
        if deletion?.perform(change) == true { dismiss() }
    }

    private var unit: String {
        let base = ["daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"][repeatFreq] ?? ""
        return repeatInterval == 1 ? base : base + "s"
    }

    private static func clock(_ time: String?, on day: Date) -> Date {
        let wall = time.flatMap(CalendarTimeline.minutes) ?? CalendarTimeline.defaultHour * 60
        return Calendar.current.date(bySettingHour: wall / 60, minute: wall % 60, second: 0, of: day) ?? day
    }

    private static func hhmm(_ date: Date) -> String {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
        return CalendarTimeline.time((parts.hour ?? 0) * 60 + (parts.minute ?? 0))
    }
}

/// An event opened from the day view: its details, its categories ticked
/// right here, and Edit (which also holds Delete).
private struct CalendarEventDetail: View {
    @ObservedObject var model: CaptureModel
    let occurrence: CalendarOccurrence
    @Environment(\.dismiss) private var dismiss
    @State private var editing = false
    @State private var finished = false

    /// The event as it is now, so a tick shows at once; the opened copy if it's gone.
    private var event: CalendarEvent {
        model.calendarEvents.first { $0.id == occurrence.event.id } ?? occurrence.event
    }
    private var pending: Bool { model.pendingCalendarIDs.contains(event.id) }

    var body: some View {
        List {
            Section {
                if let date = CalendarPage.date(occurrence.date) {
                    Text(date, format: .dateTime.weekday(.wide).month().day().year())
                }
                Text(eventTimeLabel(occurrence))
                if let freq = event.repeatFreq, occurrence.isRecurring {
                    let every = event.repeatInterval > 1 ? "Every \(event.repeatInterval) · " : ""
                    Label(every + freq.capitalized + (event.repeatUntil.map { " until \($0)" } ?? ""),
                          systemImage: "repeat")
                }
                if pending {
                    Label("Saved on device · Waiting to sync", systemImage: "icloud.slash").foregroundStyle(.secondary)
                }
            }
            if let description = event.description, !description.isEmpty {
                Section("Description") { Text(description).textSelection(.enabled) }
            }
            if !event.tags.isEmpty {
                Section("Tags") { Text(event.tags.joined(separator: ", ")) }
            }
            Section {
                CategoryChecklist(selected: Set(event.categoryTags)) { category in
                    var tags = Set(event.categoryTags)
                    if tags.contains(category) { tags.remove(category) } else { tags.insert(category) }
                    model.queueCalendar(.categories(id: event.id, tags: Array(tags)))
                }
            } header: {
                Text("Categories")
            } footer: {
                if occurrence.isRecurring { Text("For every occurrence.") }
            }
        }
        .navigationTitle(event.title.isEmpty ? "Untitled" : event.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) { Button("Edit") { editing = true } }
        }
        .sheet(isPresented: $editing, onDismiss: { if finished { dismiss() } }) {
            EventForm(draft: CalendarDraft(editing: event), editing: true, series: occurrence.isRecurring,
                      deletion: EventDeletion(id: event.id, recurring: occurrence.isRecurring,
                                              date: occurrence.occurrenceDate) { change in
                          finished = model.queueCalendar(change)
                          return finished
                      }) { draft, scope in
                let change: CalendarChange = occurrence.isRecurring && scope == .future
                    ? .updateFrom(date: occurrence.occurrenceDate, newID: ULID.make(), draft)
                    : .update(draft)
                finished = model.queueCalendar(change)
                return finished
            }
        }
    }
}
