// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LunaschalCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "LunaschalCore", targets: ["LunaschalCore"])],
    targets: [
        .target(name: "LunaschalCore"),
        .testTarget(name: "LunaschalCoreTests", dependencies: ["LunaschalCore"])
    ]
)
