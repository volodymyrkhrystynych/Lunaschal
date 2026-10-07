#if DEBUG
import Foundation
import LunaschalCore

/// What the Jobs feed's UI test reads, since UI tests run with no server:
/// launched with `-jobsFeedFixture`, the cached feed is replaced with a few
/// made-up postings covering each kind of card (triaged and not, flagged,
/// remote, located, no salary), and any unsent decisions are cleared so a
/// rerun starts from the full feed. Debug builds only.
enum JobsFixture {
    static let argument = "-jobsFeedFixture"

    static func seedIfAsked(root: URL) throws {
        guard ProcessInfo.processInfo.arguments.contains(argument) else { return }
        let folder = root.appendingPathComponent("jobs", isDirectory: true)
        if FileManager.default.fileExists(atPath: folder.path) { try FileManager.default.removeItem(at: folder) }
        let iso = ISO8601DateFormatter()
        let daysAgo = { (days: Double) in iso.string(from: Date().addingTimeInterval(-days * 86_400)) }
        let reasons = { (matched: [String], missing: [String]) in FeedJob.MatchReasons(matched: matched, missing: missing) }
        try JobStore(root: folder).saveFeed([
            FeedJob(id: "01K7ZZZZZZZZZZZZZZZZZZZZJ1", title: "Senior iOS Engineer", company: "Northwind Labs",
                    location: "Toronto, ON", salaryMin: 140_000, salaryMax: 170_000, salaryCurrency: "CAD",
                    url: "https://example.com/jobs/ios", matchReasons: reasons(["swift", "swiftui", "sqlite"], ["kotlin"]),
                    postedAt: daysAgo(1), createdAt: daysAgo(1), triageFit: "strong",
                    triageSummary: "Own the offline sync layer of a field-service app. Small team, SwiftUI throughout.",
                    distanceKm: 2.4, distancePrecision: "exact", workLocation: "hybrid"),
            FeedJob(id: "01K7ZZZZZZZZZZZZZZZZZZZZJ2", title: "Backend Developer (Python)", company: "Fable & Finch",
                    location: "Remote - Canada", remote: true, salaryMin: 110_000, salaryCurrency: "CAD",
                    url: "https://example.com/jobs/python", matchReasons: reasons(["python", "flask"], ["django", "aws"]),
                    postedAt: daysAgo(3), createdAt: daysAgo(2), triageFit: "possible",
                    triageSummary: "Flask services behind a scheduling product. Fully remote within Canada.",
                    workLocation: "remote"),
            FeedJob(id: "01K7ZZZZZZZZZZZZZZZZZZZZJ3", title: "Full-Stack Engineer", company: "Harbourfront Health",
                    location: "Mississauga, ON",
                    description: "We are looking for a full-stack engineer to join our patient portal team. You will "
                        + "work across a React front end and Python services, and help us move to a modern CI setup.",
                    url: "https://example.com/jobs/fullstack",
                    matchReasons: reasons(["react", "typescript", "python"], ["java"]),
                    createdAt: daysAgo(0), distanceKm: 27, distancePrecision: "city"),
            FeedJob(id: "01K7ZZZZZZZZZZZZZZZZZZZZJ4", title: "Principal Platform Architect", company: "Meridian Bank",
                    location: "Toronto, ON", salaryMin: 210_000, salaryMax: 250_000, salaryCurrency: "CAD",
                    url: "https://example.com/jobs/principal", matchReasons: reasons(["python"], ["java", "kubernetes", "terraform"]),
                    postedAt: daysAgo(6), createdAt: daysAgo(5), triageFit: "stretch",
                    triageSummary: "Lead platform architecture for a retail bank. Fifteen years' experience expected.",
                    triageFlags: [FeedJob.Flag(kind: "seniority_mismatch", detail: "Asks for 15+ years"),
                                  FeedJob.Flag(kind: "security_clearance", detail: "Reliability clearance")],
                    distanceKm: 0.8, distancePrecision: "district", workLocation: "onsite"),
            FeedJob(id: "01K7ZZZZZZZZZZZZZZZZZZZZJ5", title: "Mobile Developer, 6-month contract", company: "Lakeshore Studio",
                    location: "Hamilton, ON", url: "https://example.com/jobs/contract",
                    matchReasons: reasons(["swift"], ["flutter", "dart", "firebase"]),
                    postedAt: daysAgo(9), createdAt: daysAgo(8), triageFit: "stretch",
                    triageSummary: "Six-month Flutter contract rebuilding a retail app.",
                    triageFlags: [FeedJob.Flag(kind: "contract_only", detail: "Six months, no extension mentioned"),
                                  FeedJob.Flag(kind: "stack_mismatch", detail: "Flutter rather than native")],
                    distanceKm: 58, distancePrecision: "city", workLocation: "onsite"),
        ])
    }
}
#endif
