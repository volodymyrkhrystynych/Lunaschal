import XCTest
@testable import LunaschalCore

private final class FakeServer: WritingTransport, WritingTranscriber {
    var documents: [WritingDocumentKey: WritingServerCopy] = [:]
    var bundles: [String: WritingBundle] = [:]
    var projects: [WritingProject] = []
    var offline = false
    var creates: [WritingDocumentKey] = []
    var sends: [(key: WritingDocumentKey, title: String?, content: String?, base: Int, at: Date)] = []
    var refusal: WritingSaveRefusal?
    var duringSend: (() async throws -> Void)?
    var transcript: String?
    var dictationRefusal: WritingDictationRefusal?
    var bundleRequests: [String] = []

    func createDocument(_ document: WritingDocument) async throws {
        if offline { throw URLError(.notConnectedToInternet) }
        creates.append(document.key)
        if documents[document.key] == nil { documents[document.key] = document.server }
    }

    func saveDocument(_ key: WritingDocumentKey, title: String?, content: String?, baseRevision: Int,
                      capturedAt: Date) async throws -> Int {
        if offline { throw URLError(.notConnectedToInternet) }
        try await duringSend?()
        sends.append((key, title, content, baseRevision, capturedAt))
        if let refusal { throw refusal }
        var copy = documents[key] ?? WritingServerCopy(title: title ?? "", content: "", revision: 0)
        if baseRevision != copy.revision { throw WritingSaveRefusal.changed(copy) }
        if let title { copy.title = title }
        if let content { copy.content = content }
        copy.revision += 1
        documents[key] = copy
        return copy.revision
    }

    func writingProjects() async throws -> [WritingProject] {
        if offline { throw URLError(.notConnectedToInternet) }
        return projects
    }

    func writingBundle(_ projectID: String) async throws -> WritingBundle {
        bundleRequests.append(projectID)
        return bundles[projectID]!
    }

    func transcribeWriting(audioURL: URL) async throws -> String {
        if offline { throw URLError(.notConnectedToInternet) }
        if let dictationRefusal { throw dictationRefusal }
        return transcript ?? ""
    }
}

final class WritingStoreTests: XCTestCase {
    private var root: URL!
    private var store: WritingStore!
    private var sync: WritingSync!
    private var server: FakeServer!
    private let project = "01BOOK"
    private var key: WritingDocumentKey { WritingDocumentKey(.chapter, "01CHAPTER") }

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try WritingStore(root: root)
        sync = WritingSync(store: store)
        server = FakeServer()
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    private func book(chapters: [WritingChapter] = [], notes: [WritingNote] = [], updatedAt: String = "t1") -> WritingBundle {
        WritingBundle(project: WritingProject(id: project, title: "Salt Roads", updatedAt: updatedAt),
                      chapters: chapters, notes: notes)
    }

    private func chapter(_ content: String = "Server text.", revision: Int = 3, title: String = "One") -> WritingChapter {
        WritingChapter(id: key.id, projectId: project, title: title, content: content, revision: revision)
    }

    private func seed(_ content: String = "Server text.", revision: Int = 3) throws {
        try store.apply(book(chapters: [chapter(content, revision: revision)]))
        server.documents[key] = WritingServerCopy(title: "One", content: content, revision: revision)
    }

    private var current: WritingDocument { store.document(key, project: project)! }

    // MARK: Keeping text

    func testTextIsOnDiskBeforeAnyNetworkAndSurvivesARelaunch() throws {
        try seed()
        try store.edit(key, project: project, content: "Typed on the bus.")
        let relaunched = try WritingStore(root: root)
        let document = relaunched.document(key, project: project)!
        XCTAssertEqual(document.content, "Typed on the bus.")
        XCTAssertEqual(document.server.content, "Server text.")
        XCTAssertTrue(document.isDirty)
    }

    func testTypingBackToTheServersTextIsNoDraft() throws {
        try seed()
        try store.edit(key, project: project, content: "Changed.")
        try store.edit(key, project: project, content: "Server text.")
        XCTAssertNil(current.draft)
    }

    func testTheCursorIsKeptBesideTheText() throws {
        try seed()
        try store.setCursor(key, project: project, 42)
        XCTAssertEqual(current.cursor, 42)
    }

