import AVKit
import LunaschalCore
import SwiftUI

/// The Journal page, read the way the desktop's feed is: one timeline, newest
/// first, every entry's photos, clips and videos on its card, and a ring in an
/// event's category colours around the entries written during it.
struct JournalFeedView: View {
    @ObservedObject var model: CaptureModel
    @State private var query = ""

    private var searching: Bool { !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    /// What this device holds that the server's record doesn't show yet.
    private var localCaptures: [Capture] {
        let onServer = Set(model.journalRecords.map(\.id))
        return model.captures.filter { capture in
            capture.matchesSearch(query)
                && !(capture.state == .synced && onServer.contains(capture.snapshot?.id ?? capture.id))
        }
    }

    /// Server entries and this device's captures, as one timeline.
    private var items: [FeedItem] {
        let records = model.journalRecords.map {
            FeedItem.entry($0, JournalTimestamp.parse($0.data?["createdAt"]?.string) ?? .distantPast)
        }
        return (records + localCaptures.map { FeedItem.capture($0) }).sorted { $0.time > $1.time }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                if !model.pendingEdits.isEmpty {
                    FeedHeading("Pending edits")
                    ForEach(model.pendingEdits) { edit in
                        NavigationLink { PendingEditView(model: model, edit: edit) } label: {
                            FeedCard {
                                Text(edit.original.title).lineLimit(1)
                                Text(edit.state == "pending" ? "Saved on device · Waiting to sync" : "Needs resolution")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }.buttonStyle(.plain)
                    }
                    FeedHeading("Journal")
                }
                ForEach(blocks(items), id: \.id) { block in
                    switch block {
                    case .item(let item): card(item)
                    case .event(let occurrence, let items):
                        EventGroup(occurrence: occurrence) { ForEach(items, id: \.id) { card($0) } }
                    }
                }
                if model.journalRecords.count < model.journalCount {
                    Button("Load more entries (\(model.journalRecords.count) of \(model.journalCount))") { model.loadMoreJournal() }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
        }
        .background(Color(.systemGroupedBackground))
        .overlay {
            if localCaptures.isEmpty && model.journalRecords.isEmpty && model.pendingEdits.isEmpty {
                ContentUnavailableView(searching ? "No matching entries" : "No captures yet", systemImage: "book.closed")
            }
        }
        .navigationTitle("Journal")
        .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search saved journal")
        .onAppear { model.searchJournal(query) }
        .onChange(of: query) { _, value in model.searchJournal(value) }
    }

    @ViewBuilder private func card(_ item: FeedItem) -> some View {
        switch item {
        case .entry(let record, _): EntryCard(model: model, record: record)
        case .capture(let capture):
            NavigationLink { CaptureDetail(model: model, id: capture.id) } label: {
                LocalCaptureCard(model: model, capture: capture)
            }.buttonStyle(.plain)
        }
    }

    private enum FeedItem {
        case entry(SyncChange, Date)
        case capture(Capture)
        var id: String {
            switch self {
            case .entry(let record, _): return record.id
            case .capture(let capture): return "capture:" + capture.id
            }
        }
        var time: Date {
            switch self {
            case .entry(_, let time): return time
            case .capture(let capture): return capture.createdAt
            }
        }
    }

    private enum Block {
        case item(FeedItem)
        case event(CalendarOccurrence, [FeedItem])
        var id: String {
            switch self {
            case .item(let item): return item.id
            case .event(let occurrence, let items): return "event:\(occurrence.id):\(items.first?.id ?? "")"
            }
        }
    }

    /// The feed cut into runs: an item on its own, or an event's border
    /// around the items written during it.
    private func blocks(_ items: [FeedItem]) -> [Block] {
        let spans = searching ? [] : JournalEventGroups.spans(times: items.map(\.time), occurrences: model.journalOccurrences)
        var out: [Block] = []
        var index = 0
        var next = spans.makeIterator()
        var span = next.next()
        while index < items.count {
            if let current = span, current.start == index {
                out.append(.event(current.occurrence, Array(items[current.start...current.end])))
                index = current.end + 1
                span = next.next()
            } else {
                out.append(.item(items[index]))
                index += 1
            }
        }
        return out
    }
}

private struct FeedHeading: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(.footnote.weight(.semibold)).foregroundStyle(.secondary).textCase(.uppercase)
            .padding(.top, 4)
    }
}

private struct FeedCard<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 8) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 12))
            .contentShape(RoundedRectangle(cornerRadius: 12))
    }
}

