// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AmbystoSteadyInstaller",
    defaultLocalization: "en",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "AmbystoInstaller", targets: ["AmbystoInstaller"])],
    targets: [.executableTarget(name: "AmbystoInstaller", resources: [.process("Resources")])],
    swiftLanguageModes: [.v6]
)
