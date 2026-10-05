/// Text kept as a catalog key plus parameters and rendered only when shown (ADR-0006), the
/// Swift counterpart of `msg()` in app/i18n.py. JSON form: `{"key": "...", "params": {...}}`.
public struct Message: Hashable, Sendable, Codable {
    public var key: String
    public var params: [String: MessageValue]

    public init(_ key: String, _ params: [String: MessageValue] = [:]) {
        self.key = key
        self.params = params
    }

    private enum CodingKeys: String, CodingKey { case key, params }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        key = try container.decode(String.self, forKey: .key)
        params = try container.decodeIfPresent([String: MessageValue].self, forKey: .params) ?? [:]
    }
}

/// A message parameter: plain text, a number (formatted when rendered), a nested message, or a
/// list of those (rendered "a, b, c", as in app/i18n.py).
public enum MessageValue: Hashable, Sendable, Codable {
    case text(String)
    case number(Double)
    case message(Message)
    case list([MessageValue])

    /// Python passes "" where an optional part of a sentence is absent.
    public static let empty = MessageValue.text("")

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let text = try? container.decode(String.self) {
            self = .text(text)
        } else if let number = try? container.decode(Double.self) {
            self = .number(number)
        } else if let list = try? container.decode([MessageValue].self) {
            self = .list(list)
        } else {
            self = .message(try container.decode(Message.self))
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .text(let text): try container.encode(text)
        case .number(let number): try container.encode(number)
        case .message(let message): try container.encode(message)
        case .list(let list): try container.encode(list)
        }
    }
}

extension MessageValue: ExpressibleByStringLiteral, ExpressibleByIntegerLiteral, ExpressibleByFloatLiteral {
    public init(stringLiteral value: String) { self = .text(value) }
    public init(integerLiteral value: Int) { self = .number(Double(value)) }
    public init(floatLiteral value: Double) { self = .number(value) }
}

/// One parameter of a catalog entry, as listed in the generated `MessageCatalog`.
public struct MessageArgument: Sendable {
    public let name: String
    /// English format spec such as ".1f", "+.0f" or ".0%"; nil for plain values.
    public let format: String?

    public init(_ name: String, _ format: String?) {
        self.name = name
        self.format = format
    }
}
