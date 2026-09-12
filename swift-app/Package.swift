// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LevelStudio",
    platforms: [.macOS(.v12)],
    targets: [
        .executableTarget(
            name: "LevelStudio",
            path: "Sources/LevelStudio"
        )
    ]
)
