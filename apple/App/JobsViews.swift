import SwiftUI
import LunaschalCore

/// The jobs triage feed, reached from More. Read from the server when it can
/// be, and from the last copy otherwise; Queue and Dismiss go through
/// `CaptureModel`'s outbox, so the card leaves on the tap either way.
@MainActor
final class JobsFeedModel: ObservableObject {
    @Published private(set) var jobs: [FeedJob]
    @Published var sort = JobSort.match
    @Published private(set) var loading = false
    @Published private(set) var error: String?
    /// The card Undo brings back: where it sat, and what was decided.
    @Published private(set) var lastDecision: (job: FeedJob, index: Int, op: JobDecisionOp)?
    /// Cards brought back whose undo the server hasn't had yet, so a refresh
    /// (which still sees them dismissed or queued) doesn't take them away again.
    private var restored: [FeedJob] = []
    let capture: CaptureModel

    init(capture: CaptureModel) {
        self.capture = capture
        jobs = capture.jobStore.cachedFeed()
    }

    func refresh() async {
        guard let api = capture.chatAPI() else {
            error = UIDevice.current.userInterfaceIdiom == .pad
                ? "Sign in under Settings to load new postings."
                : "Sign in under More → Settings to load new postings."
            return
        }
        loading = true
        defer { loading = false }
        do {
            let fetched = try await api.jobFeed(sort: sort)
            let undoing = Set(capture.jobQueue.filter(\.decision.isUndo).map(\.jobID))
            restored = restored.filter { undoing.contains($0.id) }
            jobs = restored.filter { job in !fetched.contains { $0.id == job.id } } + fetched
            error = nil
            try? capture.jobStore.saveFeed(jobs)
            capture.jobStore.prunePostings(keeping: Set(jobs.map(\.id)))
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { return }
            self.error = "Showing the last feed loaded. \(error.localizedDescription)"
        }
    }

    /// Taken out of the copy on screen as well as queued: once the decision
    /// has reached the server the outbox no longer hides the card.
    func decide(_ job: FeedJob, _ decision: JobDecision) {
        let index = jobs.firstIndex { $0.id == job.id } ?? 0
        guard let op = capture.decideJob(job, decision) else { return }
        jobs.removeAll { $0.id == job.id }
        lastDecision = (job, index, op)
        try? capture.jobStore.saveFeed(jobs)
    }

    /// Puts the last decided card back where it was and sends the opposite.
    func undo() {
        guard let last = lastDecision, let inverse = JobDeck.undo(last.op) else { return }
        lastDecision = nil
        guard capture.decideJob(last.job, inverse) != nil else { return }
        if !jobs.contains(where: { $0.id == last.job.id }) {
            jobs.insert(last.job, at: min(last.index, jobs.count))
        }
        restored.append(last.job)
        try? capture.jobStore.saveFeed(jobs)
    }
}

/// A swipe deck on the iPhone, one posting to the screen; the list on iPad,
/// where there is room to compare.
struct JobsFeedView: View {
    @ObservedObject var capture: CaptureModel
    @StateObject private var feed: JobsFeedModel
    @Environment(\.horizontalSizeClass) private var sizeClass

    init(capture: CaptureModel) {
        self.capture = capture
        _feed = StateObject(wrappedValue: JobsFeedModel(capture: capture))
    }

    private var groups: (promising: [FeedJob], rest: [FeedJob]) {
        JobFeed.split(JobFeed.hidingDecided(feed.jobs, capture.jobQueue))
    }

    var body: some View {
        Group {
            if sizeClass == .compact { JobsDeckView(capture: capture, feed: feed) }
            else { list }
        }
        .navigationTitle("Jobs")
        .toolbar {
            if feed.lastDecision != nil {
                ToolbarItem(placement: .primaryAction) {
                    Button("Undo", systemImage: "arrow.uturn.backward") { withAnimation { feed.undo() } }
                        .accessibilityIdentifier("jobs-undo")
                }
            }
        }
        .refreshable { await feed.refresh() }
        .task(id: feed.sort) { await feed.refresh() }
    }

