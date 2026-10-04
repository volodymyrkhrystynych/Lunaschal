// Render the existing public/icons/icon.svg motif into opaque distribution icons.
import AppKit

let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let context = CGContext(data: nil, width: 1024, height: 1024, bitsPerComponent: 8,
                        bytesPerRow: 4096, space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> CGColor {
    CGColor(red: CGFloat((hex >> 16) & 255) / 255,
            green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: alpha)
}
context.setFillColor(color(0x101827))
context.fill(CGRect(x: 0, y: 0, width: 1024, height: 1024))
context.translateBy(x: 0, y: 1024)
context.scaleBy(x: 5.12, y: -5.12)
func line(_ x: CGFloat, _ y: CGFloat, _ endX: CGFloat, _ endY: CGFloat, _ width: CGFloat) {
    context.setStrokeColor(color(0x818cf8, 0.4))
    context.setLineWidth(width)
    context.move(to: CGPoint(x: x, y: y))
    context.addLine(to: CGPoint(x: endX, y: endY))
    context.strokePath()
}
func circle(_ x: CGFloat, _ y: CGFloat, _ radius: CGFloat, _ hex: UInt32, _ alpha: CGFloat = 1) {
    context.setFillColor(color(hex, alpha))
    context.fillEllipse(in: CGRect(x: x - radius, y: y - radius, width: radius * 2, height: radius * 2))
}
line(60, 75, 100, 130, 2.5)
line(140, 75, 100, 130, 2.5)
line(60, 75, 140, 75, 1)
circle(100, 130, 18, 0x3b82f6)
circle(106, 125, 4, 0x60a5fa, 0.7)
circle(60, 75, 14, 0x6366f1)
circle(50, 75, 14, 0xffffff, 0.9)
circle(140, 75, 8, 0x818cf8)
circle(140, 75, 6, 0xa5b4fc, 0.6)
let bitmap = NSBitmapImageRep(cgImage: context.makeImage()!)
let data = bitmap.representation(using: .png, properties: [:])!
for target in ["App", "Watch"] {
    let destination = root.appendingPathComponent("\(target)/Assets.xcassets/AppIcon.appiconset/Icon.png")
    try data.write(to: destination, options: .atomic)
}
