import SwiftUI
import LunaschalCore

/// More → Learning: review due cards, approve what's been generated, and
/// browse the deck, as in the desktop's Learning tab.
struct LearningView: View {
    enum Page: String, CaseIterable, Identifiable {
        case review = "Review", queue = "Queue", browse = "Browse"
        var id: Self { self }
    }

    @ObservedObject var learning: LearningModel
    @State private var page = Page.review

    var body: some View {
        VStack(spacing: 0) {
            Picker("Page", selection: $page) {
                ForEach(Page.allCases) { page in
                    Text(label(page)).tag(page)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.vertical, 8)
            .accessibilityIdentifier("learning-page")

            if let problem = learning.problem {
                ContentUnavailableView("Learning needs your server", systemImage: "graduationcap",
                                       description: Text(problem))
            } else {
                switch page {
                case .review: LearningReviewPane(learning: learning)
                case .queue: LearningQueuePane(learning: learning)
                case .browse: LearningBrowsePane(learning: learning)
                }
            }
        }
        .navigationTitle("Learning")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { LearningFilterMenu(learning: learning) } }
        .alert("Learning", isPresented: Binding(get: { learning.error != nil }, set: { if !$0 { learning.error = nil } })) {
            Button("OK") { learning.error = nil }
        } message: { Text(learning.error ?? "") }
        .task { await learning.reload() }
        .onDisappear { learning.stopPolling() }
    }

    private func label(_ page: Page) -> String {
        switch page {
        case .review: learning.stats.due > 0 ? "Review (\(learning.stats.due))" : "Review"
        case .queue: learning.queue.isEmpty ? "Queue" : "Queue (\(learning.queue.count))"
        case .browse: "Browse"
        }
    }
}

/// Narrows everything to one folder and/or one tag, like the desktop's pills.
private struct LearningFilterMenu: View {
    @ObservedObject var learning: LearningModel

    var body: some View {
        Menu {
            if !learning.folders.isEmpty {
                Picker("Folder", selection: $learning.filter.folderId) {
                    Text("All folders").tag(String?.none)
                    ForEach(learning.folders) { folder in
                        Text(folder.dueCount > 0 ? "\(folder.name) · \(folder.dueCount) due" : folder.name)
                            .tag(Optional(folder.id))
                    }
                }
            }
            if !learning.tags.isEmpty {
                Picker("Tag", selection: $learning.filter.tag) {
                    Text("All tags").tag(String?.none)
                    ForEach(learning.tags) { tag in
                        Text("#\(tag.name) · \(tag.count)").tag(Optional(tag.name))
                    }
                }
            }
            if learning.folders.isEmpty && learning.tags.isEmpty {
                Text("No folders or tags yet")
            }
        } label: {
            Label("Filter", systemImage: learning.filter == LearningFilter()
                  ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
        }
        .accessibilityIdentifier("learning-filter")
    }
}

private struct LearningStatsLine: View {
    let stats: LearningStats

    var body: some View {
        HStack(spacing: 10) {
            Text("\(stats.total) cards")
            Text("\(stats.due) due").foregroundStyle(.orange)
            Text("\(stats.pending) queued").foregroundStyle(.purple)
            Text("\(stats.learning) learning").foregroundStyle(.blue)
            Text("\(stats.mastered) mastered").foregroundStyle(.green)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }
}

// MARK: Review

private struct LearningReviewPane: View {
    @ObservedObject var learning: LearningModel

    var body: some View {
        if let session = learning.session {
            if session.isEmpty {
                ContentUnavailableView {
                    Label("All caught up!", systemImage: "checkmark.seal")
                } description: {
                    Text("No cards due for review right now.")
                } actions: {
                    Button("Check again") { Task { await learning.reload() } }
                }
            } else if let result = session.result {
                LearningResultCard(learning: learning, session: session, result: result)
                    .id(result.id)
            } else if let card = session.card {
                LearningAnswerCard(learning: learning, session: session, card: card)
                    .id(card.id)
            }
        } else {
            ProgressView().frame(maxHeight: .infinity)
        }
    }
}

private struct LearningProgress: View {
    let label: String
    let position: Int
    let total: Int
    let tint: Color

    var body: some View {
        VStack(spacing: 6) {
            ProgressView(value: Double(position), total: Double(max(total, 1))).tint(tint)
            Text("\(label) \(position) of \(total)").font(.footnote).foregroundStyle(.secondary)
        }
    }
}

/// The answering pass: type an answer (the keyboard's mic dictates) or flip
/// past the card. Grading happens in the background; results come after the
/// last card.
private struct LearningAnswerCard: View {
    @ObservedObject var learning: LearningModel
    let session: LearningSession
    let card: LearningCard
    @State private var answer = ""
    /// Any of the answer came through the mic, so it's saved as spoken.
    @State private var usedVoice = false
    @StateObject private var dictation: LearningDictation
    @FocusState private var focused: Bool