    private var list: some View {
        List {
            if !capture.jobRefusals.isEmpty {
                Section {
                    ForEach(capture.jobRefusals, id: \.self) { Text($0).foregroundStyle(.orange) }
                    Button("Clear") { capture.jobRefusals = [] }
                }
            }
            if let error = feed.error {
                Section { Text(error).font(.footnote).foregroundStyle(.secondary) }
            }
            Section {
                Picker("Sort", selection: $feed.sort) {
                    ForEach(JobSort.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("jobs-sort")
            } footer: {
                if feed.sort == .distance { Text("Remote first, then nearest.") }
            }
            let groups = groups
            if !groups.promising.isEmpty {
                Section("Worth a look (\(groups.promising.count))") {
                    ForEach(groups.promising) { JobCard(job: $0, decide: feed.decide) }
                }
            }
            if !groups.rest.isEmpty {
                Section("\(groups.promising.isEmpty ? "Postings" : "The rest") (\(groups.rest.count))") {
                    ForEach(groups.rest) { JobCard(job: $0, decide: feed.decide) }
                }
            }
            if !capture.jobQueue.isEmpty {
                Section {
                    Text("\(capture.jobQueue.count) decision\(capture.jobQueue.count == 1 ? "" : "s") waiting to reach the server.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
        .overlay {
            if groups.promising.isEmpty && groups.rest.isEmpty {
                if feed.loading { ProgressView() }
                else {
                    ContentUnavailableView("Nothing to triage", systemImage: "briefcase",
                                           description: Text("New postings from your saved searches show up here."))
                }
            }
        }
    }
}

// MARK: The deck

private struct JobsDeckView: View {
    @ObservedObject var capture: CaptureModel
    @ObservedObject var feed: JobsFeedModel
    @State private var drag: CGFloat = 0
    @State private var leaving = false
    @State private var decided = 0

    var body: some View {
        let deck = JobDeck.cards(feed.jobs, capture.jobQueue)
        VStack(spacing: 10) {
            notices
            Picker("Sort", selection: $feed.sort) {
                ForEach(JobSort.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("jobs-sort")
            if feed.sort == .distance {
                Text("Remote first, then nearest.").font(.caption).foregroundStyle(.secondary)
            }
            if let top = deck.cards.first {
                Text(progress(deck))
                    .font(.footnote.weight(.medium)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("jobs-progress")
                GeometryReader { geo in
                    ZStack {
                        if deck.cards.count > 1 {
                            JobDetailCard(job: deck.cards[1], capture: capture)
                                .scaleEffect(0.95).offset(y: 12).opacity(0.6)
                                .allowsHitTesting(false).accessibilityHidden(true)
                        }
                        JobDetailCard(job: top, capture: capture)
                            .overlay(alignment: .top) { stamp }
                            .offset(x: drag)
                            .rotationEffect(.degrees(Double(drag / 25)), anchor: .bottom)
                            .simultaneousGesture(swipe(top, width: geo.size.width))
                            .id(top.id)
                            .transition(.identity)
                    }
                }
                HStack(spacing: 16) {
                    Button { commit(top, .dismiss) } label: {
                        Label("Dismiss", systemImage: "xmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered).tint(.red)
                    .accessibilityIdentifier("job-dismiss-\(top.id)")
                    Button { commit(top, .queue) } label: {
                        Label("Queue", systemImage: "checkmark").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("job-queue-\(top.id)")
                }
                .controlSize(.large)
                .disabled(leaving)
            } else if feed.loading {
                Spacer(); ProgressView(); Spacer()
            } else {
                ContentUnavailableView("Nothing to triage", systemImage: "briefcase",
                                       description: Text("New postings from your saved searches show up here."))
            }
        }
        .padding(.horizontal)
        .padding(.bottom, 8)
        .sensoryFeedback(.impact(weight: .medium), trigger: decided)
    }

    @ViewBuilder private var notices: some View {
        ForEach(capture.jobRefusals, id: \.self) { Text($0).font(.footnote).foregroundStyle(.orange) }
        if !capture.jobRefusals.isEmpty { Button("Clear") { capture.jobRefusals = [] }.font(.footnote) }
        if let error = feed.error { Text(error).font(.footnote).foregroundStyle(.secondary) }
        let waiting = capture.jobQueue.filter { !$0.decision.isUndo }.count
        if waiting > 0 {
            Text("\(waiting) decision\(waiting == 1 ? "" : "s") waiting to reach the server.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func progress(_ deck: (cards: [FeedJob], promising: Int)) -> String {
        let rest = deck.cards.count - deck.promising
        if deck.promising > 0 {
            return "Worth a look: \(deck.promising) left" + (rest > 0 ? " · then \(rest) more" : "")
        }
        return "The rest: \(rest) left"
    }

    /// Which way the card is leaning, written across it as it goes.
    @ViewBuilder private var stamp: some View {
        let strength = min(abs(drag) / 120, 1)
        if drag != 0 {
            Text(drag > 0 ? "QUEUE" : "DISMISS")
                .font(.title.weight(.heavy))
                .foregroundStyle(drag > 0 ? Color.green : Color.red)
                .padding(.horizontal, 12).padding(.vertical, 4)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(drag > 0 ? Color.green : Color.red, lineWidth: 3))
                .rotationEffect(.degrees(drag > 0 ? -12 : 12))
                .opacity(strength)
                .padding(.top, 24)
                .frame(maxWidth: .infinity, alignment: drag > 0 ? .leading : .trailing)
                .padding(.horizontal, 24)
                .allowsHitTesting(false)
        }
    }

    /// Follows only a sideways drag, so the card's own scrolling still works.
    private func swipe(_ job: FeedJob, width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 20)
            .onChanged { value in
                guard !leaving else { return }
                let t = value.translation
                if abs(t.width) > abs(t.height) * 1.5 || drag != 0 { drag = t.width }
            }
            .onEnded { value in
                guard !leaving, drag != 0 else { return }
                let t = value.translation
                if let decision = JobDeck.decision(dx: t.width, dy: t.height,
                                                   velocityX: value.velocity.width, width: width) {
                    commit(job, decision)
                } else {
                    withAnimation(.spring(duration: 0.3)) { drag = 0 }
                }
            }
    }

    /// Throws the card off the side it was decided to, then decides it.
    private func commit(_ job: FeedJob, _ decision: JobDecision) {
        guard !leaving else { return }
        leaving = true
        decided += 1
        withAnimation(.easeIn(duration: 0.22)) { drag = decision == .queue ? 600 : -600 }
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(220))
            feed.decide(job, decision)
            drag = 0
            leaving = false
        }
    }
}

/// Everything there is to decide from, on one scrolling card.
private struct JobDetailCard: View {
    let job: FeedJob
    let capture: CaptureModel
    @Environment(\.openURL) private var openURL
    /// The whole body, formatted; nil until loaded (the excerpt shows meanwhile).
    @State private var postingBlocks: [PostingBlock]?
    @State private var postingNote: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    if let fit = job.fitLabel {
                        Text(fit).font(.caption.weight(.semibold)).foregroundStyle(.tint)
                    }
                    Text(job.title).font(.title2.weight(.bold))
                    if !job.company.isEmpty { Text(job.company).font(.headline) }
                    if !job.location.isEmpty { Text(job.location).foregroundStyle(.secondary) }
                }
                facts
                if !job.triageSummary.isEmpty { Text(job.triageSummary) }
                if !job.triageFlags.isEmpty { flags }
                if let reasons = job.matchReasons, !(reasons.matched.isEmpty && reasons.missing.isEmpty) {
                    keywords(reasons)
                }
                posting
                if let url = URL(string: job.url), !job.url.isEmpty {
                    Button("Open on the job board", systemImage: "safari") { openURL(url) }
                        .buttonStyle(.borderless)
                }
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollBounceBehavior(.basedOnSize)
        .background(RoundedRectangle(cornerRadius: 20).fill(Color(.secondarySystemGroupedBackground)))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(Color(.separator), lineWidth: 0.5))
        .clipShape(RoundedRectangle(cornerRadius: 20))
        .shadow(color: .black.opacity(0.12), radius: 8, y: 4)
        .task(id: job.id) { await loadPosting() }
    }

    /// The posting itself, under the summary: headings, lists and emphasis as
    /// the board wrote them. Until it loads (or offline, if it never was), the
    /// feed's 600-character excerpt stands in.
    @ViewBuilder private var posting: some View {
        if postingBlocks != nil || !job.description.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Divider().padding(.vertical, 4)
                if let blocks = postingBlocks {
                    ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in blockView(block) }
                } else {
                    Text(job.description).font(.callout).foregroundStyle(.secondary)
                    if let postingNote {
                        Text(postingNote).font(.caption).foregroundStyle(.secondary)
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                }
            }
            .accessibilityIdentifier("job-posting")
        }
    }

    @ViewBuilder private func blockView(_ block: PostingBlock) -> some View {
        switch block {
        case .heading(let text):
            inline(text).font(.headline).padding(.top, 6)
        case .item(let text, let marker, let depth):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker).foregroundStyle(.secondary)
                inline(text)
            }
            .font(.callout)
            .padding(.leading, CGFloat(depth) * 16)
        case .paragraph(let text):
            inline(text).font(.callout)
        }
    }

    /// Bold, italics and links inside a block; the blocks themselves are ours.
    private func inline(_ text: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return Text((try? AttributedString(markdown: text, options: options)) ?? AttributedString(text))
    }

    private func loadPosting() async {
        if let cached = capture.jobStore.cachedPosting(job.id) {
            postingBlocks = PostingBlock.parse(cached)
            return
        }
        guard let api = capture.chatAPI() else {
            postingNote = "Only the start of the posting is on the phone. Sign in to read the rest."
            return
        }
        do {
            let markdown = try await api.jobPosting(id: job.id)
            try? capture.jobStore.savePosting(job.id, markdown)
            postingBlocks = PostingBlock.parse(markdown)
        } catch {
            if Task.isCancelled { return }
            postingNote = "Couldn't load the rest of the posting. \(error.localizedDescription)"
        }
    }

    private var facts: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !job.salaryText.isEmpty { Label(job.salaryText, systemImage: "banknote") }
            if let distance = job.distanceText { Label(distance, systemImage: "location") }
            if let percent = job.matchPercent { Label("\(percent)% of the keywords", systemImage: "text.badge.checkmark") }
            if let posted = JobCard.date(job.postedAt ?? job.createdAt) {
                Label { Text(posted, format: .relative(presentation: .named)) } icon: { Image(systemName: "clock") }
            }
        }
        .font(.subheadline).foregroundStyle(.secondary)
    }

    private var flags: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(job.triageFlags.enumerated()), id: \.offset) { _, flag in
                Label {
                    Text(JobFeed.flagLabels[flag.kind] ?? flag.kind).bold()
                        + Text(flag.detail.isEmpty ? "" : " — \(flag.detail)")
                } icon: { Image(systemName: "exclamationmark.triangle") }
            }
        }
        .font(.subheadline).foregroundStyle(.orange)
    }

    private func keywords(_ reasons: FeedJob.MatchReasons) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if !reasons.matched.isEmpty {
                Label(reasons.matched.joined(separator: ", "), systemImage: "checkmark.circle")
                    .foregroundStyle(.green)
            }
            if !reasons.missing.isEmpty {
                Label(reasons.missing.joined(separator: ", "), systemImage: "xmark.circle")
                    .foregroundStyle(.secondary)
            }
        }
        .font(.subheadline)
    }
}

