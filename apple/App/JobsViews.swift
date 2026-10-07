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
    private let capture: CaptureModel

    init(capture: CaptureModel) {
        self.capture = capture
        jobs = capture.jobStore.cachedFeed()
    }

    func refresh() async {
        guard let api = capture.chatAPI() else {
            error = "Sign in under More → Settings to load new postings."
            return
        }
        loading = true
        defer { loading = false }
        do {
            jobs = try await api.jobFeed(sort: sort)
            error = nil
            try? capture.jobStore.saveFeed(jobs)
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled { return }
            self.error = "Showing the last feed loaded. \(error.localizedDescription)"
        }
    }

    /// Taken out of the copy on screen as well as queued: once the decision
    /// has reached the server the outbox no longer hides the card.
    func decide(_ job: FeedJob, _ decision: JobDecision) {
        capture.decideJob(job, decision)
        jobs.removeAll { $0.id == job.id }
        try? capture.jobStore.saveFeed(jobs)
    }
}

struct JobsFeedView: View {
    @ObservedObject var capture: CaptureModel
    @StateObject private var feed: JobsFeedModel

    init(capture: CaptureModel) {
        self.capture = capture
        _feed = StateObject(wrappedValue: JobsFeedModel(capture: capture))
    }

    private var groups: (promising: [FeedJob], rest: [FeedJob]) {
        JobFeed.split(JobFeed.hidingDecided(feed.jobs, capture.jobQueue))
    }

    var body: some View {
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
        .navigationTitle("Jobs")
        .refreshable { await feed.refresh() }
        .task(id: feed.sort) { await feed.refresh() }
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

    private static func date(_ iso: String?) -> Date? {
        guard let iso else { return nil }
        return ISO8601DateFormatter().date(from: iso)
    }
}
