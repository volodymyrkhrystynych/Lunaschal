import SwiftUI
import LunaschalCore

/// One chapter of a downloaded book, read the way the desktop reader reads it:
/// scrolling is logged as reading spans, the chapter becomes the book's
/// last-read chapter, and the menu at the bottom left takes commentary and
/// bookmarks. Next/Previous, above and below the text, move through the
/// chapter's own threadmark category without leaving.
struct TextChapterReader: View {
    @ObservedObject var owner: CaptureModel
    var initialFraction: Double?
    @State private var current: SyncChange
    @State private var book: SyncChange?
    @State private var chapters: [SyncChange] = []
    @State private var position: Int?
    @State private var version = ""
    @State private var blocks: [ChapterBlock] = []
    /// A moment's confirmation above the menu button ("Continue point saved
    /// here"), gone again after a few seconds; nothing sits there otherwise.
    @State private var status: String?
    @State private var statusClear: Task<Void, Never>?
    /// Points, kept on this device only, like the reading position.
    @AppStorage("readerTextSize") private var textSize = ReaderTextSize.standard
    @State private var fraction = 0.0
    @State private var userScrolling = false
    @State private var spans = ReadingSpanState()
    @State private var pendingFraction: Double?
    @State private var sheet: ReaderSheet?
    @State private var unsavedPosition: (String, String, Int, String?)?
    @State private var positionSave: Task<Void, Never>?
    /// Chapters this reader has already considered marking read, so reaching
    /// the end of one asks the store once, not on every scroll tick.
    @State private var autoReadChecked: Set<String> = []
    /// What the forum shows (or will) as the user's reaction on this chapter.
    @State private var reaction: ChapterReaction?
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss
    private var store: ReplicaStore { owner.replica }
    private var ficID: String? { current.data?["ficId"]?.string }

    init(owner: CaptureModel, chapter: SyncChange, initialFraction: Double? = nil, book: SyncChange? = nil) {
        _owner = ObservedObject(wrappedValue: owner)
        self.initialFraction = initialFraction
        _current = State(initialValue: chapter)
        _book = State(initialValue: book)
        _pendingFraction = State(initialValue: initialFraction)
    }

    private var neighbors: (previous: SyncChange?, next: SyncChange?) {
        ChapterCategories.neighbors(of: current.id, in: chapters)
    }
    /// The scroll id of the Next/Previous row above the text. The start of a
    /// chapter lands here rather than on its first paragraph, so the row shows.
    private static let topRow = -1

    var body: some View { lifecycle(chrome) }