/// An event's border: one ring per category, widening outward, the web's
/// `categoryRingBoxShadow`.
private struct EventGroup<Content: View>: View {
    let occurrence: CalendarOccurrence
    @ViewBuilder let content: Content

    private static var ring: CGFloat { 3 }
    private var colors: [Color] {
        occurrence.event.categoryTags.compactMap { CalendarCategory.colors[$0] }.map(Color.init(hex:))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline) {
                    Text(occurrence.event.title).font(.caption.weight(.semibold))
                    Spacer()
                    Text(eventTimeLabel(occurrence)).font(.caption).foregroundStyle(.secondary)
                }
                if let description = occurrence.event.description, !description.isEmpty {
                    Text(description).font(.subheadline)
                }
            }
            .padding(.horizontal, 4)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("journal-event-group")
            content
        }
        .padding(6)
        .overlay {
            ForEach(Array(colors.enumerated()), id: \.offset) { index, color in
                let outset = Self.ring * CGFloat(index)
                RoundedRectangle(cornerRadius: 14 + outset)
                    .strokeBorder(color, lineWidth: Self.ring)
                    .padding(-outset)
            }
        }
        .padding(Self.ring * CGFloat(max(0, colors.count - 1)))
    }
}

/// A capture on its way to the server: its words, its photos, where it stands.
private struct LocalCaptureCard: View {
    @ObservedObject var model: CaptureModel
    let capture: Capture

    var body: some View {
        FeedCard {
            Text(capture.snapshot?.title ?? (capture.text.isEmpty ? (capture.files.first?.name ?? "Recording") : capture.text))
                .lineLimit(2)
            if capture.kind == .food { Label("Food log", systemImage: "fork.knife").font(.caption) }
            let photos = capture.files.filter(\.isImage).compactMap { try? model.store.fileURL($0) }
            if !photos.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(photos, id: \.self) { url in
                            LocalThumbnail(url: url).frame(height: 90)
                        }
                    }
                }
            }
            if !capture.clips.isEmpty || capture.attachmentID != nil {
                Label(capture.clips.count > 1 ? "\(capture.clips.count) recordings" : "Recording", systemImage: "waveform")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text(capture.createdAt, format: .dateTime.month().day().hour().minute())
                .font(.caption).foregroundStyle(.secondary)
            EntryWeatherText(weather: capture.entryWeather)
            Text(captureStatus(capture)).font(.caption)
        }
    }
}

private struct LocalThumbnail: View {
    let url: URL
    @State private var image: UIImage?
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else { Color.secondary.opacity(0.15).aspectRatio(1, contentMode: .fit) }
        }
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .task(id: url) { image = await Thumbnails.load(url, size: 240) }
    }
}

enum Thumbnails {
    /// A picture scaled down off the main thread: a phone photo is 12 MP.
    static func load(_ url: URL, size: CGFloat) async -> UIImage? {
        await Task.detached(priority: .userInitiated) {
            guard let image = UIImage(contentsOfFile: url.path) else { return nil }
            return await image.byPreparingThumbnail(ofSize: fitted(image.size, size)) ?? image
        }.value
    }

    private static func fitted(_ size: CGSize, _ longest: CGFloat) -> CGSize {
        let scale = min(1, longest * 2 / max(size.width, size.height, 1))
        return CGSize(width: size.width * scale, height: size.height * scale)
    }
}

/// One server entry: what was written, then everything hung off it.
private struct EntryCard: View {
    @ObservedObject var model: CaptureModel
    let record: SyncChange

    private var attachments: [JournalAttachmentItem] { model.journalAttachments[record.id] ?? [] }
    private var created: Date? { JournalTimestamp.parse(record.data?["createdAt"]?.string) }
    private var content: String { record.data?["content"]?.string ?? "" }
    private var title: String? {
        guard let title = record.data?["title"]?.string, !title.isEmpty else { return nil }
        return title
    }

