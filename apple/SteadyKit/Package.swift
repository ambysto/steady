// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SteadyKit",
    platforms: [.iOS("26.0"), .macOS("26.0")],
    products: [
        .library(name: "SteadyKit", targets: ["SteadyKit"]),
    ],
    targets: [
        .target(name: "SteadyKit"),
        .testTarget(name: "SteadyKitTests", dependencies: ["SteadyKit"]),
    ]
)
