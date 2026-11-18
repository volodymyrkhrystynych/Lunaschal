import SwiftUI
import LunaschalCore

/// Work, leisure or mix as one round glyph: the state's symbol in its
/// calendar colour on a disc tinted the same. Mix is work's briefcase filled
/// half work, half leisure, on a half-and-half disc. Compiled into the phone
/// app, the Watch app and the Watch complications (see project.yml), so the
/// state looks the same on every surface; nil is the state never set.
struct ActivityGlyph: View {
    let state: ActivityState?
    /// Filled solid with a white symbol: the Watch tile for the state that is on.
    var active = false

    var body: some View {
        GeometryReader { box in
            let side = min(box.size.width, box.size.height)
            ZStack {
                Circle().fill(disc)
                Image(systemName: state?.symbol ?? "circle.dashed")
                    .font(.system(size: side * 0.48, weight: .semibold))
                    .foregroundStyle(symbol)
            }
            .frame(width: side, height: side)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .aspectRatio(1, contentMode: .fit)
        .accessibilityElement()
        .accessibilityLabel(state.map { "\($0.title) state" } ?? "No state set")
    }

    private var colors: [Color] { state?.colors.map(Color.init(activityHex:)) ?? [.gray] }

    /// Left to right, the halves meeting in a hard edge rather than a blend.
    private func split(_ colors: [Color]) -> AnyShapeStyle {
        guard colors.count == 2 else { return AnyShapeStyle(colors[0]) }
        return AnyShapeStyle(LinearGradient(stops: [
            .init(color: colors[0], location: 0), .init(color: colors[0], location: 0.5),
            .init(color: colors[1], location: 0.5), .init(color: colors[1], location: 1),
        ], startPoint: .leading, endPoint: .trailing))
    }

    private var disc: AnyShapeStyle { split(colors.map { $0.opacity(active ? 1 : 0.25) }) }
    private var symbol: AnyShapeStyle { active ? AnyShapeStyle(Color.white) : split(colors) }
}

extension Color {
    /// A calendar category colour, 0xRRGGBB.
    init(activityHex hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255)
    }
}
