/// Where a chapter reopens, in block indices. Every spoiler starts closed, so
/// a fraction is a share of the blocks that show then: the ones inside a
/// closed spoiler have no row to scroll to.
public enum ChapterRows {
    /// The blocks showing with every spoiler closed.
    public static func closed(_ blocks: [ChapterBlock]) -> [Int] {
        blocks.indices.filter { blocks[$0].spoilers.isEmpty }
    }

    /// The row a bookmark's fraction means.
    public static func row(at fraction: Double, in blocks: [ChapterBlock]) -> Int {
        let rows = closed(blocks)
        guard fraction.isFinite, !rows.isEmpty else { return 0 }
        return rows[Int((min(1, max(0, fraction)) * Double(rows.count - 1)).rounded())]
    }

    /// The fraction that reopens at `row`, or at the spoiler's title when it
    /// is inside one.
    public static func fraction(of row: Int, in blocks: [ChapterBlock]) -> Double {
        let rows = closed(blocks)
        guard rows.count > 1, let index = rows.lastIndex(where: { $0 <= row }) else { return 0 }
        return Double(index) / Double(rows.count - 1)
    }

    /// A saved row and the spoilers to open so that it shows. Row 0, the
    /// start, for one the chapter no longer has.
    public static func restore(saved: Int, in blocks: [ChapterBlock]) -> (row: Int, open: Set<Int>) {
        guard blocks.indices.contains(saved) else { return (0, []) }
        return (saved, Set(blocks[saved].spoilers))
    }
}
