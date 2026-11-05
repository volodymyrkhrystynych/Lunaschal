import SwiftUI
import PencilKit

/// A favorite is a complete tool, so two pens can keep different colors and widths.
struct DrawingFavorite: Codable, Identifiable, Equatable {
    var id = UUID()
    var kind: String
    var red: Double = 0
    var green: Double = 0
    var blue: Double = 0
    var width: Double = 3

    static let inks: [PKInkingTool.InkType] = [.pen, .pencil, .marker, .monoline, .fountainPen, .watercolor, .crayon, .reed]
    // PencilKit raw values are private-looking identifiers (com.apple.ink.*),
    // not the Swift case names. Keep our persisted names and labels stable.
    static let inkKinds = ["pen", "pencil", "marker", "monoline", "fountainPen", "watercolor", "crayon", "reed"]
    static let kinds = inkKinds + ["eraser", "pixelEraser", "lasso", "ruler", "insert"]
    static let defaults: [Self] = [Self(kind: "pen"), Self(kind: "pen", blue: 0.9),
        Self(kind: "marker", red: 1, green: 0.8, width: 20), Self(kind: "eraser")]
    var ink: PKInkingTool.InkType? { Self.inkKinds.firstIndex(of: kind).map { Self.inks[$0] } }
    var isEraser: Bool { kind == "eraser" || kind == "pixelEraser" }
    var widthRange: ClosedRange<Double>? {
        let range = ink?.validWidthRange ?? (kind == "pixelEraser" ? PKEraserTool.EraserType.fixedWidthBitmap.validWidthRange : nil)
        return range.map { Double($0.lowerBound)...Double($0.upperBound) }
    }
    var effectiveWidth: Double {
        guard let range = widthRange else { return width }
        return min(max(width, range.lowerBound), range.upperBound)
    }
    var widthPresets: [Double] {
        guard let range = widthRange else { return [] }
        // Geometric spacing leaves useful choices at the thin end of wide ink ranges.
        return (0..<5).map { index in
            range.lowerBound * pow(range.upperBound / range.lowerBound, Double(index) / 4)
        }
    }
    var color: UIColor { UIColor(red: red, green: green, blue: blue, alpha: 1) }
    var title: String {
        switch kind {
        case "marker": return "Highlighter"
        case "fountainPen": return "Fountain pen"
        case "pixelEraser": return "Pixel eraser"
        case "eraser": return "Object eraser"
        case "lasso": return "Selection"
        case "insert": return "Insert shape or text"
        default: return kind.capitalized
        }
    }
    var symbol: String {
        switch kind {
        case "eraser", "pixelEraser": return "eraser"
        case "lasso": return "lasso"
        case "ruler": return "ruler"
        case "insert": return "plus.rectangle"
        case "marker": return "highlighter"
        case "pencil", "crayon": return "pencil"
        default: return "pencil.tip"
        }
    }
    var tool: (any PKTool)? {
        if let ink { return PKInkingTool(ink, color: color, width: effectiveWidth) }
        switch kind {
        case "eraser": return PKEraserTool(.vector)
        // The variable bitmap eraser has no configurable width; the fixed
        // variant is the native pixel eraser that honors a chosen size.
        case "pixelEraser": return PKEraserTool(.fixedWidthBitmap, width: effectiveWidth)
        case "lasso": return PKLassoTool()
        default: return nil
        }
    }
}

enum DrawingDock: String, Codable, CaseIterable {
    case top, bottom, left, right
    var vertical: Bool { self == .left || self == .right }

    static func nearest(to point: CGPoint, in bounds: CGRect) -> Self {
        let distances: [(Self, CGFloat)] = [(.top, abs(point.y - bounds.minY)), (.bottom, abs(point.y - bounds.maxY)),
            (.left, abs(point.x - bounds.minX)), (.right, abs(point.x - bounds.maxX))]
        return distances.min { $0.1 < $1.1 }!.0
    }

