import Foundation
@testable import SteadyKit

/// Files shared with the other platforms, read straight from the repository (ADR-0010).
enum RepositoryData {
    /// apple/SteadyKit/Tests/SteadyKitTests/<this file> -> repository root.
    static let root = URL(filePath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()

    static func data(_ path: String) throws -> Data {
        try Data(contentsOf: root.appending(path: path))
    }

    /// Keys of app/locales/en.json, the source of every user-facing text.
    static func englishKeys() throws -> Set<String> {
        let catalog = try JSONSerialization.jsonObject(with: data("app/locales/en.json")) as? [String: Any] ?? [:]
        return Set(catalog.keys)
    }
}

/// Equality that tolerates floating-point noise in numbers, as the Python vector test does.
func approximatelyEqual(_ a: MessageValue, _ b: MessageValue) -> Bool {
    switch (a, b) {
    case (.text(let x), .text(let y)): x == y
    case (.number(let x), .number(let y)): abs(x - y) <= 1e-9
    case (.message(let x), .message(let y)): approximatelyEqual(x, y)
    case (.list(let x), .list(let y)): x.count == y.count && zip(x, y).allSatisfy(approximatelyEqual)
    default: false
    }
}

func approximatelyEqual(_ a: Message, _ b: Message) -> Bool {
    a.key == b.key && a.params.keys == b.params.keys
        && a.params.allSatisfy { key, value in approximatelyEqual(value, b.params[key]!) }
}
