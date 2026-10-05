import Foundation
import Testing
@testable import SteadyKit

/// The Mac's side of checks #3 and #13: what stands in for BSSIDs, and the stored Wi‑Fi minutes.
struct InterferenceOnTheMacTests {
    typealias Sighting = Interference.Sighting

    @Test func ourRouterIsTheClosestSignalOnOurChannel() {
        // As measured on a Mac: our router 3 dB off our own reading, one weak neighbour.
        let current = Sighting(rssi: -54, channel: 40, band: "5 GHz")
        let around = [Sighting(rssi: -51, channel: 40, band: "5 GHz"), Sighting(rssi: -83, channel: 40, band: "5 GHz"),
                      Sighting(rssi: -47, channel: 1, band: "2.4 GHz")]
        let (connection, networks) = Interference.anonymous(current: current, around: around)
        #expect(networks[0].bssid == connection.bssid)
        #expect(Set(networks.map(\.bssid)).count == 3)
        let result = Interference.evaluate(connection, scan: networks)
        #expect(result.status == .ok)
        #expect(result.summary == Message("diag.interference.quiet", ["channel": .number(40), "count": .number(1)]))
    }

    @Test func ourRoutersOtherSSIDsShowAtTheSameSignal() {
        let current = Sighting(rssi: -54, channel: 40, band: "5 GHz")
        let around = [Sighting(rssi: -50, channel: 40, band: "5 GHz"),   // ours
                      Sighting(rssi: -52, channel: 40, band: "5 GHz"),   // our guest network
                      Sighting(rssi: -53, channel: 40, band: "5 GHz"),   // and another of ours
                      Sighting(rssi: -70, channel: 40, band: "5 GHz")]   // a neighbour
        let (connection, networks) = Interference.anonymous(current: current, around: around)
        let own = Interference.deviceKey(connection.bssid)
        #expect(networks.map { Interference.deviceKey($0.bssid) == own } == [true, true, true, false])
        #expect(Interference.evaluate(connection, scan: networks).status == .ok)
    }

    @Test func twoNeighboursOnOurChannelWarn() {
        let current = Sighting(rssi: -54, channel: 40, band: "5 GHz")
        let around = [Sighting(rssi: -54, channel: 40, band: "5 GHz"), Sighting(rssi: -66, channel: 40, band: "5 GHz"),
                      Sighting(rssi: -75, channel: 40, band: "5 GHz"), Sighting(rssi: -60, channel: 149, band: "5 GHz")]
        let (connection, networks) = Interference.anonymous(current: current, around: around)
        let result = Interference.evaluate(connection, scan: networks)
        #expect(result.status == .warn)
        #expect(result.advice == .message(Message("diag.interference.advice_5g", ["group": .text("149–161"),
                                                                                  "channel": .number(153)])))
    }

    @Test func withoutOurRouterInTheScanEveryNetworkIsForeign() {
        let current = Sighting(rssi: -54, channel: 40, band: "5 GHz")
        let around = [Sighting(rssi: -70, channel: 40, band: "5 GHz"), Sighting(rssi: -71, channel: 40, band: "5 GHz")]
        let (connection, networks) = Interference.anonymous(current: current, around: around)
        #expect(!networks.contains { $0.bssid == connection.bssid })
        #expect(Interference.evaluate(connection, scan: networks).status == .warn)
    }

    @Test func deviceKeyIsTheFirstFiveOctets() {
        #expect(Interference.deviceKey("02:5E:00:9a:40:24") == "02:5e:00:9a:40")
        #expect(Interference.deviceKey("not a bssid") == "")
        #expect(Interference.deviceKey("") == "")
    }
}

@MainActor
struct PhysicalLinkOnTheMacTests {
    @Test func wifiMinutesAreStoredReplacedPurgedAndDeleted() throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "steady-test-\(UUID().uuidString).sqlite")
        defer { try? FileManager.default.removeItem(at: url) }
        let store = try MinuteStore(url: url)
        let now = 1_790_000_040
        try store.insert(WiFiMinute(ts: now - 40 * 86_400, state: "connected", rssi: -60, txMbps: 400))
        try store.insert(WiFiMinute(ts: now, state: "connected", channel: 40, signal: 92, rssi: -54, txMbps: 720))
        try store.insert(WiFiMinute(ts: now, state: "connected", channel: 40, signal: 90, rssi: -55, txMbps: 650))
        #expect(try store.wifiMinutes(since: 0).count == 2)
        try store.purge(now: Double(now))
        #expect(try store.wifiMinutes(since: 0) == [WiFiMinute(ts: now, state: "connected", channel: 40, signal: 90,
                                                               rssi: -55, txMbps: 650)])
        try store.deleteAll()
        #expect(try store.wifiMinutes(since: 0).isEmpty)
    }

    @Test func theMacJudgesItsLinkFromStoredMinutesWithItsTransmitRate() throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "steady-test-\(UUID().uuidString).sqlite")
        defer { try? FileManager.default.removeItem(at: url) }
        let clock = LiveMonitorTests.FakeClock()
        let monitor = LiveMonitor(store: try MinuteStore(url: url), now: { clock.now })
        #expect(monitor.physicalLink == nil)   // no Wi‑Fi data: iPhone and iPad never show #13
        monitor.wifi = WiFiSignal.State(state: "connected", channel: 40, signal: 90, rssi: -55, txMbps: 720)
        #expect(monitor.physicalLink?.summary.key == "diag.physical_link.not_enough")
        for minute in 0..<31 {
            if minute == 20 { monitor.wifi?.txMbps = 24 }   // a slow, lossy stretch
            for second in 0..<60 {
                let lost = minute >= 20 && second < 6        // 10% to the router
                monitor.record([("router", lost ? nil : 3)])
                clock.advance(1)
            }
        }
        monitor.record([])
        let result = try #require(monitor.physicalLink)
        #expect(result.status == .warn)                    // 11 of 31 minutes
        #expect(result.details.first == .message(Message("diag.physical_link.bad_minutes", [
            "bad": .number(11), "total": .number(31), "fraction": .number(11.0 / 31),
        ])))
        let relaunched = LiveMonitor(store: try MinuteStore(url: url), now: { clock.now })
        #expect(relaunched.physicalLink?.status == .warn)
    }
}

struct NotAssessedTests {
    @Test func checksWithNothingToGoOnAreNotAssessed() {
        #expect(!PingQuality.evaluate(hour: [], recent: []).isAssessed)
        #expect(!PhysicalLink.evaluate([], now: nil).isAssessed)
        #expect(!Interference.evaluate(nil, scan: []).isAssessed)
        #expect(!WiFiSignal.evaluate(nil).isAssessed)
        #expect(!WiFiSignal.evaluate(WiFiSignal.State(state: "disconnected")).isAssessed)
        #expect(VPNCheck.evaluate([]).isAssessed)
        #expect(PingQuality.evaluate(hour: [.init(target: "router", sent: 60, lost: 0)], recent: []).isAssessed)
    }

    @Test func everyNotAssessedSummaryExists() throws {
        #expect(CheckResult.notAssessed.isSubset(of: try RepositoryData.englishKeys()))
    }
}