    func frame(in bounds: CGRect, length: CGFloat, offset: Double) -> CGRect {
        let size = CGSize(width: vertical ? 44 : min(length, bounds.width), height: vertical ? min(length, bounds.height) : 44)
        let fraction = min(max(offset, 0), 1)
        return CGRect(x: vertical ? (self == .left ? bounds.minX : bounds.maxX - size.width) : bounds.minX + (bounds.width - size.width) * fraction,
                      y: vertical ? bounds.minY + (bounds.height - size.height) * fraction : (self == .top ? bounds.minY : bounds.maxY - size.height),
                      width: size.width, height: size.height)
    }
}

struct DrawingToolbarPreferences: Codable, Equatable {
    var favorites = DrawingFavorite.defaults
    var dock: DrawingDock = .bottom
    var offset: Double = 0.5
    static let key = "drawing-favorites-v1"

    static func load(from defaults: UserDefaults) -> Self {
        guard let data = defaults.data(forKey: key), let value = try? JSONDecoder().decode(Self.self, from: data),
              value.offset.isFinite, value.favorites.allSatisfy({ DrawingFavorite.kinds.contains($0.kind) && $0.width.isFinite &&
                  [$0.red, $0.green, $0.blue].allSatisfy { $0.isFinite && (0...1).contains($0) } }),
              Set(value.favorites.map(\.id)).count == value.favorites.count else { return Self() }
        return value
    }
    func save(to defaults: UserDefaults) { if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.key) } }
}

/// Only this 44-point strip intercepts touches; the surrounding page remains native.
@MainActor
final class DrawingToolbar: UIView, UIPencilInteractionDelegate {
    private(set) var preferences: DrawingToolbarPreferences
    private let defaults: UserDefaults
    private let stack = UIStackView()
    private let scroll = UIScrollView()
    private let favoritesStack = UIStackView()
    private var favoriteButtons: [UUID: UIButton] = [:]
    private let background = UIVisualEffectView(effect: UIBlurEffect(style: .systemThinMaterialLight))
    private weak var presenter: UIViewController?
    var onSelect: ((DrawingFavorite) -> Void)?
    var undoManagerProvider: (() -> UndoManager?)?
    private(set) var activeTool: DrawingFavorite?
    private var previousTool: DrawingFavorite?
    private var lastNonEraser: DrawingFavorite?
    private weak var attributesController: UIViewController?
    private var displayedTools: [DrawingFavorite] {
        guard let activeTool, !preferences.favorites.contains(where: { $0.id == activeTool.id }) else { return preferences.favorites }
        return preferences.favorites + [activeTool]
    }
    private var observers: [NSObjectProtocol] = []
    private var dragOrigin = CGPoint.zero
    private var undoButton: UIButton!
    private var redoButton: UIButton!

    init(presenter: UIViewController, defaults: UserDefaults = .standard) {
        self.presenter = presenter
        self.defaults = defaults
        preferences = .load(from: defaults)
        super.init(frame: .zero)
        accessibilityIdentifier = "drawing-toolbar"
        background.layer.cornerRadius = 14
        background.clipsToBounds = true
        addSubview(background)
        addSubview(stack)
        stack.alignment = .fill
        let handle = button("Move toolbar", symbol: "line.3.horizontal", action: {})
        handle.accessibilityIdentifier = "drawing-toolbar-move"
        handle.accessibilityHint = "Drag to any screen edge, or use All tools to choose a position."
        handle.addGestureRecognizer(UIPanGestureRecognizer(target: self, action: #selector(drag(_:))))
        stack.addArrangedSubview(handle)
        undoButton = button("Undo", symbol: "arrow.uturn.backward") { [weak self] in
            self?.undoManagerProvider?()?.undo(); self?.refreshUndo()
        }
        redoButton = button("Redo", symbol: "arrow.uturn.forward") { [weak self] in
            self?.undoManagerProvider?()?.redo(); self?.refreshUndo()
        }
        stack.addArrangedSubview(undoButton)
        stack.addArrangedSubview(redoButton)
        scroll.showsHorizontalScrollIndicator = false
        scroll.showsVerticalScrollIndicator = false
        scroll.addSubview(favoritesStack)
        stack.addArrangedSubview(scroll)
        let all = button("All tools", symbol: "slider.horizontal.3") { [weak self] in self?.showTools() }
        all.accessibilityIdentifier = "drawing-all-tools"
        stack.addArrangedSubview(all)
        for name in [Notification.Name.NSUndoManagerDidUndoChange, .NSUndoManagerDidRedoChange, .NSUndoManagerDidCloseUndoGroup, .NSUndoManagerCheckpoint] {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                // Checkpoints can be sent while UndoManager is still closing a group.
                DispatchQueue.main.async { self?.refreshUndo() }
            })
        }
        rebuild()
    }
    required init?(coder: NSCoder) { fatalError("not used") }
    deinit { observers.forEach(NotificationCenter.default.removeObserver) }

