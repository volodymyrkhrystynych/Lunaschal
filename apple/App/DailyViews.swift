import SwiftUI
import LunaschalCore

/// The Capture tab's second page: daily health and voluntary spending logs.
/// Everything is saved on the device first and uploads with the next sync.
struct DailyView: View {
    @ObservedObject var model: CaptureModel
    @State private var weight = ""
    // Kept across launches, like the desktop card's draft.
    @AppStorage("dailyCaloriesDraft") private var calorieLine = ""
    @State private var calorieError: String?
    @AppStorage("dailySpendingAmountDraft") private var spendingAmount = ""
    @AppStorage("dailySpendingCategoryDraft") private var spendingCategory = ""
    @State private var spendingError: String?
    @State private var showCamera = false
    @FocusState private var typing: Bool

    private var summary: DailySummary {
        DailySummary(day: DayKey.of(Date()), server: model.dailyStatus, local: model.dailyLogs)
    }
    private var parsedWeight: Double? { Double(weight.replacingOccurrences(of: ",", with: ".")) }
    private var refused: [DailyLog] { model.dailyLogs.filter { $0.state == .failed } }

    var body: some View {
        let today = summary
        Form {
            Section("Weather") { WeatherCard(weather: model.weather) }
            Section("Selfie") {
                selfie(today)
                Button { typing = false; showCamera = true } label: {
                    Label(today.hasSelfie ? "Retake selfie" : "Take selfie", systemImage: "camera")
                }.disabled(!UIImagePickerController.isSourceTypeAvailable(.camera))
            }
            Section("Body weight") {
                if let value = today.weight {
                    HStack {
                        Text("Today")
                        Spacer()
                        Text(value.formatted(.number.precision(.fractionLength(0...1)))).monospacedDigit()
                    }
                    if today.weightWaiting { waiting }
                }
                HStack {
                    TextField(today.weight == nil ? "Today's weight" : "Correct today's weight", text: $weight)
                        .keyboardType(.decimalPad).focused($typing)
                        .accessibilityLabel("Body weight")
                    Button("Log weight") {
                        if let value = parsedWeight, model.logWeight(value) { weight = ""; typing = false }
                    }.disabled(parsedWeight == nil)
                }
            }
            Section {
                ForEach(today.entries) { entry in
                    HStack {
                        Text(entry.description)
                        Spacer()
                        if entry.waiting {
                            Image(systemName: "clock").foregroundStyle(.secondary).accessibilityLabel("Waiting to sync")
                        }
                        Text("\(entry.calories) kcal").monospacedDigit().foregroundStyle(.secondary)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button("Delete", role: .destructive) {
                            model.deleteDailyEntry(id: entry.id, kind: .calories, day: today.day)
                        }
                    }
                }
                HStack {
                    TextField("chicken breast and rice, ~600", text: $calorieLine)
                        .focused($typing)
                        .submitLabel(.done)
                        .onSubmit(addCalories)
                        .onChange(of: calorieLine) { _, _ in calorieError = nil }
                        .accessibilityLabel("Calories")
                    Button("Add", action: addCalories)
                        .disabled(calorieLine.trimmingCharacters(in: .whitespaces).isEmpty)
                        .accessibilityLabel("Add calories")
                }
                if let error = calorieError {
                    Text(error).font(.footnote).foregroundStyle(.red)
                } else if let preview = CalorieLine.parse(calorieLine) {
                    Text("\(preview.description) — \(preview.calories) kcal")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } header: {
                HStack {
                    Text("Calories")
                    Spacer()
                    Text("\(today.total) kcal today").monospacedDigit()
                }
            }
            Section {
                ForEach(today.spending) { entry in
                    HStack {
                        Text(entry.category)
                        Spacer()
                        if entry.waiting {
                            Image(systemName: "clock").foregroundStyle(.secondary).accessibilityLabel("Waiting to sync")
                        }
                        Text(money(entry.amountCents)).monospacedDigit().foregroundStyle(.secondary)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button("Delete", role: .destructive) {
                            model.deleteDailyEntry(id: entry.id, kind: .spending, day: today.day)
                        }
                    }
                }
                TextField("Category, e.g. Groceries or McDonald's", text: $spendingCategory)
                    .focused($typing).accessibilityLabel("Spending category")
                HStack {
                    TextField("Amount (CAD)", text: $spendingAmount)
                        .keyboardType(.decimalPad).focused($typing).accessibilityLabel("Spending amount")
                    Button("Add", action: addSpending)
                        .disabled(spendingCategory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || spendingAmount.isEmpty)
                        .accessibilityLabel("Add spending")
                }
                if let error = spendingError { Text(error).font(.footnote).foregroundStyle(.red) }
            } header: {
                HStack {
                    Text("Voluntary spending")
                    Spacer()
                    Text("\(money(today.totalCents)) today").monospacedDigit()
                }
            }
            .onChange(of: spendingAmount) { _, _ in spendingError = nil }
            .onChange(of: spendingCategory) { _, _ in spendingError = nil }
            if !refused.isEmpty {
                Section("Not accepted by the server") {
                    ForEach(refused) { log in
                        VStack(alignment: .leading) {
                            Text(label(log))
                            if let error = log.lastError { Text(error).font(.footnote).foregroundStyle(.red) }
                        }
                        .swipeActions { Button("Remove", role: .destructive) { model.discard(log) } }
                    }
                }
            }
            if model.dailyStatus?.day != today.day {
                Text(model.signedIn ? "Showing what this device logged today. The server's record appears after the next sync."
                     : "Showing what this device logged today. Sign in to sync.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .scrollDismissesKeyboard(.interactively)
        .refreshable { model.requestSync(manual: true) }
        // Today's record on the server, when the page is shown rather than every pass.
        .task { await model.refreshDailyStatus() }
        .fullScreenCover(isPresented: $showCamera) {
            CameraPicker(front: true) { _ = model.logSelfie($0) }.ignoresSafeArea()
        }
    }

    /// One line, split the way the desktop's Calories card splits it.
    private func addCalories() {
        guard let entry = CalorieLine.parse(calorieLine) else {
            calorieError = "End the line with a calorie count, e.g. \"rice and chicken 600\""
            return
        }
        if model.logCalories(entry.calories, description: entry.description) {
            calorieLine = ""; typing = false
        }
    }

    private func addSpending() {
        guard let cents = SpendingAmount.cents(spendingAmount) else {
            spendingError = DailyError.invalidAmount.localizedDescription
            return
        }
        if model.logSpending(cents, category: spendingCategory) {
            spendingAmount = ""; spendingCategory = ""; spendingError = nil; typing = false
        }
    }

    private func money(_ cents: Int) -> String {
        (Decimal(cents) / 100).formatted(.currency(code: "CAD"))
    }

    @ViewBuilder private func selfie(_ today: DailySummary) -> some View {
        if let local = today.localSelfie, let image = UIImage(contentsOfFile: model.daily.imageURL(local).path) {
            photo(image)
            if local.state == .pending { waiting }
        } else if let selfie = today.serverSelfie, model.selfieThumbnail?.id == selfie.id,
                  let data = model.selfieThumbnail?.data, let image = UIImage(data: data) {
            photo(image)
        } else if today.hasSelfie {
            Label("Taken today", systemImage: "checkmark.circle")
        } else {
            Text("No selfie yet today").foregroundStyle(.secondary)
        }
    }

    private func photo(_ image: UIImage) -> some View {
        Image(uiImage: image).resizable().scaledToFill()
            .frame(width: 120, height: 120).clipShape(RoundedRectangle(cornerRadius: 12))
            .accessibilityLabel("Today's selfie")
    }

    private var waiting: some View {
        Label("Saved on this device · Waiting to sync", systemImage: "clock")
            .font(.footnote).foregroundStyle(.secondary)
    }

    private func label(_ log: DailyLog) -> String {
        if log.isDeletion { return "Delete \(log.kind == .calories ? "calorie" : "spending") entry for \(log.day)" }
        switch log.kind {
        case .selfie: return "Selfie for \(log.day)"
        case .weight: return "Weight \(log.weight.map { String($0) } ?? "") for \(log.day)"
        case .calories: return "\(log.description ?? "") · \(log.calories ?? 0) kcal"
        case .spending: return "\(log.category ?? "") · \(money(log.amountCents ?? 0))"
        }
    }
}