    private var chrome: some View {
        page
        // Floating rather than in a bar, so the text runs to the bottom edge.
        .overlay(alignment: .bottomLeading) { floatingControls }
        .navigationTitle(current.title).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) { textSizeMenu }
            if FicLibrary.isLikeable(book: book, chapter: current) {
                ToolbarItem(placement: .topBarTrailing) { likeButton }
            }
            if let book {
                // Back to the list this chapter was opened from, never a new
                // one on top: chapters used to stack up one per pick.
                ToolbarItem(placement: .topBarTrailing) {
                    Button { dismiss() } label: {
                        Label("Chapters", systemImage: "list.bullet")
                    }
                }
            }
        }
    }

    private var page: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: textSize * 0.85) {
                chapterButtons(edge: "top").padding(.bottom, 8).id(Self.topRow)
                ForEach(Array(blocks.enumerated()), id: \.offset) { index, block in
                    ChapterBlockView(owner: owner, block: block, size: textSize).id(index)
                }
                chapterButtons(edge: "bottom").padding(.top, 24)
                    // Clear of the menu button, which floats over the bottom left.
                    .padding(.bottom, 80)
            }
            .scrollTargetLayout()
            .frame(maxWidth: 760, alignment: .leading).padding()
            .frame(maxWidth: .infinity)
        }
        .id(current.id)
        .scrollPosition(id: $position, anchor: .top)
        .onScrollPhaseChange { _, phase in
            // Only the reader's own scrolling counts: restoring a position or
            // a bookmark moves the view too, and proves nothing.
            userScrolling = phase == .interacting || phase == .decelerating
        }
        .onScrollGeometryChange(for: Double.self) { geometry in
            let range = geometry.contentSize.height + geometry.contentInsets.top
                + geometry.contentInsets.bottom - geometry.containerSize.height
            guard range > 0 else { return 0 }
            return min(1, max(0, (geometry.contentOffset.y + geometry.contentInsets.top) / range))
        } action: { _, value in
            fraction = value
            if userScrolling {
                recordScroll(value)
                markReadAtEnd(value)
            }
        }
    }

    private var floatingControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let status {
                Text(status).font(.caption).lineLimit(2)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .transition(.opacity)
            }
            readerMenu
        }
        .padding(.horizontal).padding(.bottom, 8)
        .animation(.default, value: status)
    }

    /// Sheets, loading and saving: what the reader does around the page.
    private func lifecycle<Content: View>(_ content: Content) -> some View {
        content
        .sheet(item: $sheet) { which in
            switch which {
            case .text:
                CommentaryTextSheet(chapterTitle: current.title) { text in
                    guard let ficID, owner.saveCommentary(text, ficID: ficID, chapterID: current.id) else { return false }
                    show("Commentary saved · Goes to the journal when connected")
                    return true
                }
            case .transcribe:
                CommentaryRecordSheet(recorder: owner.recorder, chapterTitle: current.title) {
                    guard let ficID else { return }
                    await owner.startCommentaryRecording(ficID: ficID, chapterID: current.id)
                } onSaved: {
                    show("Recording saved · The transcript arrives in the journal")
                }
            }
        }
        .task(id: current.id) { load() }
        .onDisappear { savePosition(); closeSpan(sync: true) }
        .onChange(of: scenePhase) { _, phase in
            guard phase != .active else { return }
            savePosition()
            let now = Int(Date().timeIntervalSince1970)
            if let due = spans.takeFlush(now: now, force: true) { owner.queueReading(.span(due)) }
        }
        .onChange(of: position) { _, row in
            // The top Next/Previous row is the start of the first paragraph.
            guard let row, !version.isEmpty, blocks.indices.contains(max(0, row)) else { return }
            let value = max(0, row)
            // At most once a second while scrolling, and on the way out: each
            // save is a disk-flushed write on the UI thread, and one per
            // paragraph made scrolling wait on any download writing meanwhile.
            unsavedPosition = (current.id, version, value, ficID)
            positionSave?.cancel()
            positionSave = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard !Task.isCancelled else { return }
                savePosition()
            }
        }
    }

    private var textSizeMenu: some View {
        Menu {
            Button("Larger text", systemImage: "textformat.size.larger") { textSize = ReaderTextSize.larger(textSize) }
                .disabled(textSize >= ReaderTextSize.range.upperBound)
                .accessibilityIdentifier("reader-text-larger")
            Button("Smaller text", systemImage: "textformat.size.smaller") { textSize = ReaderTextSize.smaller(textSize) }
                .disabled(textSize <= ReaderTextSize.range.lowerBound)
                .accessibilityIdentifier("reader-text-smaller")
            Button("Default size", systemImage: "arrow.counterclockwise") { textSize = ReaderTextSize.standard }
                .disabled(textSize == ReaderTextSize.standard)
        } label: {
            Label("Text size", systemImage: "textformat.size")
        }
        .menuActionDismissBehavior(.disabled)
        .accessibilityIdentifier("reader-text-size")
        .accessibilityValue("\(Int(textSize)) points")
    }

    private func show(_ message: String) {
        status = message
        statusClear?.cancel()
        statusClear = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            guard !Task.isCancelled else { return }
            status = nil
        }
    }

    private var readerMenu: some View {
        Menu {
            Button("Text", systemImage: "text.cursor") { sheet = .text }
            Button("Transcribe", systemImage: "mic") { sheet = .transcribe }
            Button("Continue", systemImage: "bookmark") { bookmark("continue") }
            Button("Bookmark", systemImage: "star") { bookmark("favorite") }
            if let link = current.data?["sourceUrl"]?.string, let url = URL(string: link) {
                Button("Open on site", systemImage: "safari") { openOnSite(url) }
                    .accessibilityIdentifier("reader-open-on-site")
            }
            if FicLibrary.isLikeable(book: book, chapter: current) {
                Button("Re-get this chapter", systemImage: "arrow.down.doc") {
                    Task { await owner.refetchChapter(current) }
                }
                .accessibilityIdentifier("reader-refetch-chapter")
            }
            if let book, FicSources.isUpdatable(book.data?["sourceType"]?.string) {
                Button("Get latest chapters", systemImage: "arrow.clockwise") {
                    Task { await owner.checkFicForUpdates(book, deep: false) }
                }
                .accessibilityIdentifier("reader-check-updates")
            }
        } label: {
            Image(systemName: "line.3.horizontal")
                .font(.title3.weight(.semibold))
                .frame(width: 48, height: 48)
                .background(.regularMaterial, in: Circle())
        }
        .accessibilityLabel("Reader menu")
        .accessibilityIdentifier("reader-menu")
    }

    /// Filled once liked; a clock badge while the server has still to make
    /// the like on the forum.
    private var likeButton: some View {
        let liked = reaction?.isLiked ?? false
        return Button {
            setLike(!liked, assumed: false)
            show(liked ? "Like removed · Syncs when connected" : "Liked · Sent to the forum when connected")
        } label: {
            Label(liked ? "Unlike" : "Like",
                  systemImage: reaction?.pending != nil ? "clock.arrow.circlepath" : liked ? "heart.fill" : "heart")
        }
        .tint(liked ? .pink : nil)
        .accessibilityIdentifier("reader-like")
        .accessibilityValue(liked ? (reaction?.pending != nil ? "Liked, waiting to send" : "Liked") : "Not liked")
    }

    /// Opening a chapter on the forum is going there to like it: it shows as
    /// liked here, and the server sends nothing.
    private func openOnSite(_ url: URL) {
        if FicLibrary.isLikeable(book: book, chapter: current) { setLike(true, assumed: true) }
        openURL(url)
    }

    private func setLike(_ liked: Bool, assumed: Bool) {
        guard let ficID else { return }
        owner.likeChapter(bookID: ficID, chapterID: current.id, liked: liked, assumed: assumed)
        reaction = (try? store.reactions(bookID: ficID))?[current.id]
    }

    /// Shown at the top and the bottom of the text; `edge` keeps the two
    /// copies apart for UI tests.
    @ViewBuilder
    private func chapterButtons(edge: String) -> some View {
        let (previous, next) = neighbors
        if previous != nil || next != nil {
            HStack {
                if let previous {
                    Button("Previous", systemImage: "chevron.left") { move(to: previous) }
                        .accessibilityIdentifier("chapter-previous-\(edge)")
                }
                Spacer()
                if let next {
                    Button { move(to: next) } label: {
                        Label("Next chapter", systemImage: "chevron.right").labelStyle(.titleAndIcon)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("chapter-next-\(edge)")
                }
            }
        }
    }

    private func load() {
        do {
            // A chapter from a list is an outline, without its text.
            if current.data?["contentText"] == nil,
               let whole = try store.record(collection: "fic_chapters", id: current.id), !whole.deleted {
                current = whole
            }
            blocks = ChapterText.blocks(html: current.data?["contentHtml"]?.string,
                                        text: current.data?["contentText"]?.string)
            if let ficID {
                if book == nil { book = try store.record(collection: "fics", id: ficID) }
                if chapters.isEmpty { chapters = try store.chapterOutline(bookID: ficID) }
                reaction = try store.reactions(bookID: ficID)[current.id]
                // The desktop's last-read pointer, so it resumes here too.
                owner.queueReading(.progress(ficId: ficID, chapterId: current.id))
            }
            // "b": positions count formatted blocks now, not the old plain-text
            // paragraphs, so a position saved before means nothing here.
            version = "\(try store.epoch ?? "unknown"):\(current.revision):b"
            let saved = try store.readingPosition(collection: "fic_chapters", id: current.id, version: version) ?? 0
            if let fraction = pendingFraction, fraction.isFinite {
                let row = Int((min(1, max(0, fraction)) * Double(max(0, blocks.count - 1))).rounded())
                position = row > 0 ? row : Self.topRow
            } else { position = blocks.indices.contains(saved) && saved > 0 ? saved : Self.topRow }
            pendingFraction = nil
        } catch { show("Could not restore reading position: \(error.localizedDescription)") }
    }

    private func move(to chapter: SyncChange) {
        savePosition()
        closeSpan(sync: false)
        position = Self.topRow
        current = (try? store.record(collection: "fic_chapters", id: chapter.id)) ?? chapter
    }

    private func savePosition() {
        positionSave?.cancel()
        positionSave = nil
        guard let (chapterID, version, offset, bookID) = unsavedPosition else { return }
        unsavedPosition = nil
        do {
            try store.saveReadingPosition(collection: "fic_chapters", id: chapterID, version: version,
                                          offset: offset, bookID: bookID)
        } catch { show("Could not save reading position: \(error.localizedDescription)") }
    }

    private func recordScroll(_ value: Double) {
        guard let ficID else { return }
        let now = Int(Date().timeIntervalSince1970)
        if let closed = spans.recordScroll(now: now, fraction: value, ficId: ficID, chapterId: current.id) {
            owner.queueReading(.span(closed))
        }
        if let due = spans.takeFlush(now: now) { owner.queueReading(.span(due)) }
    }

    /// Scrolling to the end of a chapter marks it read, once.
    private func markReadAtEnd(_ value: Double) {
        guard let ficID, value >= FicLibrary.autoReadFraction, !autoReadChecked.contains(current.id) else { return }
        autoReadChecked.insert(current.id)
        let alreadyRead = (try? store.readChapterIDs(bookID: ficID).contains(current.id)) ?? true
        guard FicLibrary.shouldAutoMark(fraction: value, alreadyRead: alreadyRead) else { return }
        owner.markChapters(bookID: ficID, chapterIDs: [current.id], read: true)
        show("Marked read")
    }

    private func closeSpan(sync: Bool) {
        if let last = spans.close() { owner.queueReading(.span(last), sync: sync) }
    }

    private func bookmark(_ type: String) {
        do {
            try store.queueBookmark(chapter: current, type: type, fraction: min(1, max(0, fraction)))
            show(type == "continue"
                ? "Continue point saved here · Syncs when connected"
                : "Bookmarked · Syncs when connected")
            owner.requestSync()
        } catch ReplicaError.editAlreadyPending {
            // Only a favorite: a new continue point replaces one still waiting to sync.
            owner.message = "This chapter's bookmark is already waiting to sync."
        } catch { owner.message = error.localizedDescription }
    }
}