private struct JobCard: View {
    let job: FeedJob
    let decide: (FeedJob, JobDecision) -> Void
    @Environment(\.openURL) private var openURL

    private var subtitle: String {
        [job.company, job.location, job.salaryText].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    private var summary: String {
        job.triageSummary.isEmpty ? job.description : job.triageSummary
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(job.title).font(.headline)
            if !subtitle.isEmpty { Text(subtitle).font(.subheadline).foregroundStyle(.secondary) }
            HStack(spacing: 8) {
                if let fit = job.fitLabel { Text(fit).foregroundStyle(.tint) }
                else if let percent = job.matchPercent { Text("\(percent)% match") }
                if let distance = job.distanceText { Text(distance) }
                if let posted = Self.date(job.postedAt ?? job.createdAt) {
                    Text(posted, format: .relative(presentation: .named))
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            if !summary.isEmpty {
                Text(summary).font(.callout).lineLimit(job.triageSummary.isEmpty ? 4 : nil)
            }
            if !job.triageFlags.isEmpty {
                Text(job.triageFlags.map { JobFeed.flagLabels[$0.kind] ?? $0.kind }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.orange)
            }
            HStack {
                if let url = URL(string: job.url), !job.url.isEmpty {
                    Button("Open") { openURL(url) }.buttonStyle(.borderless)
                }
                Spacer()
                Button("Dismiss") { decide(job, .dismiss) }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("job-dismiss-\(job.id)")
                Button("Queue") { decide(job, .queue) }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("job-queue-\(job.id)")
            }
            .padding(.top, 2)
        }
        .padding(.vertical, 4)
        .swipeActions(edge: .leading) {
            Button("Queue") { decide(job, .queue) }.tint(.accentColor)
        }
        .swipeActions(edge: .trailing) {
            Button("Dismiss", role: .destructive) { decide(job, .dismiss) }
        }
    }

    static func date(_ iso: String?) -> Date? {
        guard let iso else { return nil }
        return ISO8601DateFormatter().date(from: iso)
    }
}