    func activateInitialTool() { select(preferences.favorites.first(where: { $0.tool != nil }) ?? DrawingFavorite(kind: "pen")) }
    func refreshUndo() {
        undoButton.isEnabled = undoManagerProvider?()?.canUndo == true
        redoButton.isEnabled = undoManagerProvider?()?.canRedo == true
    }
    func select(_ favorite: DrawingFavorite) {
        if favorite.tool != nil {
            if activeTool?.id != favorite.id { previousTool = activeTool }
            activeTool = favorite
            if !favorite.isEraser { lastNonEraser = favorite }
        }
        onSelect?(favorite)
        rebuild()
    }
    func updateActiveTool(_ tool: DrawingFavorite) {
        guard tool.id == activeTool?.id else { return }
        if let index = preferences.favorites.firstIndex(where: { $0.id == tool.id }) {
            preferences.favorites[index] = tool
            preferences.save(to: defaults)
        }
        select(tool)
    }
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveTap tap: UIPencilInteraction.Tap) {
        performPencilAction(UIPencilInteraction.preferredTapAction)
    }
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveSqueeze squeeze: UIPencilInteraction.Squeeze) {
        if squeeze.phase == .ended { performPencilAction(UIPencilInteraction.preferredSqueezeAction) }
    }
    func performPencilAction(_ action: UIPencilPreferredAction) {
        switch action {
        case .switchEraser:
            dismissAttributes()
            if activeTool?.isEraser == true {
                select(lastNonEraser ?? preferences.favorites.first(where: { $0.ink != nil }) ?? DrawingFavorite(kind: "pen"))
            } else {
                select(preferences.favorites.first(where: { $0.isEraser }) ?? DrawingFavorite(kind: "eraser"))
            }
        case .switchPrevious:
            dismissAttributes()
            if let previousTool { select(previousTool) }
        case .showColorPalette, .showInkAttributes:
            if attributesController != nil { dismissAttributes() } else { showAttributes() }
        case .showContextualPalette:
            showTools()
        case .ignore, .runSystemShortcut: break
        @unknown default: break
        }
    }
    private func dismissAttributes() {
        attributesController?.dismiss(animated: true)
        attributesController = nil
    }
    private func showAttributes() {
        guard let activeTool, activeTool.ink != nil || activeTool.isEraser else { return }
        let controller = UIHostingController(rootView: DrawingToolAttributes(tool: activeTool) { [weak self] in self?.updateActiveTool($0) })
        attributesController = controller
        present(controller, size: CGSize(width: 320, height: 340), source: favoriteButtons[activeTool.id])
    }
    private func button(_ title: String, symbol: String, action: @escaping () -> Void) -> UIButton {
        let button = UIButton(type: .system)
        button.setImage(UIImage(systemName: symbol), for: .normal)
        button.accessibilityLabel = title
        button.addAction(UIAction { _ in action() }, for: .touchUpInside)
        button.widthAnchor.constraint(equalToConstant: 44).isActive = true
        button.heightAnchor.constraint(equalToConstant: 44).isActive = true
        return button
    }
    private func rebuild() {
        let ids = Set(displayedTools.map(\.id))
        for id in Array(favoriteButtons.keys) where !ids.contains(id) {
            favoriteButtons.removeValue(forKey: id)?.removeFromSuperview()
        }
        for (index, favorite) in displayedTools.enumerated() {
            // Keep button instances through a reorder or selection.
            let control = favoriteButtons[favorite.id] ?? button(favorite.title, symbol: favorite.symbol) { [weak self] in
                guard let self, let current = displayedTools.first(where: { $0.id == favorite.id }) else { return }
                if activeTool?.id == current.id { showAttributes() } else { select(current) }
            }
            if favoriteButtons[favorite.id] == nil {
                control.addGestureRecognizer(UILongPressGestureRecognizer(target: self, action: #selector(favoriteMenu(_:))))
            }
            favoriteButtons[favorite.id] = control
            control.setImage(UIImage(systemName: favorite.symbol), for: .normal)
            control.accessibilityLabel = favorite.title
            control.accessibilityIdentifier = "drawing-favorite-\(favorite.id)"
            control.tintColor = favorite.ink == nil ? .systemBlue : favorite.color
            control.backgroundColor = favorite.id == activeTool?.id ? .systemBlue.withAlphaComponent(0.15) : .clear
            control.layer.cornerRadius = 10
            control.accessibilityValue = favorite.widthRange == nil ? nil : String(format: "Width %.1f", favorite.effectiveWidth)
            if favorite.id == activeTool?.id { control.accessibilityTraits.insert(.selected) }
            else { control.accessibilityTraits.remove(.selected) }
            control.accessibilityHint = "Tap the selected tool for size and color. Hold to edit, move or remove this favorite."
            if favoritesStack.arrangedSubviews.count <= index || favoritesStack.arrangedSubviews[index] !== control {
                favoritesStack.insertArrangedSubview(control, at: index)
            }
        }
        refreshUndo()
        position()
    }
    @objc private func favoriteMenu(_ recognizer: UILongPressGestureRecognizer) {
        guard recognizer.state == .began, let source = recognizer.view,
              let id = favoriteButtons.first(where: { $0.value === source })?.key,
              let favorite = preferences.favorites.first(where: { $0.id == id }),
              let presenter, presenter.presentedViewController == nil else { return }
        // An explicit popover remains usable immediately after a reorder; a
        // UIButton context-menu preview can retain the tool's old position.
        let menu = UIAlertController(title: favorite.title, message: nil, preferredStyle: .actionSheet)
        menu.addAction(UIAlertAction(title: "Edit favorite", style: .default) { [weak self] _ in self?.edit(favorite) })
        menu.addAction(UIAlertAction(title: "Move earlier", style: .default) { [weak self] _ in self?.move(favorite, by: -1) })
        menu.addAction(UIAlertAction(title: "Move later", style: .default) { [weak self] _ in self?.move(favorite, by: 1) })
        menu.addAction(UIAlertAction(title: "Remove favorite", style: .destructive) { [weak self] _ in
            guard let self else { return }
            preferences.favorites.removeAll { $0.id == id }
            if activeTool?.id == id { activateInitialTool() }
            persist()
        })
        menu.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        menu.popoverPresentationController?.sourceView = source
        menu.popoverPresentationController?.sourceRect = source.bounds
        presenter.present(menu, animated: true)
    }
    private func move(_ favorite: DrawingFavorite, by delta: Int) {
        guard let index = preferences.favorites.firstIndex(where: { $0.id == favorite.id }), preferences.favorites.indices.contains(index + delta) else { return }
        preferences.favorites.swapAt(index, index + delta)
        persist()
    }
    private func persist() { preferences.save(to: defaults); rebuild() }

    func position() {
        guard let parent = superview else { return }
        let bounds = parent.bounds.inset(by: parent.safeAreaInsets).insetBy(dx: 8, dy: 8)
        guard bounds.width >= 44, bounds.height >= 44 else { return }
        frame = preferences.dock.frame(in: bounds, length: CGFloat(4 + displayedTools.count) * 44, offset: preferences.offset)
        setNeedsLayout()
    }
    override func layoutSubviews() {
        super.layoutSubviews()
        background.frame = bounds
        stack.frame = bounds
        let vertical = preferences.dock.vertical
        if (stack.axis == .vertical) != vertical { scroll.contentOffset = .zero }
        stack.axis = vertical ? .vertical : .horizontal
        favoritesStack.axis = stack.axis
        let length = CGFloat(displayedTools.count) * 44
        favoritesStack.frame = CGRect(origin: .zero, size: CGSize(width: vertical ? 44 : length, height: vertical ? length : 44))
        scroll.contentSize = favoritesStack.bounds.size
        // The scroll view fills the space between the fixed undo/redo/menu buttons.
        stack.distribution = .fill
    }
    @objc private func drag(_ recognizer: UIPanGestureRecognizer) {
        guard let parent = superview else { return }
        if recognizer.state == .began { dragOrigin = center }
        let delta = recognizer.translation(in: parent)
        center = CGPoint(x: dragOrigin.x + delta.x, y: dragOrigin.y + delta.y)
        if recognizer.state == .ended {
            let bounds = parent.bounds.inset(by: parent.safeAreaInsets).insetBy(dx: 8, dy: 8)
            preferences.dock = .nearest(to: recognizer.location(in: parent), in: bounds)
            let vertical = preferences.dock.vertical
            let length = min(CGFloat(4 + displayedTools.count) * 44, vertical ? bounds.height : bounds.width)
            let available = (vertical ? bounds.height : bounds.width) - length
            preferences.offset = available > 0 ? min(max(((vertical ? center.y - bounds.minY : center.x - bounds.minX) - length / 2) / available, 0), 1) : 0.5
            persist()
        } else if recognizer.state == .cancelled { position() }
    }
    private func showTools() {
        let controller = UIHostingController(rootView: DrawingToolsView(favorites: preferences.favorites, select: { [weak self] favorite in
            self?.presenter?.dismiss(animated: true) { self?.select(favorite) }
        },
            edit: { [weak self] in self?.edit($0) }, dock: { [weak self] in self?.preferences.dock = $0; self?.persist() }))
        present(controller)
    }
    private func edit(_ favorite: DrawingFavorite) {
        let controller = UIHostingController(rootView: DrawingFavoriteEditor(favorite: favorite) { [weak self] edited in
            guard let self else { return }
            if let index = preferences.favorites.firstIndex(where: { $0.id == edited.id }) { preferences.favorites[index] = edited }
            else { preferences.favorites.append(edited) }
            persist()
            if edited.tool != nil { select(edited) }
        })
        present(controller)
    }
    private func present(_ controller: UIViewController, size: CGSize = CGSize(width: 360, height: 520), source: UIView? = nil) {
        guard let presenter else { return }
        let show = {
            controller.modalPresentationStyle = .popover
            controller.preferredContentSize = size
            controller.popoverPresentationController?.sourceView = source ?? self
            controller.popoverPresentationController?.sourceRect = (source ?? self).bounds
            presenter.present(controller, animated: true)
        }
        if presenter.presentedViewController != nil { presenter.dismiss(animated: true, completion: show) } else { show() }
    }
}

/// Like the system palette: tap the selected tool, adjust, then keep drawing.
/// Favorites management remains separate from these immediate drawing controls.
private struct DrawingToolAttributes: View {
    @State var tool: DrawingFavorite
    let change: (DrawingFavorite) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if tool.isEraser {
                        Picker("Eraser mode", selection: $tool.kind) {
                            Text("Object").tag("eraser")
                            Text("Pixel").tag("pixelEraser")
                        }.pickerStyle(.segmented)
                            .onChange(of: tool.kind) { _, kind in
                                if kind == "pixelEraser" { tool.width = PKEraserTool.EraserType.fixedWidthBitmap.defaultWidth }
                            }
                    }
                    if let range = tool.widthRange {
                        Text("Size").font(.headline)
                        HStack(spacing: 8) {
                            ForEach(Array(tool.widthPresets.enumerated()), id: \.offset) { index, width in
                                Button { tool.width = width } label: {
                                    Circle().fill(tool.ink == nil ? Color.primary : Color(uiColor: tool.color))
                                        .frame(width: CGFloat(4 + index * 4), height: CGFloat(4 + index * 4))
                                        .frame(width: 44, height: 44)
                                        .background(abs(tool.effectiveWidth - width) < 0.01 ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                                }.buttonStyle(.plain)
                                    .accessibilityLabel("Size \(index + 1)")
                                    .accessibilityValue(String(format: "%.1f", width))
                                    .accessibilityIdentifier("drawing-size-\(index)")
                            }
                        }
                        Slider(value: Binding(get: { tool.effectiveWidth }, set: { tool.width = $0 }), in: range) { Text("Tool size") }
                            .accessibilityIdentifier("drawing-size-slider")
                        Text("Width \(tool.effectiveWidth, specifier: "%.1f")").monospacedDigit().foregroundStyle(.secondary)
                    }
                    if tool.ink != nil {
                        ColorPicker("Ink color", selection: Binding(get: { Color(uiColor: tool.color) }, set: { color in
                            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
                            UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: .light)).getRed(&r, green: &g, blue: &b, alpha: &a)
                            tool.red = r; tool.green = g; tool.blue = b
                        }), supportsOpacity: false)
                    }
                    if tool.kind == "eraser" { Text("Erases whole strokes and objects.").foregroundStyle(.secondary) }
                }.padding()
            }.navigationTitle(tool.title).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() }.accessibilityIdentifier("drawing-attributes-done") } }
        }.onChange(of: tool) { _, value in change(value) }
    }
}