/// The reader's text size in points: one setting for every book, saved on
/// this device.
enum ReaderTextSize {
    static let standard = 19.0
    static let range = 13.0...35.0
    static let step = 2.0
    static func larger(_ size: Double) -> Double { min(range.upperBound, size + step) }
    static func smaller(_ size: Double) -> Double { max(range.lowerBound, size - step) }
}

/// One formatted block of a chapter: its italics, bold and links kept, quotes
/// indented with a bar, scene breaks centred.
private struct ChapterBlockView: View {
    let owner: CaptureModel
    let block: ChapterBlock
    let size: Double
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        content
            .padding(.leading, CGFloat(block.quoteDepth) * 16)
            .overlay(alignment: .leading) {
                if block.quoteDepth > 0 {
                    Rectangle().fill(.tertiary).frame(width: 3)
                        .padding(.leading, CGFloat(block.quoteDepth - 1) * 16)
                }
            }
    }

    private var frameAlignment: Alignment {
        switch block.alignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }

    private var textAlignment: TextAlignment {
        switch block.alignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }

    @ViewBuilder
    private var content: some View {
        switch block.kind {
        case .rule:
            Divider().padding(.vertical, size * 0.5)
        case .sceneBreak:
            Text(block.text).font(.system(size: size, design: .serif)).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .center).padding(.vertical, size * 0.4)
        case .heading(let level):
            let base = size * (level <= 2 ? 1.4 : 1.15)
            text(base: base).font(.system(size: base, weight: .bold, design: .serif))
                .padding(.top, size * 0.5)
        case .listItem(let marker):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(marker).font(.system(size: size, design: .serif)).foregroundStyle(.secondary)
                text(base: size).font(.system(size: size, design: .serif)).lineSpacing(size * 0.3)
            }
            .padding(.leading, 8)
        case .preformatted:
            text(base: size * 0.85).font(.system(size: size * 0.85, design: .monospaced))
        case .paragraph:
            text(base: size).font(.system(size: size, design: .serif)).lineSpacing(size * 0.3)
        case .image(let src):
            ChapterImageView(owner: owner, src: src, alt: block.text)
                .frame(maxWidth: .infinity, alignment: frameAlignment)
                .padding(.vertical, size * 0.3)
        }
    }

    private func text(base: Double) -> some View {
        Text(attributed(base: base)).textSelection(.enabled)
            .multilineTextAlignment(textAlignment)
            .frame(maxWidth: .infinity, alignment: frameAlignment)
    }

    private func attributed(base: Double) -> AttributedString {
        var out = AttributedString()
        for run in block.runs {
            var piece = AttributedString(run.text)
            var intent: InlinePresentationIntent = []
            if run.style.contains(.bold) { intent.insert(.stronglyEmphasized) }
            if run.style.contains(.italic) { intent.insert(.emphasized) }
            if run.style.contains(.strikethrough) { intent.insert(.strikethrough) }
            if run.style.contains(.code) { intent.insert(.code) }
            if !intent.isEmpty { piece.inlinePresentationIntent = intent }
            if run.style.contains(.underline) { piece.underlineStyle = .single }
            if let font = font(for: run, base: base) { piece.font = font }
            if let background = run.look.background {
                piece.backgroundColor = Color(background)
                if let color = run.look.color { piece.foregroundColor = Color(color) }
            } else if let color = run.look.color {
                // Picked against the forum's white page: kept legible here.
                piece.foregroundColor = Color(color.readable(onDark: scheme == .dark))
            }
            if let link = run.link, let url = URL(string: link) { piece.link = url }
            out += piece
        }
        return out
    }

    /// Nil leaves the block's own font. A run with its own font names its
    /// weight and slant itself, since its font replaces the block's.
    private func font(for run: ChapterRun, base: Double) -> Font? {
        let small = run.style.contains(.small)
        guard run.look.font != nil || run.look.scale != nil || small else { return nil }
        let points = base * (run.look.scale ?? 1) * (small ? 0.8 : 1)
        var font: Font
        if let family = run.look.font?.family, Self.installed(family) {
            font = .custom(family, size: points)
        } else {
            let design: Font.Design
            switch run.look.font?.design ?? .serif {
            case .serif: design = .serif
            case .sansSerif: design = .default
            case .monospaced: design = .monospaced
            case .rounded: design = .rounded
            }
            font = .system(size: points, design: design)
        }
        if run.style.contains(.bold) { font = font.bold() }
        if run.style.contains(.italic) { font = font.italic() }
        return font
    }

    private static let families = Set(UIFont.familyNames.map { $0.lowercased() })
    private static func installed(_ family: String) -> Bool { families.contains(family.lowercased()) }
}