    init(learning: LearningModel, session: LearningSession, card: LearningCard) {
        self.learning = learning
        self.session = session
        self.card = card
        _dictation = StateObject(wrappedValue: LearningDictation(capture: learning.capture) { [weak learning] in learning?.transport() })
    }

    private var canCheck: Bool {
        !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !learning.sending && dictation.status == .idle
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                LearningStatsLine(stats: learning.stats)
                LearningProgress(label: "Card", position: session.position, total: session.total, tint: .accentColor)
                MarkdownText(text: card.question)
                    .font(.title3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                TextField("Type your answer…", text: $answer, axis: .vertical)
                    .lineLimit(3...8)
                    .padding(10)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
                    .focused($focused)
                    .accessibilityIdentifier("learning-answer")
                if let error = dictation.error {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                HStack {
                    Button {
                        Task { if await learning.submit(answer, voice: usedVoice) { answer = ""; usedVoice = false } }
                    } label: {
                        Text("Check Answer").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canCheck)
                    .accessibilityIdentifier("learning-check")
                    LearningMicButton(status: dictation.status) {
                        focused = false
                        Task {
                            await dictation.toggle { text in
                                answer = answer.isEmpty ? text : "\(answer) \(text)"
                                usedVoice = true
                            }
                        }
                    }
                    Button("Flip") { Task { await learning.skip() } }
                        .buttonStyle(.bordered)
                        .disabled(learning.sending)
                        .accessibilityIdentifier("learning-flip")
                }
                .controlSize(.large)
                Text("Answers are checked in the background — results after the last card. Flip skips a card; its answer is shown at the end.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .onAppear { focused = true }
        .onDisappear { dictation.cancel() }
    }
}

/// The results pass: the card's answer, yours, the grader's verdict, and the
/// four ratings with its suggestion picked out. Tapping one rates the card.
private struct LearningResultCard: View {
    @ObservedObject var learning: LearningModel
    let session: LearningSession
    let result: LearningSession.Answer

    var body: some View {
        let grade = learning.grade(of: result)
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                LearningProgress(label: "Result", position: session.position, total: session.answers.count, tint: .green)
                MarkdownText(text: result.card.question)
                    .font(.title3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                LearningBox(title: "Answer") { MarkdownText(text: result.card.answer) }
                if let text = result.text {
                    LearningBox(title: "Your answer") { Text(text).textSelection(.enabled) }
                }
                switch grade {
                case .pending:
                    Label("Checking your answer…", systemImage: "hourglass")
                        .font(.subheadline).foregroundStyle(.secondary)
                case .error:
                    Label("Automatic grading failed — rate yourself.", systemImage: "exclamationmark.triangle")
                        .font(.subheadline).foregroundStyle(.orange)
                case let .done(coverage, _, gradedAs):
                    LearningCoverageView(coverage: coverage)
                    // A dictated answer is tidied before grading; say what was graded.
                    if result.voice, !gradedAs.isEmpty {
                        Text("Graded as: \u{201C}\(gradedAs)\u{201D}").font(.caption).foregroundStyle(.secondary)
                    }
                    if let summary = coverage.speechSummary {
                        Button {
                            Task { await learning.speak(summary) }
                        } label: {
                            Label(learning.speaking ? "Speaking…" : "Replay", systemImage: "speaker.wave.2")
                        }
                        .font(.footnote)
                        .disabled(learning.speaking)
                        .frame(maxWidth: .infinity)
                        .accessibilityIdentifier("learning-replay")
                    }
                case nil:
                    EmptyView()
                }
                Text(suggestion(grade) == nil ? "How well did you know this?" : "How hard was it to recall? (suggestion highlighted)")
                    .font(.subheadline).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity)
                HStack(spacing: 8) {
                    ForEach(LearningRating.allCases) { rating in
                        LearningRatingButton(rating: rating, suggested: suggestion(grade) == rating) {
                            Task { await learning.rate(rating) }
                        }
                        .disabled(learning.sending)
                    }
                }
            }
            .padding()
        }
        .onAppear { learning.speakIfNew(result) }
        .onChange(of: grade) { _, _ in learning.speakIfNew(result) }
        // Moving on stops whatever was still being read.
        .onDisappear { learning.stopSpeaking() }
    }

    private func suggestion(_ grade: LearningGrade?) -> LearningRating? {
        if case let .done(_, suggested, _) = grade { return suggested }
        return nil
    }
}

/// The desktop's 🎤: red with Stop while recording, a spinner while the
/// server transcribes.
private struct LearningMicButton: View {
    let status: LearningDictation.Status
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            switch status {
            case .recording: Label("Stop", systemImage: "stop.fill")
            case .starting, .transcribing: ProgressView()
            case .idle: Label("Dictate", systemImage: "mic")
            }
        }
        .labelStyle(.iconOnly)
        .buttonStyle(.bordered)
        .tint(status == .recording ? .red : nil)
        .disabled(status == .starting || status == .transcribing)
        .accessibilityLabel(status == .recording ? "Stop" : status == .transcribing ? "Transcribing" : "Dictate")
        .accessibilityIdentifier("learning-mic")
    }
}

private struct LearningRatingButton: View {
    let rating: LearningRating
    let suggested: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(rating.label)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(.white)
                .background(color.opacity(suggested ? 1 : 0.7), in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.primary, lineWidth: suggested ? 2 : 0))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("learning-rate-\(rating.label)")
        .accessibilityHint(suggested ? "Suggested" : "")
    }

    private var color: Color {
        switch rating { case .again: .red; case .hard: .orange; case .good: .yellow; case .easy: .green }
    }
}