private struct DrawingToolsView: View {
    let favorites: [DrawingFavorite]
    let select: (DrawingFavorite) -> Void
    let edit: (DrawingFavorite) -> Void
    let dock: (DrawingDock) -> Void
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section("All tools") {
                    ForEach(DrawingFavorite.kinds, id: \.self) { kind in
                        let favorite = DrawingFavorite(kind: kind, width: Double(DrawingFavorite(kind: kind).ink?.defaultWidth ?? 3))
                        HStack {
                            Button { select(favorite) } label: { Label(favorite.title, systemImage: favorite.symbol) }
                            Spacer()
                            Button { edit(favorite) } label: { Image(systemName: "star").frame(width: 44, height: 44) }
                                .accessibilityLabel("Add \(favorite.title) favorite")
                        }.buttonStyle(.borderless)
                    }
                }
                Section("Favorites · hold a toolbar tool to reorder or remove") {
                    ForEach(favorites) { favorite in
                        Button { edit(favorite) } label: { Label(favorite.title, systemImage: favorite.symbol) }
                    }
                }
                Section("Toolbar position") {
                    ForEach(DrawingDock.allCases, id: \.self) { edge in
                        Button(edge.rawValue.capitalized) { dock(edge); dismiss() }
                    }
                }
            }.accessibilityIdentifier("drawing-tools-list")
                .navigationTitle("Drawing tools").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Done") { dismiss() } }
        }
    }
}