private extension Color {
    init(_ color: ChapterColor) { self.init(red: color.red, green: color.green, blue: color.blue) }
}

/// A picture in a chapter. The server's copy comes from the device, fetched
/// and kept the first time it's needed; one the server couldn't download is
/// loaded from the web, as the desktop reader does, or offered as a link.
private struct ChapterImageView: View {
    let owner: CaptureModel
    let src: String
    let alt: String
    @State private var image: UIImage?
    @State private var missing = false

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFit()
                    .frame(maxWidth: image.size.width)
                    .clipShape(RoundedRectangle(cornerRadius: 4))
                    .accessibilityLabel(alt.isEmpty ? "Picture" : alt)
            } else if let reference = FicImages.reference(src) {
                if missing {
                    Label(alt.isEmpty ? "Picture not on this device" : alt, systemImage: "photo")
                        .font(.footnote).foregroundStyle(.secondary)
                } else {
                    ProgressView().frame(height: 80)
                        .task(id: src) { await load(reference) }
                }
            } else if let url = URL(string: src) {
                AsyncImage(url: url) { phase in
                    if let picture = phase.image {
                        picture.resizable().scaledToFit().accessibilityLabel(alt.isEmpty ? "Picture" : alt)
                    } else if phase.error != nil {
                        Link(destination: url) { Label(alt.isEmpty ? "Picture on the web" : alt, systemImage: "photo") }
                            .font(.footnote)
                    } else {
                        ProgressView().frame(height: 80)
                    }
                }
            }
        }
        .accessibilityIdentifier("chapter-image")
    }

    private func load(_ reference: FicImageReference) async {
        guard let file = await owner.ficImage(reference) else { missing = true; return }
        let loaded = await Task.detached(priority: .userInitiated) { UIImage(contentsOfFile: file.path) }.value
        if let loaded { image = loaded } else { missing = true }
    }
}