    func testAnIDThatCouldEscapeTheFolderIsRefused() {
        XCTAssertThrowsError(try store.edit(WritingDocumentKey(.note, "../../etc"), project: project, content: "x"))
        XCTAssertNil(store.document(WritingDocumentKey(.note, "../x"), project: project))
    }

    func testARestoreReplacesADraft() throws {
        try seed()
        try store.edit(key, project: project, content: "Typed.")
        try store.restored(key, project: project, server: WritingServerCopy(title: "One", content: "Earlier.", revision: 5))
        XCTAssertEqual(current.content, "Earlier.")
        XCTAssertNil(current.draft)
        XCTAssertEqual(current.server.revision, 5)
    }

    func testChaptersAreInOrderAndANewOneSortsLast() throws {
        try store.apply(book(chapters: [
            WritingChapter(id: "02B", projectId: project, title: "Second", position: 1),
            WritingChapter(id: "01A", projectId: project, title: "First", position: 0),
        ]))
        try store.create(.chapter, project: project, title: "Third")
        XCTAssertEqual(store.documents(project: project).map(\.title), ["First", "Second", "Third"])
    }

    // MARK: Pushing

    func testASavedDraftIsClearedAndTheRevisionMoves() async throws {
        try seed()
        try store.edit(key, project: project, content: "New.")
        let refused = try await sync.push(using: server)
        XCTAssertEqual(refused, [])
        XCTAssertNil(current.draft)
        XCTAssertEqual(current.server.content, "New.")
        XCTAssertEqual(current.server.revision, 4)
        XCTAssertEqual(server.sends.first?.base, 3)
    }

    func testOfflineKeepsTheDraftAndALaterPassSendsIt() async throws {
        try seed()
        try store.edit(key, project: project, content: "New.")
        server.offline = true
        do { _ = try await sync.push(using: server); XCTFail("should have thrown") } catch {}
        XCTAssertEqual(current.content, "New.")
        XCTAssertNotNil(current.draft)
        server.offline = false
        _ = try await sync.push(using: server)
        XCTAssertNil(current.draft)
    }

    func testTheSendCarriesWhenItWasWritten() async throws {
        try seed()
        let written = Date(timeIntervalSince1970: 1_000_000)
        try store.edit(key, project: project, content: "New.", at: written)
        _ = try await sync.push(using: server)
        XCTAssertEqual(server.sends.first?.at, written)
    }

    func testTypingDuringASendKeepsTheNewerText() async throws {
        try seed()
        try store.edit(key, project: project, content: "Sent.")
        server.duringSend = { [unowned self] in
            server.duringSend = nil
            try store.edit(key, project: project, content: "Sent, and more.")
        }
        _ = try await sync.push(using: server)
        XCTAssertEqual(current.content, "Sent, and more.")
        XCTAssertEqual(current.server.content, "Sent.")
        XCTAssertEqual(current.server.revision, 4)
        XCTAssertNotNil(current.draft)
        _ = try await sync.push(using: server)
        XCTAssertEqual(server.documents[key]?.content, "Sent, and more.")
        XCTAssertNil(current.draft)
    }

    func testAPushDuringAPushGoesRoundAgainRatherThanRacing() async throws {
        try seed()
        try store.edit(key, project: project, content: "First.")
        server.duringSend = { [unowned self] in
            server.duringSend = nil
            try store.edit(key, project: project, content: "Second.")
            let inner = try await sync.push(using: server)
            XCTAssertEqual(inner, [])
            XCTAssertEqual(server.sends.count, 0, "the inner push must not send alongside the outer one")
        }
        let refused = try await sync.push(using: server)
        XCTAssertEqual(refused, [])
        XCTAssertEqual(server.sends.map(\.base), [3, 4], "the second send builds on the first")
        XCTAssertEqual(server.documents[key]?.content, "Second.")
        XCTAssertNil(current.hold)
        XCTAssertNil(current.draft)
    }

    func testAMadeOfflineDocumentIsCreatedThenFilled() async throws {
        let made = try store.create(.note, project: project, title: "Lyra", docType: "character", content: "Keeper.")
        XCTAssertFalse(made.onServer)
        _ = try await sync.push(using: server)
        XCTAssertEqual(server.creates, [made.key])
        XCTAssertEqual(server.documents[made.key]?.content, "Keeper.")
        let after = store.document(made.key, project: project)!
        XCTAssertTrue(after.onServer)
        XCTAssertFalse(after.isDirty)
    }

