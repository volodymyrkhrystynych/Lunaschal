#if DEBUG
import Foundation
import LunaschalCore
import UIKit

/// A newspaper notebook for UI tests and the simulator, which have no server
/// to download an issue from: launched with `-newspaperFixture`, Draw lists
/// "Toronto Star · <today>" over a generated issue whose pages come in
/// different shapes — broadsheet, tabloid, a wide spread — so each page
/// keeping its own shape in the column is visible. Debug builds only.
enum NewspaperFixture {
    static let argument = "-newspaperFixture"
    /// Height over width of each generated page.
    static let shapes: [CGFloat] = [1.8, 1.3, 0.7, 11.0 / 8.5]

    static func seedIfAsked(_ notebooks: NotebookStore) throws {
        guard ProcessInfo.processInfo.arguments.contains(argument) else { return }
        let date = DayKey.of(Date())
        if try notebooks.unsavedNewspaper(date: date) != nil { return }
        let pdf = FileManager.default.temporaryDirectory.appendingPathComponent("fixture-\(UUID().uuidString).pdf")
        try makeIssue(at: pdf)
        try notebooks.createNewspaper(date: date, pdf: pdf, pageCount: shapes.count)
    }

    /// Each page says which it is and outlines its own edge, so a page cut
    /// off or stretched in the column is plain to see.
    static func makeIssue(at url: URL) throws {
        let width: CGFloat = 612
        // A page whose bounds are US Letter (the last one) comes out at the
        // renderer's default size instead, so the default is Letter too.
        let renderer = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: width, height: width * 11 / 8.5))
        try renderer.writePDF(to: url) { context in
            for (index, ratio) in shapes.enumerated() {
                let page = CGRect(x: 0, y: 0, width: width, height: (width * ratio).rounded())
                context.beginPage(withBounds: page, pageInfo: [:])
                UIColor(white: 0.96, alpha: 1).setFill()
                context.fill(page)
                UIColor.black.setStroke()
                context.cgContext.setLineWidth(6)
                context.cgContext.stroke(page.insetBy(dx: 3, dy: 3))
                let title = "PAGE \(index + 1)" as NSString
                title.draw(at: CGPoint(x: 40, y: 40),
                           withAttributes: [.font: UIFont.boldSystemFont(ofSize: 64), .foregroundColor: UIColor.black])
                let body = String(repeating: "Column text for the fixture issue. ", count: 40) as NSString
                body.draw(in: page.insetBy(dx: 40, dy: 140),
                          withAttributes: [.font: UIFont.systemFont(ofSize: 18), .foregroundColor: UIColor.darkGray])
            }
        }
    }
}
#endif