private struct DrawingFavoriteEditor: View {
    @State var favorite: DrawingFavorite
    let save: (DrawingFavorite) -> Void
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            Form {
                Picker("Tool", selection: $favorite.kind) {
                    ForEach(DrawingFavorite.kinds, id: \.self) { kind in Text(DrawingFavorite(kind: kind).title).tag(kind) }
                }.onChange(of: favorite.kind) { _, _ in favorite.width = Double(favorite.ink?.defaultWidth ?? 3) }
                if let ink = favorite.ink {
                    ColorPicker("Ink color", selection: Binding(get: { Color(uiColor: favorite.color) }, set: { color in
                        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
                        UIColor(color).resolvedColor(with: UITraitCollection(userInterfaceStyle: .light)).getRed(&r, green: &g, blue: &b, alpha: &a)
                        favorite.red = r; favorite.green = g; favorite.blue = b
                    }), supportsOpacity: false)
                    Slider(value: $favorite.width, in: Double(ink.validWidthRange.lowerBound)...Double(ink.validWidthRange.upperBound)) { Text("Width") }
                    Text("Width \(favorite.width, specifier: "%.1f")").foregroundStyle(.secondary)
                }
            }.navigationTitle("Favorite tool").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) { Button("Save favorite") { save(favorite); dismiss() } }
                }
        }
    }
}
