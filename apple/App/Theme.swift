import LunaschalCore
import SwiftUI
import UIKit

/// The app's one global colour rule for text: what the user typed is blue,
/// speech-to-text of their recordings is teal, and AI-written text keeps the
/// normal text colour — the three things a regeneration treats differently.
/// Who produced what comes from the server (`backend/authorship.py`) as
/// `AuthoredText`; this file only paints it.
extension Color {
    /// Catppuccin blue on dark, as the web's `--color-text-user`; a deeper
    /// blue on light, where the pale one would not read against white.
    static let userText = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0x89 / 255, green: 0xB4 / 255, blue: 0xFA / 255, alpha: 1)
            : UIColor(red: 0x1E / 255, green: 0x66 / 255, blue: 0xF5 / 255, alpha: 1)
    })
    /// Catppuccin teal on dark, as the web's `--color-text-speech`.
    static let speechText = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0x94 / 255, green: 0xE2 / 255, blue: 0xD5 / 255, alpha: 1)
            : UIColor(red: 0x17 / 255, green: 0x92 / 255, blue: 0x99 / 255, alpha: 1)
    })
    static let aiText = Color.primary
}

extension TextAuthor {
    var color: Color {
        switch self {
        case .user: .userText
        case .speech: .speechText
        case .ai: .aiText
        }
    }
}

extension AuthoredText {
    /// The one colour for an editable field, which cannot be painted per
    /// character: its author's when it has only one, the normal text colour
    /// when it is mixed.
    var fieldColor: Color { soleAuthor?.color ?? .aiText }
}

extension CalendarEvent {
    /// Spans describe the text the server last sent; an edit made here and
    /// not yet synced no longer matches them and is the user's.
    private func current(_ authored: AuthoredText?, _ text: String?) -> AuthoredText? {
        guard let text, !text.isEmpty else { return nil }
        if let authored, authored.text == text { return authored }
        return AuthoredText(text, author: .user)
    }
    var titleColor: Color { current(authoredTitle, title)?.fieldColor ?? .primary }
    var authoredDescriptionText: AuthoredText? { current(authoredDescription, description) }
}

extension View {
    /// Colours a whole block of text by who wrote it.
    func authored(_ author: TextAuthor) -> some View { foregroundStyle(author.color) }
}

/// Text whose parts have different authors, drawn as one `Text` so wrapping,
/// line limits and selection behave exactly as a plain one does.
struct AuthoredTextView: View {
    let text: AuthoredText

    var body: some View { Text(Self.attributed(text)) }

    /// Paints already-styled text (Markdown) by the source it was rendered
    /// from; `aligner` walks that source, so calls must come in source order.
    static func paint(_ styled: AttributedString, _ aligner: AuthorAligner) -> AttributedString {
        var out = styled
        let authors = aligner.authors(for: String(out.characters))
        let scalars = out.unicodeScalars
        // One attribute write per run of one author, not per character: a
        // chapter is tens of thousands of them.
        var start = scalars.startIndex
        var index = start
        var current = authors.first
        for author in authors {
            guard index < scalars.endIndex else { break }
            if author != current, let painted = current {
                out[start..<index].foregroundColor = painted.color
                start = index
                current = author
            }
            index = scalars.index(after: index)
        }
        if let painted = current, start < index { out[start..<index].foregroundColor = painted.color }
        return out
    }

    static func attributed(_ text: AuthoredText) -> AttributedString {
        var out = AttributedString()
        for run in text.runs {
            var part = AttributedString(run.text)
            part.foregroundColor = run.author.color
            out += part
        }
        return out
    }
}