    var body: some View {
        FeedCard {
            NavigationLink { JournalRecordView(model: model, record: record) } label: {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(title ?? (content.isEmpty ? "Entry" : "Untitled")).font(.headline).lineLimit(2)
                        Spacer()
                        if let created {
                            Text(created, format: .dateTime.month(.abbreviated).day().hour().minute())
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    EntryWeatherText(weather: EntryWeather.parse(record.data?["weather"]?.string))
                    if !content.isEmpty {
                        Text(content).font(.body).lineLimit(10).multilineTextAlignment(.leading)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("journal-entry")

            let photos = attachments.filter { $0.media == .image }
            if !photos.isEmpty { PhotoStrip(model: model, photos: photos) }
            ForEach(attachments.filter { $0.media == .youtube || $0.media == .video }) { item in
                VideoCard(model: model, item: item)
            }
            ForEach(attachments.filter { $0.media == .audio }) { item in
                AudioRow(model: model, item: item)
            }
            ForEach(attachments.filter { $0.media == .file }) { item in
                NavigationLink {
                    DownloadedMediaView(model: model, collection: "journal_attachments", id: item.id,
                                        mime: item.mime, title: item.name.isEmpty ? "Attachment" : item.name)
                } label: { Label(item.name.isEmpty ? "Attachment" : item.name, systemImage: "doc") }
                .font(.subheadline)
            }
        }
    }
}

// MARK: Photos

/// A strip of fixed-height pictures, each width following its photo, blown
/// up full screen on a tap: the web's journal photo strip.
private struct PhotoStrip: View {
    @ObservedObject var model: CaptureModel
    let photos: [JournalAttachmentItem]
    @State private var shown: JournalAttachmentItem?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(photos) { photo in
                    Button { shown = photo } label: { RemoteThumbnail(model: model, item: photo, height: 120) }
                        .buttonStyle(.plain)
                        .accessibilityLabel(photo.description ?? (photo.name.isEmpty ? "Photo" : photo.name))
                        .accessibilityIdentifier("journal-photo")
                }
            }
        }
        .fullScreenCover(item: $shown) { photo in PhotoViewer(model: model, item: photo) }
    }
}

private struct RemoteThumbnail: View {
    @ObservedObject var model: CaptureModel
    let item: JournalAttachmentItem
    var poster = false
    let height: CGFloat
    @State private var image: UIImage?
    @State private var missing = false

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFit()
            } else {
                ZStack {
                    Color.secondary.opacity(0.15)
                    if missing { Image(systemName: "photo").foregroundStyle(.secondary) } else { ProgressView() }
                }
                .aspectRatio(poster ? 16 / 9 : 1, contentMode: .fit)
            }
        }
        .frame(height: height)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .task(id: item.id) {
            guard image == nil else { return }
            if let file = await model.journalMediaFile(item, thumbnail: poster) {
                image = await Thumbnails.load(file, size: height * 2)
            }
            missing = image == nil
        }
    }
}

private struct PhotoViewer: View {
    @ObservedObject var model: CaptureModel
    let item: JournalAttachmentItem
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var missing = false
    @State private var zoom: CGFloat = 1
    @GestureState private var pinch: CGFloat = 1

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                if let image {
                    GeometryReader { space in
                        ScrollView([.horizontal, .vertical], showsIndicators: false) {
                            Image(uiImage: image).resizable().scaledToFit()
                                .frame(width: space.size.width * zoom * pinch,
                                       height: space.size.height * zoom * pinch)
                        }
                    }
                    .gesture(MagnifyGesture().updating($pinch) { value, state, _ in state = value.magnification }
                        .onEnded { zoom = min(6, max(1, zoom * $0.magnification)) })
                    .onTapGesture(count: 2) { withAnimation { zoom = zoom > 1 ? 1 : 2.5 } }
                } else if missing {
                    ContentUnavailableView("Photo unavailable offline", systemImage: "wifi.slash",
                                           description: Text("Download the library, or open this again when connected."))
                } else {
                    ProgressView().tint(.white)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if let text = item.description ?? item.transcript {
                    Text(text).font(.footnote).foregroundStyle(.white).padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.black.opacity(0.6))
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Done") { dismiss() } }
            }
            .toolbarBackground(.black, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationTitle(item.name).navigationBarTitleDisplayMode(.inline)
        }
        .task {
            if let file = await model.journalMediaFile(item) {
                image = await Task.detached { UIImage(contentsOfFile: file.path) }.value
            }
            missing = image == nil
        }
    }
}

// MARK: Audio

