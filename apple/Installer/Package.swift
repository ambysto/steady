// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AmbystoSteadyInstaller",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "AmbystoInstaller", targets: ["AmbystoInstaller"])],
    targets: [.executableTarget(name: "AmbystoInstaller")],
    swiftLanguageModes: [.v6]
)
