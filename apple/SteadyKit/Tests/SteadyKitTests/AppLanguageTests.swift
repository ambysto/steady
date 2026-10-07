import Foundation
import Testing
@testable import SteadyKit

/// The language picked in Settings, against a small bundle with real .lproj folders.
struct AppLanguageTests {
    /// en, vi and zh-Hans translate the Settings tab; only English has the app's name, as in
    /// the generated catalog where app.name is not translated.
    static func bundle() throws -> Bundle {
        let root = FileManager.default.temporaryDirectory.appending(path: "AppLanguageTests-\(UUID().uuidString)")
        let tables = [
            "Base": "",
            "en": #""ui.nav.settings" = "Settings"; "app.name" = "Ambysto Steady"; "apple.ui.language.auto" = "Same as system";"#,
            "vi": #""ui.nav.settings" = "Cài đặt"; "apple.ui.language.auto" = "Theo hệ thống";"#,
            "zh-Hans": #""ui.nav.settings" = "设置";"#,
        ]
        for (language, table) in tables {
            let folder = root.appending(path: "\(language).lproj")
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try Data(table.utf8).write(to: folder.appending(path: "Localizable.strings"))
        }
        return try #require(Bundle(url: root))
    }

    @Test func availableLanguagesComeFromTheBundleWithoutBase() throws {
        // English first, then by English name: Chinese before Vietnamese.
        #expect(AppLanguage.available(in: try Self.bundle()) == ["en", "zh-Hans", "vi"])
    }

    @Test func namesAreWrittenInTheirOwnLanguage() {
        #expect(AppLanguage.name(of: "vi") == "Tiếng Việt")
        #expect(AppLanguage.name(of: "fr") == "Français")
        #expect(AppLanguage.name(of: "pt-BR") == "Português (Brasil)")
        #expect(AppLanguage.name(of: "zh-Hans") == "简体中文")
    }

    @Test func aChosenLanguageKeepsTheSystemRegionAndClock() {
        let english = AppLanguage.locale(language: "en", system: Locale(identifier: "vi_VN"))
        #expect(english.language.languageCode == .english)
        #expect(english.region == .vietnam)
        #expect(english.hourCycle == .zeroToTwentyThree)   // not the US 12-hour clock
        // A 12-hour clock picked in the system settings stays too.
        let vietnamese = AppLanguage.locale(language: "vi", system: Locale(identifier: "de_DE@hours=h12"))
        #expect(vietnamese.language.languageCode == .vietnamese)
        #expect(vietnamese.region == .germany)
        #expect(vietnamese.hourCycle == .oneToTwelve)
        let chinese = AppLanguage.locale(language: "zh-Hans", system: Locale(identifier: "en_US"))
        #expect(chinese.language.script == .hanSimplified)
        #expect(chinese.region == .unitedStates)
    }

    @Test func aChosenLanguageWinsOverTheSystem() throws {
        let bundle = try Self.bundle()
        let text = Localizer(bundle: bundle, choice: "vi", preferences: ["zh-Hans-CN"])
        #expect(text.language == "vi")
        #expect(text("ui.nav.settings") == "Cài đặt")
        #expect(text("ui.language.auto") == "Theo hệ thống")   // the apple.* wording
        #expect(text("app.name") == "Ambysto Steady")          // untranslated: English
        let chinese = Localizer(bundle: bundle, choice: "zh-Hans", preferences: ["vi-VN"])
        #expect(chinese("ui.nav.settings") == "设置")
    }

    @Test func automaticFollowsTheSystem() throws {
        let bundle = try Self.bundle()
        #expect(Localizer(bundle: bundle, choice: AppLanguage.automatic, preferences: ["vi-VN", "en-US"])("ui.nav.settings") == "Cài đặt")
        #expect(Localizer(bundle: bundle, choice: AppLanguage.automatic, preferences: ["zh-Hans-CN"]).language == "zh-Hans")
        // A system language the app does not have: English.
        #expect(Localizer(bundle: bundle, choice: AppLanguage.automatic, preferences: ["de-DE"])("ui.nav.settings") == "Settings")
    }

    @Test func aLanguageNoLongerShippedFallsBackToAutomatic() throws {
        let bundle = try Self.bundle()
        // "ja" saved before Japanese was removed from the catalogs.
        let vietnamese = Localizer(bundle: bundle, choice: "ja", preferences: ["vi-VN"])
        #expect(vietnamese.language == "vi")
        #expect(vietnamese("ui.nav.settings") == "Cài đặt")
        let english = Localizer(bundle: bundle, choice: "ja", preferences: ["ja-JP"])
        #expect(english.language == "en")
        #expect(english("ui.nav.settings") == "Settings")
    }
}