private enum ReaderSheet: String, Identifiable {
    case text, transcribe
    var id: String { rawValue }
}

/// Typed commentary on the chapter; Save files it as a journal entry.
private struct CommentaryTextSheet: View {
    let chapterTitle: String
    let save: (String) -> Bool
    @State private var text = ""
    @FocusState private var focused: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            TextEditor(text: $text)
                .focused($focused)
                .padding(.horizontal)
                .accessibilityIdentifier("commentary-text")
                .navigationTitle("Commentary")
                .navigationBarTitleDisplayMode(.inline)
                .safeAreaInset(edge: .top) {
                    Text("On \(chapterTitle)").font(.caption).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal)
                }
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") { if save(text) { dismiss() } }
                            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
                .onAppear { focused = true }
        }
        .presentationDetents([.medium, .large])
    }
}

/// Spoken commentary: one microphone button. Stopping is the save, as on the
/// desktop, and closing the sheet mid-recording stops (and so saves) it too.
private struct CommentaryRecordSheet: View {
    @ObservedObject var recorder: Recorder
    let chapterTitle: String
    let start: () async -> Void
    let onSaved: () -> Void
    @State private var mine: String?
    @Environment(\.dismiss) private var dismiss

    private var recording: Bool { mine != nil && recorder.activeID == mine }
    private var busyElsewhere: Bool { recorder.activeID != nil && !recording }

    var body: some View {
        VStack(spacing: 20) {
            Text("Commentary on \(chapterTitle)").font(.headline).multilineTextAlignment(.center)
            Button {
                if recording { stop() } else {
                    Task {
                        await start()
                        mine = recorder.activeID
                    }
                }
            } label: {
                Image(systemName: recording ? "stop.fill" : "mic.fill")
                    .font(.system(size: 40))
                    .foregroundStyle(.white)
                    .frame(width: 96, height: 96)
                    .background(recording ? Color.red : Color.accentColor, in: Circle())
            }
            .disabled(recorder.isStarting || busyElsewhere)
            .accessibilityLabel(recording ? "Stop and save" : "Record commentary")
            .accessibilityIdentifier("commentary-mic")
            Text(recorder.isStarting ? "Starting…"
                 : recording ? "Recording · Tap to stop and save"
                 : busyElsewhere ? "Another recording is in progress."
                 : "Tap to record. The transcript is added to your journal.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding()
        .presentationDetents([.medium])
        .onDisappear { if recording { stop(dismissing: false) } }
    }

    private func stop(dismissing: Bool = true) {
        recorder.stop()
        mine = nil
        onSaved()
        if dismissing { dismiss() }
    }
}
