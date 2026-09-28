import SwiftUI
import LunaschalCore

struct LibraryView: View {
    @ObservedObject var model: CaptureModel
    @State private var query = ""
    @State private var results: [SyncChange] = []

    var body: some View {
        List {
            Section {
                Button("Download reading text over Wi-Fi") { Task { await model.downloadLibrary() } }
                    .disabled(model.downloadingLibrary || !model.signedIn)
                if model.downloadingLibrary { ProgressView() }
                if let message = model.libraryMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
            }
            Section("Books and stories") {
                ForEach(query.isEmpty ? model.libraryRecords : results) { book in
                    NavigationLink { BookView(model: model, book: book) } label: {
                        VStack(alignment: .leading) {
                            Text(book.title)
                            Text(book.data?["author"]?.string ?? "").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Library")
        .searchable(text: $query, prompt: "Search downloaded titles")
        .onChange(of: query) { _, value in
            do { results = try model.replica.records(collection: "fics", query: value) }
            catch { model.message = error.localizedDescription }
        }
    }
}

private struct BookView: View {
    @ObservedObject var model: CaptureModel
    let book: SyncChange
    @State private var chapters: [SyncChange] = []

    var body: some View {
        List {
            if let description = book.data?["description"]?.string, !description.isEmpty {
                Text(description)
            }
            if chapters.isEmpty {
                Text("No chapter text downloaded. Use the Library download button on Wi-Fi.")
            }
            ForEach(chapters) { chapter in
                NavigationLink {
                    ScrollView {
                        Text(chapter.data?["contentText"]?.string ?? "")
                            .font(.system(.body, design: .serif)).textSelection(.enabled)
                            .frame(maxWidth: 760, alignment: .leading).padding()
                    }.navigationTitle(chapter.title).navigationBarTitleDisplayMode(.inline)
                } label: { Text(chapter.title) }
            }
        }
        .navigationTitle(book.title)
        .task {
            do {
                chapters = try model.replica.relatedRecords(collection: "fic_chapters", field: "ficId", value: book.id)
                    .sorted { ($0.data?["position"]?.number ?? 0) < ($1.data?["position"]?.number ?? 0) }
            } catch { model.message = error.localizedDescription }
        }
    }
}

struct JournalRecordView: View {
    @ObservedObject var model: CaptureModel
    let record: SyncChange
    @State private var content = ""
    @State private var title = ""
    @State private var editing = false
    @State private var confirmingDelete = false
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            if editing {
                TextField("Title", text: $title)
                TextEditor(text: $content).frame(minHeight: 240)
                Button("Save edit on this device") {
                    if model.edit(record, content: content, title: title) { dismiss() }
                }
                Button("Cancel", role: .cancel) { editing = false }
            } else {
                Text(record.data?["content"]?.string ?? "").textSelection(.enabled)
                if let original = record.data?["rawContent"]?.string, !original.isEmpty {
                    DisclosureGroup("Original transcript") { Text(original).textSelection(.enabled) }
                }
                Button("Edit") {
                    content = record.data?["content"]?.string ?? ""
                    title = record.data?["title"]?.string ?? ""
                    editing = true
                }
                Button("Delete entry", role: .destructive) { confirmingDelete = true }
            }
        }
        .navigationTitle(record.title).navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("Delete this journal entry when the server reconnects?", isPresented: $confirmingDelete) {
            Button("Delete entry", role: .destructive) { model.delete(record); dismiss() }
        }
    }
}

struct PendingEditView: View {
    @ObservedObject var model: CaptureModel
    let edit: PendingEdit
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section("Your saved change") {
                Text(edit.operation.action == "delete" ? "Delete entry" : edit.operation.data["content"]?.string ?? "Title or tags changed")
                    .textSelection(.enabled)
            }
            if let error = edit.error { Text(error).foregroundStyle(.orange) }
            if let current = edit.conflict {
                Section("Server version") {
                    Text(current.deleted ? "Deleted on server" : current.data?["content"]?.string ?? "").textSelection(.enabled)
                }
            }
            if edit.state != "pending" {
                Button("Apply my change to the latest version") { model.resolve(edit, keepLocal: true); dismiss() }
                    .disabled(edit.conflict?.deleted == true)
                if let text = edit.operation.data["content"]?.string {
                    Button("Save my text as a separate entry") {
                        if model.saveText(text) { model.resolve(edit, keepLocal: false); dismiss() }
                    }
                }
                Button("Discard my pending change", role: .destructive) { model.resolve(edit, keepLocal: false); dismiss() }
            } else { Text("This edit is saved locally and will sync when connected.") }
        }.navigationTitle("Saved edit")
    }
}