/// A clip played in place, with its transcript and what was heard in it
/// folded underneath.
private struct AudioRow: View {
    @ObservedObject var model: CaptureModel
    let item: JournalAttachmentItem
    @State private var player: AVPlayer?
    @State private var playing = false
    @State private var loading = false
    @State private var failed = false
    @State private var elapsed: Double = 0
    @State private var duration: Double = 0
    @State private var observer: Any?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Button(action: toggle) {
                    ZStack {
                        if loading { ProgressView() }
                        else { Image(systemName: playing ? "pause.fill" : "play.fill").font(.body) }
                    }
                    .frame(width: 36, height: 36)
                    .background(Color.accentColor.opacity(0.15), in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(loading || model.recorder.activeID != nil)
                .accessibilityLabel(playing ? "Pause" : "Play")
                .accessibilityIdentifier("journal-audio-play")
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.name.isEmpty ? "Voice clip" : item.name).font(.subheadline).lineLimit(1)
                    if duration > 0 {
                        ProgressView(value: min(elapsed, duration), total: duration)
                        Text("\(clock(elapsed)) / \(clock(duration))").font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    } else if failed {
                        Text("Unavailable offline").font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            if let transcript = item.transcript {
                DisclosureGroup("Transcript") { Text(transcript).font(.subheadline).textSelection(.enabled) }
                    .font(.caption)
            }
            if let description = item.description {
                DisclosureGroup("What was heard") { Text(description).font(.subheadline).textSelection(.enabled) }
                    .font(.caption)
            }
        }
        .onDisappear { stop() }
    }

    private func toggle() {
        if let player {
            if playing { player.pause() } else { player.play() }
            playing.toggle()
            return
        }
        loading = true
        Task {
            defer { loading = false }
            guard let made = await model.journalPlayer(item) else { failed = true; return }
            try? AVAudioSession.sharedInstance().setCategory(.playback)
            try? AVAudioSession.sharedInstance().setActive(true)
            observer = made.addPeriodicTimeObserver(forInterval: CMTime(seconds: 0.25, preferredTimescale: 600),
                                                    queue: .main) { time in
                MainActor.assumeIsolated {
                    elapsed = time.seconds
                    if let length = made.currentItem?.duration.seconds, length.isFinite { duration = length }
                    if duration > 0, elapsed >= duration - 0.05 { playing = false; made.seek(to: .zero); elapsed = 0 }
                }
            }
            player = made
            made.play()
            playing = true
            failed = false
        }
    }

    private func stop() {
        player?.pause()
        if let observer { player?.removeTimeObserver(observer) }
        observer = nil
        player = nil
        playing = false
    }

    private func clock(_ seconds: Double) -> String {
        guard seconds.isFinite else { return "0:00" }
        let total = Int(seconds)
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

// MARK: Video

/// A video's poster, opened full screen on a tap rather than played inline:
/// an entry can carry several, as on the web. A watched YouTube video plays
/// the server's archived copy and links to where it came from.
private struct VideoCard: View {
    @ObservedObject var model: CaptureModel
    let item: JournalAttachmentItem
    @State private var player: AVPlayer?
    @State private var opening = false
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            switch item.importStatus {
            case "importing":
                Label("Downloading the video…", systemImage: "hourglass").font(.caption).foregroundStyle(.secondary)
            case "error":
                Label("Could not download this video.", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
            default:
                Button(action: open) {
                    ZStack {
                        if item.media == .youtube {
                            RemoteThumbnail(model: model, item: item, poster: true, height: 180)
                        } else {
                            Color.black.frame(height: 180).clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        Image(systemName: opening ? "hourglass" : "play.fill")
                            .font(.title2).foregroundStyle(.white)
                            .padding(14).background(.black.opacity(0.6), in: Circle())
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain)
                .disabled(opening)
                .accessibilityLabel("Play \(item.name.isEmpty ? "video" : item.name)")
                .accessibilityIdentifier("journal-video")
                if failed {
                    Text("This video needs the server.").font(.caption).foregroundStyle(.orange)
                }
            }
            if !item.name.isEmpty { Text(item.name).font(.subheadline).lineLimit(2) }
            if let source = item.sourceURL, let url = URL(string: source) {
                Link("Open on YouTube", destination: url).font(.caption)
            }
            // The one account of a video that may never be rewatched, so it is
            // shown open, as on the web.
            if item.media == .youtube, let description = item.description {
                Text(description).font(.subheadline).textSelection(.enabled)
            }
            if let transcript = item.transcript {
                DisclosureGroup("Transcript") { Text(transcript).font(.subheadline).textSelection(.enabled) }
                    .font(.caption)
            }
        }
        .fullScreenCover(isPresented: Binding(get: { player != nil }, set: { if !$0 { player?.pause(); player = nil } })) {
            if let player {
                VideoPlayer(player: player)
                    .ignoresSafeArea()
                    .overlay(alignment: .topLeading) {
                        Button("Done") { self.player?.pause(); self.player = nil }
                            .buttonStyle(.borderedProminent).padding()
                    }
                    .onAppear { player.play() }
            }
        }
    }

    private func open() {
        opening = true
        Task {
            defer { opening = false }
            try? AVAudioSession.sharedInstance().setCategory(.playback)
            player = await model.journalPlayer(item)
            failed = player == nil
        }
    }
}
