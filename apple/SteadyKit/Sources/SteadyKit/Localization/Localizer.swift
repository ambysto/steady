import Foundation

/// Renders messages with the String Catalog generated from app/locales (ADR-0010).
///
/// Mirrors app/i18n.py: a key missing from the catalog renders as the key itself, an unknown
/// parameter stays as "{name}", and numbers use the language's decimal separator.
public struct Localizer: Sendable {
    public let bundle: Bundle
    /// Catalog language code ("en", "pt-BR", "zh-Hans"...), the name of its .lproj folder.
    public let language: String
    public let locale: Locale

    /// Uses the language the system picked for `bundle`, so text and numbers always agree.
    public init(bundle: Bundle = .main) {
        self.init(bundle: bundle, language: bundle.preferredLocalizations.first ?? "en")
    }

    public init(bundle: Bundle, language: String) {
        self.bundle = bundle
        self.language = language
        self.locale = Locale(identifier: language)
    }

    public func callAsFunction(_ key: String, _ params: [String: MessageValue] = [:]) -> String {
        render(Message(key, params))
    }

    public func callAsFunction(_ message: Message) -> String {
        render(message)
    }

    public func render(_ message: Message) -> String {
        // Messages keep the shared key (Python and Windows use it too); the Apple wording, when
        // there is one, only changes the text. Parameters are the same by construction.
        let catalogKey = MessageCatalog.appleVariants.contains(message.key) ? "apple." + message.key : message.key
        let format = localizedFormat(catalogKey)
        let plural = MessageCatalog.pluralKeys.contains(message.key)
        let arguments: [any CVarArg] = (MessageCatalog.arguments[message.key] ?? []).map { argument in
            let value = message.params[argument.name]
            if plural, argument.name == "count" {
                if case .number(let count)? = value { return Int(count) }
                return 0
            }
            guard let value else { return "{\(argument.name)}" }
            return render(value, format: argument.format)
        }
        // Every entry goes through String(format:): the catalog escapes a literal "%" as "%%".
        return String(format: format, locale: locale, arguments: arguments)
    }

    public func render(_ value: MessageValue, format: String? = nil) -> String {
        switch value {
        case .text(let text): text
        case .message(let message): render(message)
        case .number(let number): formatNumber(number, spec: format)
        case .list(let values): values.map { render($0, format: format) }.joined(separator: ", ")
        }
    }

    /// The subset of Python format specs the catalogs use: "[+].Nf" and ".N%".
    func formatNumber(_ number: Double, spec: String?) -> String {
        guard var spec = spec.map({ Substring($0) }) else { return plain(number) }
        let sign = spec.hasPrefix("+")
        if sign { spec = spec.dropFirst() }
        guard spec.hasPrefix("."), let kind = spec.last, let digits = Int(spec.dropFirst().dropLast()) else {
            return plain(number)
        }
        switch kind {
        case "f":
            let style = FloatingPointFormatStyle<Double>(locale: locale)
                .precision(.fractionLength(digits))
                .grouping(.never)
                .sign(strategy: sign ? .always() : .automatic)
            return number.formatted(style)
        case "%":
            return number.formatted(.percent.precision(.fractionLength(digits)).grouping(.never).locale(locale))
        default:
            return plain(number)
        }
    }

    private func plain(_ number: Double) -> String {
        if number.rounded() == number, abs(number) < 1e15 {
            return String(Int(number))
        }
        return number.formatted(FloatingPointFormatStyle<Double>(locale: locale).grouping(.never))
    }

    private func localizedFormat(_ key: String) -> String {
        guard let path = bundle.path(forResource: language, ofType: "lproj"),
              let localized = Bundle(path: path) else {
            return bundle.localizedString(forKey: key, value: nil, table: nil)
        }
        let format = localized.localizedString(forKey: key, value: nil, table: nil)
        guard format == key else { return format }
        // Not translated into this language (app.name, a new key): English, as Python's i18n
        // does. Asking `bundle` itself would ask the user's language again and show the key.
        guard let english = bundle.path(forResource: "en", ofType: "lproj").flatMap(Bundle.init(path:)) else {
            return format
        }
        return english.localizedString(forKey: key, value: nil, table: nil)
    }
}
