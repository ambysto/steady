import Foundation
import Network
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
        // Keys OverviewView renders directly.
        var used: Set<String> = ["ui.path.checking", "ui.path.connection", "ui.path.ip", "ui.path.dns",
                                 "ui.overview.router", "ui.overview.internet", "ui.overview.latency",
                                 "ui.overview.see_all", "ui.nav.overview", "ui.live.tcp", "ui.live.window_note",
                                 "ui.live.measuring", "ui.live.no_reply", "ui.live.rtt", "ui.live.loss",
                                 "ui.live.jitter", "ui.diag.run", "ui.diag.running", "ui.diag.on_demand",
                                 "ui.diag.bufferbloat_run", "ui.diag.bufferbloat_confirm", "ui.sheet.cancel",
                                 "diag.bufferbloat.title", "diag.bufferbloat.download", "diag.bufferbloat.upload"]
        used.formUnion([CheckStatus.ok, .info, .warn, .bad].map { "ui.status." + $0.rawValue })
        for check in [PingQuality.evaluate(hour: [], recent: []), DNSBenchmark.evaluate([], inUse: [], roles: [:]),
                      WiFiSignal.evaluate(nil), VPNCheck.evaluate([]),
                      VPNCheck.evaluate([VPNCheck.Adapter(name: "utun7", status: "Up", isTunnel: true)])] {
            used.formUnion([check.title.key, check.summary.key])
            if case .message(let advice) = check.advice { used.insert(advice.key) }
        }
        for path in paths {
            used.insert(path.statusMessage.key)
            used.insert(path.dnsMessage.key)
            if let link = path.linkMessage { used.insert(link.key) }
            if let reason = path.reasonMessage { used.insert(reason.key) }
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

struct AddressFormatTests {
    /// NWInterface has no public initializer: take the loopback interface from a path monitor.
    static func loopback() async -> NWInterface? {
        for await path in NWPathMonitor(requiredInterfaceType: .loopback) {
            return path.availableInterfaces.first
        }
        return nil
    }

    @Test func ipv4AndIPv6AreShownOnceWithTheirScope() async throws {
        let v4 = try #require(IPv4Address("192.0.2.1"))
        #expect(NetworkPath.address(.ipv4(v4)) == "192.0.2.1")
        #expect(NetworkPath.address(.ipv6(try #require(IPv6Address("2001:db8::1")))) == "2001:db8::1")
        let interface = try #require(await Self.loopback())
        let linkLocal = try #require(IPv6Address("fe80::1"))
        let scoped = try #require(IPv6Address(linkLocal.rawValue, interface))
        #expect(NetworkPath.address(.ipv6(scoped)) == "fe80::1%\(interface.name)")
        // As the kernel hands it over: interface index 11 embedded in bytes 2–3.
        var embedded = linkLocal.rawValue
        embedded[3] = 0x0B
        let kernel = try #require(IPv6Address(embedded, interface))
        #expect(NetworkPath.address(.ipv6(kernel)) == "fe80::1%\(interface.name)")
    }
}
