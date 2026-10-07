import SwiftUI
import LunaschalCore

/// One chapter of a downloaded book, read the way the desktop reader reads it:
/// scrolling is logged as reading spans, the chapter becomes the book's
/// last-read chapter, and the menu at the bottom left takes commentary and
/// bookmarks. Next/Previous move through the book without leaving.
struct TextChapterReader: View {
    @ObservedObject var owner: CaptureModel
    var initialFraction: Double?
    @State private var current: SyncChange
    @State private var book: SyncChange?
    @State private var chapters: [SyncChange] = []
    @State private var position: Int?
    @State private var version = ""
    @State private var status = "Reading position stays on this device."
    @State private var fraction = 0.0
    @State private var userScrolling = false
    @State private var spans = ReadingSpanState()
    @State private var pendingFraction: Double?
    @State private var sheet: ReaderSheet?
    @Environment(\.scenePhase) private var scenePhase
    private var store: ReplicaStore { owner.replica }
    private var ficID: String? { current.data?["ficId"]?.string }

    init(owner: CaptureModel, chapter: SyncChange, initialFraction: Double? = nil, book: SyncChange? = nil) {
        _owner = ObservedObject(wrappedValue: owner)
        self.initialFraction = initialFraction
        _current = State(initialValue: chapter)
        _book = State(initialValue: book)
        _pendingFraction = State(initialValue: initialFraction)
    }

    private var paragraphs: [String] {
        (current.data?["contentText"]?.string ?? "").components(separatedBy: "\n\n")
    }

    private var index: Int? { chapters.firstIndex { $0.id == current.id } }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                ForEach(Array(paragraphs.enumerated()), id: \.offset) { index, text in
                    Text(text).font(.system(.body, design: .serif)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).id(index)
                }
                chapterButtons.padding(.vertical, 24)
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
            if userScrolling { recordScroll(value) }
        }
        .safeAreaInset(edge: .bottom) {
            HStack(spacing: 10) {
                readerMenu
                Text(status).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal).padding(.vertical, 6)
            .background(.bar)
        }
        .navigationTitle(current.title).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let book {
                NavigationLink { BookView(model: owner, book: book) } label: {
                    Label("Chapters", systemImage: "list.bullet")
                }
            }
        }
        .sheet(item: $sheet) { which in
            switch which {
            case .text:
                CommentaryTextSheet(chapterTitle: current.title) { text in
                    guard let ficID, owner.saveCommentary(text, ficID: ficID, chapterID: current.id) else { return false }
                    status = "Commentary saved · Goes to the journal when connected"
                    return true
                }
            case .transcribe:
                CommentaryRecordSheet(recorder: owner.recorder, chapterTitle: current.title) {
                    guard let ficID else { return }
                    await owner.startCommentaryRecording(ficID: ficID, chapterID: current.id)
                } onSaved: {
                    status = "Recording saved · The transcript arrives in the journal"
                }
            }
        }
        .task(id: current.id) { load() }
        .onDisappear { closeSpan(sync: true) }
        .onChange(of: scenePhase) { _, phase in
            guard phase != .active else { return }
            let now = Int(Date().timeIntervalSince1970)
            if let due = spans.takeFlush(now: now, force: true) { owner.queueReading(.span(due)) }
        }
        .onChange(of: position) { _, value in
            guard let value, !version.isEmpty, paragraphs.indices.contains(value) else { return }
            do {
                try store.saveReadingPosition(collection: "fic_chapters", id: current.id, version: version,
                                              offset: value, bookID: ficID)
            } catch { status = "Could not save reading position: \(error.localizedDescription)" }
        }
    }

    private var readerMenu: some View {
        Menu {
            Button("Text", systemImage: "text.cursor") { sheet = .text }
            Button("Transcribe", systemImage: "mic") { sheet = .transcribe }
            Button("Continue", systemImage: "bookmark") { bookmark("continue") }
            Button("Bookmark", systemImage: "star") { bookmark("favorite") }
        } label: {
            Image(systemName: "line.3.horizontal")
                .font(.title3.weight(.semibold))
                .frame(width: 48, height: 48)
                .background(.regularMaterial, in: Circle())
        }
        .accessibilityLabel("Reader menu")
        .accessibilityIdentifier("reader-menu")
    }

    @ViewBuilder
    private var chapterButtons: some View {
        if let index {
            HStack {
                if index > 0 {
                    Button("Previous", systemImage: "chevron.left") { move(to: chapters[index - 1]) }
                }
                Spacer()
                if index + 1 < chapters.count {
                    Button { move(to: chapters[index + 1]) } label: {
                        Label("Next chapter", systemImage: "chevron.right").labelStyle(.titleAndIcon)
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
    }

    private func load() {
        do {
            if let ficID {
                if book == nil { book = try store.record(collection: "fics", id: ficID) }
                if chapters.isEmpty {
                    chapters = try store.relatedRecords(collection: "fic_chapters", field: "ficId", value: ficID)
                        .sorted { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) }
                }
                // The desktop's last-read pointer, so it resumes here too.
                owner.queueReading(.progress(ficId: ficID, chapterId: current.id))
            }
            version = "\(try store.epoch ?? "unknown"):\(current.revision)"
            let saved = try store.readingPosition(collection: "fic_chapters", id: current.id, version: version) ?? 0
            if let fraction = pendingFraction, fraction.isFinite {
                position = Int((min(1, max(0, fraction)) * Double(max(0, paragraphs.count - 1))).rounded())
            } else { position = paragraphs.indices.contains(saved) ? saved : 0 }
            pendingFraction = nil
        } catch { status = "Could not restore reading position: \(error.localizedDescription)" }
    }

    private func move(to chapter: SyncChange) {
        closeSpan(sync: false)
        position = 0
        current = chapter
    }

    private func recordScroll(_ value: Double) {
        guard let ficID else { return }
        let now = Int(Date().timeIntervalSince1970)
        if let closed = spans.recordScroll(now: now, fraction: value, ficId: ficID, chapterId: current.id) {
            owner.queueReading(.span(closed))
        }
        if let due = spans.takeFlush(now: now) { owner.queueReading(.span(due)) }
    }

    private func closeSpan(sync: Bool) {
        if let last = spans.close() { owner.queueReading(.span(last), sync: sync) }
    }

    private func bookmark(_ type: String) {
        do {
            try store.queueBookmark(chapter: current, type: type, fraction: min(1, max(0, fraction)))
            status = type == "continue"
                ? "Continue point saved here · Syncs when connected"
                : "Bookmarked · Syncs when connected"
            owner.requestSync()
        } catch ReplicaError.editAlreadyPending {
            // Only a favorite: a new continue point replaces one still waiting to sync.
            owner.message = "This chapter's bookmark is already waiting to sync."
        } catch { owner.message = error.localizedDescription }
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
