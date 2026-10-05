import Foundation
import Testing
@testable import SteadyKit

struct LocalizationTests {
    /// No bundle in tests: every key renders as itself, which is enough to test number formatting.
    let english = Localizer(bundle: .main, language: "en")
    let vietnamese = Localizer(bundle: .main, language: "vi")
    let french = Localizer(bundle: .main, language: "fr")

    @Test func numbersFollowThePythonFormatSpecs() {
        #expect(english.formatNumber(12.345, spec: ".1f") == "12.3")
        #expect(english.formatNumber(1.5, spec: ".2f") == "1.50")
        #expect(english.formatNumber(7, spec: "+.0f") == "+7")
        #expect(english.formatNumber(-7, spec: "+.0f") == "-7")
        #expect(english.formatNumber(0.2, spec: ".0%") == "20%")
        #expect(english.formatNumber(1500, spec: nil) == "1500")
    }

    @Test func decimalCommaFollowsTheLanguage() {
        #expect(vietnamese.formatNumber(12.345, spec: ".1f") == "12,3")
        #expect(french.formatNumber(1.5, spec: ".2f") == "1,50")
        #expect(vietnamese.formatNumber(1500, spec: nil) == "1500")   // integers are left alone
    }

    @Test func missingKeyRendersAsTheKey() {
        #expect(english("no.such.key") == "no.such.key")
    }

    @Test func messagesRoundTripThroughPythonJSON() throws {
        let json = #"{"key": "diag.ping.line", "params": {"target": "router", "loss": 1.5, "jitter": "", "recent": {"key": "diag.ping.recent", "params": {"loss": 0.0}}}}"#
        let message = try JSONDecoder().decode(Message.self, from: Data(json.utf8))
        #expect(message.params["target"] == .text("router"))
        #expect(message.params["loss"] == .number(1.5))
        #expect(message.params["jitter"] == .empty)
        #expect(message.params["recent"] == .message(Message("diag.ping.recent", ["loss": 0])))
    }

    @Test func everyKeyTheAppUsesExistsInEnglish() throws {
        let keys = try RepositoryData.englishKeys()
        let paths = [
            NetworkPath(status: .connected, link: .wifi, isExpensive: true, isConstrained: true, supportsDNS: true),
            NetworkPath(status: .connected, link: .wired),
            NetworkPath(status: .connected, link: .cellular),
            NetworkPath(status: .requiresConnection, link: .other, reason: .vpnInactive),
            NetworkPath(status: .disconnected, reason: .notAvailable),
            NetworkPath(status: .disconnected, reason: .cellularDenied),
            NetworkPath(status: .disconnected, reason: .wifiDenied),
            NetworkPath(status: .disconnected, reason: .localNetworkDenied),
        ]
        var used: Set<String> = ["ui.path.checking", "ui.path.connection", "ui.path.ip", "ui.path.dns",
                                 "ui.overview.router", "ui.nav.overview", "diag.ping.title"]
        for path in paths {
            used.insert(path.statusMessage.key)
            used.insert(path.dnsMessage.key)
            path.linkMessage.map { used.insert($0.key) }
            path.reasonMessage.map { used.insert($0.key) }
            path.notes.forEach { used.insert($0.key) }
            if case .message(let none) = path.ipVersions { used.insert(none.key) }
        }
        #expect(used.subtracting(keys).isEmpty, "missing from app/locales/en.json: \(used.subtracting(keys).sorted())")
    }

    @Test func notesSkipTheReasonTheStatusAlreadyGives() {
        let offline = NetworkPath(status: .disconnected, reason: .notAvailable)
        #expect(offline.notes.isEmpty)
        let lowData = NetworkPath(status: .connected, link: .wifi, isConstrained: true, supportsIPv4: true, supportsIPv6: true)
        #expect(lowData.notes == [Message("ui.path.constrained")])
        #expect(lowData.ipVersions == .text("IPv4 · IPv6"))
    }
}
