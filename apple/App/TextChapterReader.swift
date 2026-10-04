import SwiftUI
import LunaschalCore

struct TextChapterReader: View {
    let store: ReplicaStore
    let chapter: SyncChange
    @State private var position: Int?
    @State private var version = ""
    @State private var status = "Reading position stays on this device."

    private var paragraphs: [String] {
        (chapter.data?["contentText"]?.string ?? "").components(separatedBy: "\n\n")
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                ForEach(Array(paragraphs.enumerated()), id: \.offset) { index, text in
                    Text(text).font(.system(.body, design: .serif)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).id(index)
                }
            }
            .scrollTargetLayout()
            .frame(maxWidth: 760, alignment: .leading).padding()
            .frame(maxWidth: .infinity)
        }
        .scrollPosition(id: $position, anchor: .top)
        .safeAreaInset(edge: .bottom) {
            Text(status).font(.caption).foregroundStyle(.secondary).padding(6)
                .frame(maxWidth: .infinity).background(.bar)
        }
        .navigationTitle(chapter.title).navigationBarTitleDisplayMode(.inline)
        .task {
            do {
                version = "\(try store.epoch ?? "unknown"):\(chapter.revision)"
                let saved = try store.readingPosition(collection: "fic_chapters", id: chapter.id, version: version) ?? 0
                position = paragraphs.indices.contains(saved) ? saved : 0
            } catch { status = "Could not restore reading position: \(error.localizedDescription)" }
        }
        .onChange(of: position) { _, value in
            guard let value, !version.isEmpty, paragraphs.indices.contains(value) else { return }
            do {
                try store.saveReadingPosition(collection: "fic_chapters", id: chapter.id, version: version,
                                              offset: value, bookID: chapter.data?["ficId"]?.string)
                status = "Reading position saved on this device."
            } catch { status = "Could not save reading position: \(error.localizedDescription)" }
        }
    }
}