    func testAnEmptyMadeOfflineDocumentStillReachesTheServer() async throws {
        let made = try store.create(.chapter, project: project, title: "Blank")
        _ = try await sync.push(using: server)
        XCTAssertEqual(server.creates, [made.key])
        XCTAssertTrue(store.document(made.key, project: project)!.onServer)
    }

    // MARK: Refusals

    func testAConflictHoldsTheDraftAndStopsSendingIt() async throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        server.documents[key] = WritingServerCopy(title: "One", content: "Theirs.", revision: 9)
        _ = try await sync.push(using: server)
        XCTAssertEqual(current.content, "Mine.")
        XCTAssertEqual(current.hold, .changed(WritingServerCopy(title: "One", content: "Theirs.", revision: 9)))
        let sends = server.sends.count
        _ = try await sync.push(using: server)
        XCTAssertEqual(server.sends.count, sends)
    }

    func testADeletedDocumentIsHeldNotDropped() async throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        server.refusal = .missing
        _ = try await sync.push(using: server)
        XCTAssertEqual(current.hold, .deleted)
        XCTAssertEqual(current.content, "Mine.")
    }

    func testARefusalIsReportedAndTheRestStillGo() async throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        server.refusal = .refused("too long")
        let refused = try await sync.push(using: server)
        XCTAssertEqual(refused.count, 1)
        XCTAssertTrue(refused[0].contains("too long"))
        XCTAssertEqual(current.content, "Mine.")
    }

    func testKeepMineGoesOutOverTheLatest() async throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        server.documents[key] = WritingServerCopy(title: "One", content: "Theirs.", revision: 9)
        _ = try await sync.push(using: server)
        try store.resolve(key, project: project, .keepMine)
        _ = try await sync.push(using: server)
        XCTAssertEqual(server.documents[key]?.content, "Mine.")
        XCTAssertEqual(server.sends.last?.base, 9)
        XCTAssertNil(current.hold)
    }

    func testUseLatestDropsMine() throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        try store.refresh(key, project: project, server: WritingServerCopy(title: "One", content: "Theirs.", revision: 9))
        try store.resolve(key, project: project, .useLatest)
        XCTAssertEqual(current.content, "Theirs.")
        XCTAssertNil(current.draft)
    }

    func testSaveCopyKeepsBoth() throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        try store.refresh(key, project: project, server: WritingServerCopy(title: "One", content: "Theirs.", revision: 9))
        let copy = try store.resolve(key, project: project, .saveCopy)
        XCTAssertEqual(current.content, "Theirs.")
        XCTAssertEqual(copy?.title, "One (copy)")
        XCTAssertEqual(copy?.content, "Mine.")
        XCTAssertEqual(copy?.onServer, false)
    }

    func testSaveAsNewRescuesADeletedDocument() throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        try store.apply(book())
        XCTAssertEqual(current.hold, .deleted)
        let copy = try store.resolve(key, project: project, .saveCopy)
        XCTAssertEqual(copy?.content, "Mine.")
        XCTAssertEqual(copy?.title, "One")
        XCTAssertNil(store.document(key, project: project))
    }

    // MARK: Pulling

    func testACleanDocumentTakesTheNewCopy() throws {
        try seed()
        try store.apply(book(chapters: [chapter("From the desktop.", revision: 4)]))
        XCTAssertEqual(current.content, "From the desktop.")
    }

    func testAPullNeverOverwritesADraft() throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        try store.apply(book(chapters: [chapter("From the desktop.", revision: 4)]))
        XCTAssertEqual(current.content, "Mine.")
        XCTAssertEqual(current.hold, .changed(WritingServerCopy(title: "One", content: "From the desktop.", revision: 4)))
    }

    func testAPullOfTheSameRevisionLeavesADraftAlone() throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        try store.apply(book(chapters: [chapter()]))
        XCTAssertNil(current.hold)
    }

    func testServerAlreadyHavingMyTextClearsTheDraft() throws {
        try seed()
        try store.edit(key, project: project, content: "Mine.")
        try store.apply(book(chapters: [chapter("Mine.", revision: 4)]))
        XCTAssertNil(current.draft)
        XCTAssertNil(current.hold)
    }

    func testADocumentGoneFromTheServerGoesFromThePhoneUnlessEdited() throws {
        try seed()
        try store.apply(book())
        XCTAssertNil(store.document(key, project: project))
    }

    func testAPullKeepsADocumentMadeHere() throws {
        let made = try store.create(.note, project: project, title: "New")
        try store.apply(book())
        XCTAssertNotNil(store.document(made.key, project: project))
    }

    func testOnlyAChangedBookIsFetched() async throws {
        server.projects = [WritingProject(id: project, title: "Salt Roads", updatedAt: "t1")]
        server.bundles[project] = book(chapters: [chapter()], updatedAt: "t1")
        try await sync.pull(using: server)
        try await sync.pull(using: server)
        XCTAssertEqual(server.bundleRequests, [project])
        XCTAssertEqual(current.content, "Server text.")
        server.projects = [WritingProject(id: project, title: "Salt Roads", updatedAt: "t2")]
        server.bundles[project] = book(chapters: [chapter("Newer.", revision: 4)], updatedAt: "t2")
        try await sync.pull(using: server)
        XCTAssertEqual(server.bundleRequests, [project, project])
        XCTAssertEqual(current.content, "Newer.")
    }

    func testABookDeletedElsewhereKeepsAnUnsentDraft() async throws {
        server.projects = [WritingProject(id: project, title: "Salt Roads", updatedAt: "t1")]
        server.bundles[project] = book(chapters: [chapter()], updatedAt: "t1")
        try await sync.pull(using: server)
        try store.edit(key, project: project, content: "Mine.")
        server.projects = []
        try await sync.pull(using: server)
        XCTAssertEqual(current.content, "Mine.")
    }

    func testBooksStayReadableOffline() async throws {
        server.projects = [WritingProject(id: project, title: "Salt Roads", updatedAt: "t1")]
        server.bundles[project] = book(chapters: [chapter()], updatedAt: "t1")
        try await sync.pull(using: server)
        XCTAssertEqual(try WritingStore(root: root).projects().map(\.title), ["Salt Roads"])
    }

    // MARK: What a save's reply means

    func testASuccessfulReplyGivesTheRevision() throws {
        XCTAssertEqual(try JournalAPI.writingSaveResult(status: 200, data: Data(#"{"success":true,"revision":7}"#.utf8)), 7)
    }

    func testA409CarriesTheCurrentCopy() {
        let body = #"{"conflict":true,"current":{"title":"T","content":"C","revision":5,"projectId":"p"}}"#
        XCTAssertThrowsError(try JournalAPI.writingSaveResult(status: 409, data: Data(body.utf8))) { error in
            XCTAssertEqual(error as? WritingSaveRefusal, .changed(WritingServerCopy(title: "T", content: "C", revision: 5)))
        }
    }

    func testA404IsAMissingDocument() {
        XCTAssertThrowsError(try JournalAPI.writingSaveResult(status: 404, data: Data())) { error in
            XCTAssertEqual(error as? WritingSaveRefusal, .missing)
        }
    }

    func testABadRequestIsARefusalWithItsReason() {
        XCTAssertThrowsError(try JournalAPI.writingSaveResult(status: 400, data: Data(#"{"error":"nope"}"#.utf8))) { error in
            XCTAssertEqual(error as? WritingSaveRefusal, .refused("nope"))
        }
    }

    func testAuthAndServerFailuresStopThePassRatherThanRefuse() {
        for status in [401, 403, 429, 500, 503] {
            XCTAssertThrowsError(try JournalAPI.writingSaveResult(status: status, data: Data())) { error in
                XCTAssertNil(error as? WritingSaveRefusal, "\(status)")
            }
        }
    }

    // MARK: Words

    func testWordsMatchWhatTheServerCounts() {
        XCTAssertEqual(WritingStats.words("  one\ttwo\n\nтри  "), 3)
        XCTAssertEqual(WritingStats.words(""), 0)
        XCTAssertEqual(WritingStats.words("   \n"), 0)
    }

    func testUnsyncedWordsCountChaptersOnlyAndCanBeNegative() throws {
        try seed("one two three")
        try store.edit(key, project: project, content: "one")
        let note = try store.create(.note, project: project, title: "n", content: "a b c d")
        let documents = store.documents(project: project)
        XCTAssertEqual(WritingStats.unsyncedWords(documents), -2)
        XCTAssertNotNil(store.document(note.key, project: project))
    }

    func testTodayNeverReadsBelowZero() {
        XCTAssertEqual(WritingStats.wordsToday(serverWords: -40, unsynced: 10), 0)
        XCTAssertEqual(WritingStats.wordsToday(serverWords: 40, unsynced: 10), 50)
    }

    func testGoalProgress() {
        XCTAssertNil(WritingStats.goalProgress(words: 10, goal: nil))
        XCTAssertEqual(WritingStats.goalProgress(words: 50, goal: 100), 0.5)
        XCTAssertEqual(WritingStats.goalProgress(words: 500, goal: 100), 1)
        XCTAssertEqual(WritingStats.goalProgress(words: -5, goal: 100), 0)
    }

    func testStatsDecode() throws {
        let body = #"{"today":"2026-11-03","todayWords":12,"dailyWordGoal":null,"days":[{"day":"2026-11-03","words":12}]}"#
        let stats = try JSONDecoder().decode(WritingWordStats.self, from: Data(body.utf8))
        XCTAssertEqual(stats.todayWords, 12)
        XCTAssertNil(stats.dailyWordGoal)
    }

    func testABundleDecodesFromTheServersShape() throws {
        let body = #"""
        {"project":{"id":"p","title":"T","description":null,"dailyWordGoal":300,"createdAt":"x","updatedAt":"y"},
         "chapters":[{"id":"c","projectId":"p","title":"One","content":"Text","position":2,"revision":4,"createdAt":"x","updatedAt":"y"}],
         "notes":[{"id":"n","projectId":"p","title":"Lyra","content":"K","docType":"character","pinned":1,"revision":2,"createdAt":"x","updatedAt":"y"}]}
        """#
        let bundle = try JSONDecoder().decode(WritingBundle.self, from: Data(body.utf8))
        XCTAssertEqual(bundle.project.dailyWordGoal, 300)
        XCTAssertEqual(bundle.chapters[0].revision, 4)
        XCTAssertEqual(bundle.notes[0].pinned, true)
    }
}

final class WritingDictationTests: XCTestCase {
    private var root: URL!
    private var clips: WritingDictationStore!
    private var dictation: WritingDictationSync!
    private let key = WritingDocumentKey(.chapter, "01CHAPTER")

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        clips = try WritingDictationStore(root: root.appendingPathComponent("clips"))
        dictation = WritingDictationSync(clips: clips)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: root) }

    private func recording() throws -> URL {
        let url = root.appendingPathComponent(UUID().uuidString).appendingPathExtension("m4a")
        try Data("audio".utf8).write(to: url)
        return url
    }

    private func anchor(_ offset: Int, location: Int, removed: Int, inserted: Int) -> Int {
        var anchor = DictationAnchor(offset: offset)
        anchor.shift(location: location, removed: removed, inserted: inserted)
        return anchor.offset
    }

    func testTheAnchorMovesWithTextBeforeIt() {
        XCTAssertEqual(anchor(10, location: 2, removed: 0, inserted: 5), 15)
        XCTAssertEqual(anchor(10, location: 2, removed: 3, inserted: 0), 7)
        XCTAssertEqual(anchor(10, location: 10, removed: 0, inserted: 5), 15)
    }

    func testTheAnchorStaysForTextAfterIt() {
        XCTAssertEqual(anchor(10, location: 11, removed: 0, inserted: 5), 10)
    }

    func testAnEditAcrossTheAnchorLandsAfterTheReplacement() {
        XCTAssertEqual(anchor(10, location: 8, removed: 5, inserted: 2), 10)
    }

    func testSpokenWordsGetSpacesOnlyWhereNeeded() {
        XCTAssertEqual(DictationAnchor(offset: 3).inserting("big", into: "the storm").text, "the big storm")
        XCTAssertEqual(DictationAnchor(offset: 4).inserting("big", into: "the storm").text, "the big storm")
        XCTAssertEqual(DictationAnchor(offset: 0).inserting("big", into: "").text, "big")
        XCTAssertEqual(DictationAnchor(offset: 5).inserting("Then.", into: "It is").text, "It is Then.")
    }

    func testPunctuationDoesNotGetASpaceBeforeIt() {
        XCTAssertEqual(DictationAnchor(offset: 2).inserting(", and more", into: "Hi").text, "Hi, and more")
    }

    func testInsertionCountsUTF16LikeTheTextView() {
        let result = DictationAnchor(offset: 2).inserting("x", into: "🌊 storm")
        XCTAssertEqual(result.text, "🌊 x storm")
        XCTAssertEqual(result.end, 4)
        XCTAssertEqual(result.location, 2)
        XCTAssertEqual(result.inserted, " x")
    }

    func testAnEditIsWorkedOutFromTheTwoTexts() {
        XCTAssertEqual(WritingTextEdit.between("abc", "abXc"), WritingTextEdit(location: 2, removed: 0, inserted: 1))
        XCTAssertEqual(WritingTextEdit.between("abXc", "abc"), WritingTextEdit(location: 2, removed: 1, inserted: 0))
        XCTAssertEqual(WritingTextEdit.between("hello", "help"), WritingTextEdit(location: 3, removed: 2, inserted: 1))
        XCTAssertEqual(WritingTextEdit.between("same", "same"), WritingTextEdit(location: 4, removed: 0, inserted: 0))
        XCTAssertEqual(WritingTextEdit.between("", "new"), WritingTextEdit(location: 0, removed: 0, inserted: 3))
    }

    func testARepeatedLetterDoesNotOverlapTheEdit() {
        XCTAssertEqual(WritingTextEdit.between("aa", "aaa"), WritingTextEdit(location: 2, removed: 0, inserted: 1))
    }

    func testAnOutOfRangeAnchorIsClampedNotACrash() {
        XCTAssertEqual(DictationAnchor(offset: 99).inserting("end", into: "the").text, "the end")
    }

    func testAClipSurvivesARelaunch() throws {
        let clip = try clips.add(try recording(), key: key, projectId: "p", anchor: DictationAnchor(offset: 4))
        let relaunched = try WritingDictationStore(root: clips.root)
        XCTAssertEqual(relaunched.clips(), [clip])
        XCTAssertTrue(FileManager.default.fileExists(atPath: relaunched.audio(clip).path))
    }

    func testOfflineKeepsTheClip() async throws {
        let clip = try clips.add(try recording(), key: key, projectId: "p", anchor: DictationAnchor(offset: 0))
        let server = FakeServer()
        server.offline = true
        do {
            _ = try await dictation.run(using: server) { _, _ in XCTFail("nothing to deliver") }
            XCTFail("should have thrown")
        } catch {}
        XCTAssertEqual(clips.clips(), [clip])
    }

    func testTheAudioGoesOnlyAfterTheTextIsDelivered() async throws {
        let clip = try clips.add(try recording(), key: key, projectId: "p", anchor: DictationAnchor(offset: 0))
        let server = FakeServer()
        server.transcript = "Spoken words."
        struct DiskFull: Error {}
        do {
            _ = try await dictation.run(using: server) { _, _ in throw DiskFull() }
            XCTFail("should have thrown")
        } catch is DiskFull {}
        XCTAssertEqual(clips.clips(), [clip])
        var delivered: [String] = []
        _ = try await dictation.run(using: server) { _, text in delivered.append(text) }
        XCTAssertEqual(delivered, ["Spoken words."])
        XCTAssertEqual(clips.clips(), [])
    }

    func testAnEditBeforeTheClipLandsMovesItsPlace() async throws {
        try clips.add(try recording(), key: key, projectId: "p", anchor: DictationAnchor(offset: 5))
        try clips.shift(key, location: 0, removed: 0, inserted: 3)
        let server = FakeServer()
        server.transcript = "x"
        var seen = 0
        _ = try await dictation.run(using: server) { clip, _ in seen = clip.anchor.offset }
        XCTAssertEqual(seen, 8)
    }

    func testAnUnusableClipIsDroppedAndReported() async throws {
        try clips.add(try recording(), key: key, projectId: "p", anchor: DictationAnchor(offset: 0))
        let server = FakeServer()
        server.dictationRefusal = WritingDictationRefusal(message: "Too short")
        let refused = try await dictation.run(using: server) { _, _ in }
        XCTAssertEqual(refused.count, 1)
        XCTAssertEqual(clips.clips(), [])
    }

    func testSilenceDeliversNothing() async throws {
        try clips.add(try recording(), key: key, projectId: "p", anchor: DictationAnchor(offset: 0))
        let server = FakeServer()
        server.transcript = "   "
        _ = try await dictation.run(using: server) { _, _ in XCTFail("nothing was said") }
        XCTAssertEqual(clips.clips(), [])
    }
}
