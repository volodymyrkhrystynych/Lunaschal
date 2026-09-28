// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LunaschalCore",
    platforms: [.iOS(.v17), .macOS(.v13), .watchOS(.v10)],
    products: [.library(name: "LunaschalCore", targets: ["LunaschalCore"])],
    targets: [
        .systemLibrary(name: "CSQLite", pkgConfig: "sqlite3"),
        .target(name: "LunaschalCore", dependencies: ["CSQLite"]),
        .testTarget(name: "LunaschalCoreTests", dependencies: ["LunaschalCore"])
    ]
)