private struct LearningBox<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title.uppercased()).font(.caption).foregroundStyle(.secondary)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// CoverageResult.tsx: which of the answer's claims yours covered.
private struct LearningCoverageView: View {
    let coverage: ClaimCoverage

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !coverage.summary.isEmpty { Text(coverage.summary).font(.subheadline) }
            ForEach(Array(coverage.claims.enumerated()), id: \.offset) { _, claim in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: claim.covered ? "checkmark" : "xmark")
                        .foregroundStyle(claim.covered ? .green : .red)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(claim.text).foregroundStyle(claim.covered ? .primary : .secondary)
                        if !claim.essential || !claim.note.isEmpty {
                            HStack(spacing: 6) {
                                if !claim.essential { Text("nuance").foregroundStyle(.secondary) }
                                if !claim.note.isEmpty { Text("(\(claim.note))").foregroundStyle(.orange) }
                            }
                            .font(.caption)
                        }
                    }
                }
                .font(.subheadline)
            }
            if coverage.gated {
                Text("Quick check: your answer didn't resemble the stored one, so no detailed comparison was run.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: Queue

/// Generated cards waiting for approval: approve, steer a regeneration, or deny.
private struct LearningQueuePane: View {
    @ObservedObject var learning: LearningModel
    @State private var regenerating: LearningCard?
    @State private var direction = ""
    @State private var duplicate: (card: LearningCard, similarID: String, question: String, score: Double)?
    @State private var working: String?

    var body: some View {
        List {
            if learning.queue.isEmpty {
                ContentUnavailableView("Queue is empty", systemImage: "tray",
                                       description: Text("Cards generated from a brain-dump, a journal entry or chat land here first."))
                    .listRowBackground(Color.clear)
            }
            ForEach(learning.queue) { card in
                VStack(alignment: .leading, spacing: 10) {
                    LearningCardLabels(card: card)
                    LearningBox(title: "Question") { MarkdownText(text: card.question) }
                    LearningBox(title: "Answer") { MarkdownText(text: card.answer) }
                    HStack {
                        Button("Approve") { Task { await approve(card) } }
                            .buttonStyle(.borderedProminent).tint(.green)
                        Button("Regenerate…") { direction = ""; regenerating = card }
                            .buttonStyle(.bordered)
                        Spacer()
                        Button("Deny", role: .destructive) {
                            Task { working = card.id; await learning.deny(card); working = nil }
                        }
                        .buttonStyle(.bordered)
                    }
                    .disabled(working != nil)
                    if working == card.id { ProgressView().frame(maxWidth: .infinity) }
                }
                .padding(.vertical, 4)
            }
        }
        .listStyle(.plain)
        .refreshable { await learning.refresh() }
        .alert("Regenerate card", isPresented: Binding(get: { regenerating != nil }, set: { if !$0 { regenerating = nil } })) {
            TextField("e.g. too broad, split it", text: $direction)
            Button("Regenerate") {
                guard let card = regenerating else { return }
                Task { working = card.id; _ = await learning.regenerate(card, direction: direction); working = nil }
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Say what to change and the card is generated again.") }
        .confirmationDialog("Similar card exists", isPresented: Binding(get: { duplicate != nil }, set: { if !$0 { duplicate = nil } }),
                            titleVisibility: .visible, presenting: duplicate) { hint in
            Button("Keep both") { Task { _ = await learning.approve(hint.card, force: true) } }
            Button("Replace the old card") { Task { await learning.replace(hint.similarID, with: hint.card) } }
            Button("Delete the new card", role: .destructive) { Task { await learning.deny(hint.card) } }
            Button("Cancel", role: .cancel) {}
        } message: { hint in
            Text("\(Int((hint.score * 100).rounded()))% similar to: \(hint.question)")
        }
    }

    private func approve(_ card: LearningCard) async {
        working = card.id
        defer { working = nil }
        if case let .duplicate(similarID, question, _, score) = await learning.approve(card) {
            duplicate = (card, similarID, question, score)
        }
    }
}

private struct LearningCardLabels: View {
    let card: LearningCard

    var body: some View {
        let labels = (card.derivedFrom != nil ? ["follow-up"] : []) + (card.sourceType.map { [$0] } ?? [])
            + card.tags.map { "#\($0)" }
        if !labels.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(labels, id: \.self) { label in
                        Text(label).font(.caption)
                            .padding(.horizontal, 8).padding(.vertical, 2)
                            .background(.quaternary, in: Capsule())
                    }
                }
            }
        }
    }
}

// MARK: Browse

/// The live deck for the current filter: tap a card to edit it, swipe to delete.
private struct LearningBrowsePane: View {
    @ObservedObject var learning: LearningModel
    @State private var editing: LearningCard?
    @State private var deleting: LearningCard?

    var body: some View {
        List {
            if learning.cards.isEmpty {
                ContentUnavailableView("No cards", systemImage: "rectangle.stack",
                                       description: Text("Approved cards show here."))
                    .listRowBackground(Color.clear)
            }
            ForEach(learning.cards) { card in
                Button { editing = card } label: { LearningBrowseRow(card: card) }
                    .buttonStyle(.plain)
                    .swipeActions {
                        Button("Delete", role: .destructive) { deleting = card }
                    }
            }
        }
        .listStyle(.plain)
        .refreshable { await learning.loadCards() }
        .sheet(item: $editing) { card in
            NavigationStack { LearningCardEditor(learning: learning, card: card) }
        }
        .confirmationDialog("Delete this card?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible, presenting: deleting) { card in
            Button("Delete", role: .destructive) { Task { await learning.delete(card) } }
        } message: { _ in Text("Its review history goes with it.") }
    }
}

private struct LearningBrowseRow: View {
    let card: LearningCard

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(card.isDue() ? "Due" : "Scheduled")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(card.isDue() ? .orange : .blue)
                if card.revisedFrom != nil { Text("revised").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                if let due = card.dueDate {
                    Text("Next: \(due.formatted(date: .abbreviated, time: .omitted))")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Text(card.question).font(.body).lineLimit(3)
            Text(card.answer).font(.subheadline).foregroundStyle(.secondary).lineLimit(3)
            if !card.tags.isEmpty {
                Text(card.tags.map { "#\($0)" }.joined(separator: " ")).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

/// Edits a card's wording and tags. New wording retires the card and makes a
/// revised one; the server keeps its schedule unless the meaning changed.
private struct LearningCardEditor: View {
    @ObservedObject var learning: LearningModel
    let card: LearningCard
    @Environment(\.dismiss) private var dismiss
    @State private var question: String
    @State private var answer: String
    @State private var tags: String
    @State private var saving = false

    init(learning: LearningModel, card: LearningCard) {
        self.learning = learning
        self.card = card
        _question = State(initialValue: card.question)
        _answer = State(initialValue: card.answer)
        _tags = State(initialValue: card.tags.joined(separator: ", "))
    }

    private var changed: Bool {
        question != card.question || answer != card.answer || tags != card.tags.joined(separator: ", ")
    }

    var body: some View {
        Form {
            Section("Question") { TextField("Question", text: $question, axis: .vertical).lineLimit(2...10) }
            Section("Answer") { TextField("Answer", text: $answer, axis: .vertical).lineLimit(2...14) }
            Section {
                TextField("rust, memory", text: $tags)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            } header: { Text("Tags") } footer: { Text("Separate tags with commas.") }
            if let error = learning.error {
                Text(error).foregroundStyle(.orange)
            }
            if question != card.question || answer != card.answer {
                Text("Changing the wording saves a new version of the card. Its review schedule resets only if the meaning changed.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Edit card")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            ToolbarItem(placement: .confirmationAction) {
                if saving { ProgressView() } else {
                    Button("Save") {
                        Task {
                            saving = true
                            learning.error = nil
                            if await learning.save(card, question: question, answer: answer, tags: tags) { dismiss() }
                            saving = false
                        }
                    }
                    .disabled(!changed || question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              || answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }
}
