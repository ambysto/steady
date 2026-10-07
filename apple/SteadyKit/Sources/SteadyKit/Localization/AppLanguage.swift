import Foundation

/// The language picked in Settings: "auto" (the system's choice for the app) or a catalog
/// language the bundle has, mirroring `ui.language` on Windows (app/i18n.py).
public enum AppLanguage {
    /// Stored when the app follows the system.
    public static let automatic = "auto"
    /// UserDefaults key of the choice (no dots: SwiftUI observes it by key-value observing).
    public static let storageKey = "language"

    /// Catalog languages the bundle ships, English first and then by English name, as the
    /// Windows settings list them. Read from the bundle, so a language removed from
    /// app/locales disappears here too.
    public static func available(in bundle: Bundle = .main) -> [String] {
        let english = Locale(identifier: "en")
        return bundle.localizations
            .filter { $0 != "Base" }
            .sorted { a, b in
                if (a == "en") != (b == "en") { return a == "en" }
                return (english.localizedString(forIdentifier: a) ?? a) < (english.localizedString(forIdentifier: b) ?? b)
            }
    }

    /// The language's name in that language ("Tiếng Việt", "Français"), like `_meta.name`.
    public static func name(of code: String) -> String {
        let locale = Locale(identifier: code)
        guard let name = locale.localizedString(forIdentifier: code), let first = name.first else { return code }
        return String(first).uppercased(with: locale) + name.dropFirst()
    }

    /// The catalog language to show for a stored choice. "auto", or a language no longer in the
    /// bundle (one picked before it was removed), follows the user's preferred languages and
    /// falls back to English.
    public static func resolve(_ choice: String, available: [String], preferences: [String]) -> String {
        if choice != automatic, available.contains(choice) { return choice }
        return Bundle.preferredLocalizations(from: available, forPreferences: preferences).first ?? "en"
    }
}

extension Localizer {
    /// The Localizer for a choice stored in Settings (`AppLanguage`).
    /// `preferences` defaults to the system's list, which includes a per-app language set in
    /// the system Settings.
    public init(bundle: Bundle = .main, choice: String, preferences: [String] = Locale.preferredLanguages) {
        let available = AppLanguage.available(in: bundle)
        self.init(bundle: bundle, language: AppLanguage.resolve(choice, available: available, preferences: preferences))
    }
}
