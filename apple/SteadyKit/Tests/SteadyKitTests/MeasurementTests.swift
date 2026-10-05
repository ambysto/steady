import Foundation
import Synchronization
import Testing
@testable import SteadyKit

/// Mirrors MinuteAggregatorTests in tests/test_monitor.py, so both platforms build the same rows.
struct MinuteAggregatorTests {
    let base = 1_790_000_040.0   // a minute boundary

    @Test func rollsOnlyWhenTheMinuteChanges() {
        var aggregator = MinuteAggregator()
        #expect(aggregator.roll(at: base) == nil)
        aggregator.add("router", rttMs: 5)
        #expect(aggregator.roll(at: base + 59) == nil)
        let minute = aggregator.roll(at: base + 60)
        #expect(minute?.start == Int(base))
        #expect(minute?.rows.count == 1)
        #expect(aggregator.roll(at: base + 61) == nil)
    }

    @Test func countsLossAndJitterLikePython() {
        var aggregator = MinuteAggregator()
        _ = aggregator.roll(at: base)
        for rtt in [10.0, 20.0, 40.0, nil] {
            aggregator.add("router", rttMs: rtt)
        }
        let rows = aggregator.roll(at: base + 60)?.rows
        #expect(rows == [PingQuality.Row(target: "router", sent: 4, lost: 1, jitter: 15, avg: 23.3, max: 40)])
    }

    @Test func allLostAndSingleSampleHaveNoJitter() {
        var aggregator = MinuteAggregator()
        _ = aggregator.roll(at: base)
        aggregator.add("dead", rttMs: nil)
        aggregator.add("dead", rttMs: nil)
        aggregator.add("one", rttMs: 7)
        let rows = aggregator.roll(at: base + 60)?.rows
        #expect(rows == [PingQuality.Row(target: "dead", sent: 2, lost: 2, jitter: nil),
                         PingQuality.Row(target: "one", sent: 1, lost: 0, jitter: nil, avg: 7, max: 7)])
    }

    @Test func aMinuteWithoutSamplesProducesNoRows() {
        var aggregator = MinuteAggregator()
        _ = aggregator.roll(at: base)
        #expect(aggregator.roll(at: base + 300) == nil)
    }

    @Test func jitterRoundsHalvesToEvenLikePython() {
        #expect(MinuteAggregator.roundToTenth(0.25) == 0.2)
        #expect(MinuteAggregator.roundToTenth(12.34) == 12.3)
    }
}

struct LiveStatsTests {
    @Test func lossJitterAndLatest() {
        let stats = LiveStats(samples: [10, nil, 20, 40, nil])
        #expect(stats.lossPercent == 40)
        #expect(stats.jitter == 15)
        #expect(stats.latest == .some(nil))
        #expect(LiveStats(samples: []).latest == nil)
    }
}

@MainActor
struct LiveMonitorTests {
    final class FakeClock: Sendable {
        let time = Mutex(1_790_000_040.0)
        func advance(_ seconds: Double) { time.withLock { $0 += seconds } }
        var now: Double { time.withLock { $0 } }
    }

    @Test func oneMinuteOfCleanPingsGivesAnOkVerdict() {
        let clock = FakeClock()
        let monitor = LiveMonitor(now: { clock.now })
        monitor.routerAddress = "192.0.2.1"
        #expect(monitor.pingQuality.summary == Message("diag.ping.no_data"))
        for _ in 0..<60 {
            monitor.record([("router", 3), ("cloudflare", 20), ("google", 21)])
            clock.advance(1)
        }
        monitor.record([])   // closes the minute
        #expect(monitor.minutes.count == 1)
        #expect(monitor.pingQuality.status == .ok)
        #expect(monitor.stats(for: monitor.targets[0]).lossPercent == 0)
    }

    @Test func keepsOnlyTheLastSixtySamplesAndAnHourOfMinutes() {
        let clock = FakeClock()
        let monitor = LiveMonitor(now: { clock.now })
        for _ in 0..<130 {
            monitor.record([("cloudflare", nil)])
            clock.advance(60)
        }
        #expect(monitor.samples["cloudflare"]?.count == 60)
        #expect(monitor.minutes.count <= 61)
    }

    @Test func aNewRouterStartsFromScratch() {
        let monitor = LiveMonitor()
        monitor.routerAddress = "192.0.2.1"
        monitor.record([("router", 3)])
        monitor.routerAddress = "198.51.100.1"
        #expect(monitor.samples["router"] == nil)
        #expect(monitor.targets.first?.address == "198.51.100.1")
    }
}

struct ICMPPacketTests {
    @Test func echoRequestHasAValidChecksum() {
        let packet = ICMPPing.echoRequest(identifier: 0x1234, sequence: 7)
        #expect(packet[0] == 8)
        #expect(packet.count == 40)
        #expect(ICMPPing.checksum(packet) == 0)   // a correct checksum sums to zero
    }

    @Test func replyIsMatchedWithOrWithoutTheIPHeader() {
        var reply = ICMPPing.echoRequest(identifier: 0x1234, sequence: 7)
        reply[0] = 0
        let ipHeader: [UInt8] = [0x45] + Array(repeating: 0, count: 19)
        #expect(ICMPPing.isEchoReply(reply[...], sequence: 7))
        #expect(ICMPPing.isEchoReply((ipHeader + reply)[...], sequence: 7))
        #expect(!ICMPPing.isEchoReply((ipHeader + reply)[...], sequence: 8))
        #expect(!ICMPPing.isEchoReply(ICMPPing.echoRequest(identifier: 1, sequence: 7)[...], sequence: 7))   // a request, not a reply
    }
}

struct DNSProbeTests {
    @Test func queryPacketMatchesDnsprobePy() {
        // app/dnsprobe.py build_query("google.com", qid=0x1234)
        let expected: [UInt8] = [0x12, 0x34, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                                 6] + Array("google".utf8) + [3] + Array("com".utf8) + [0, 0, 1, 0, 1]
        #expect(DNSProbe.buildQuery("google.com", identifier: 0x1234) == expected)
        #expect(DNSProbe.buildQuery("", identifier: 1) == nil)
        #expect(DNSProbe.buildQuery(String(repeating: "a", count: 64) + ".com", identifier: 1) == nil)
    }

    @Test func repliesAreMatchedOnIdAndResponseBit() {
        var reply: [UInt8] = [0x12, 0x34, 0x81, 0x83] + Array(repeating: 0, count: 8)   // NXDOMAIN
        #expect(DNSProbe.responseCode(reply[...], identifier: 0x1234) == 3)
        #expect(DNSProbe.responseCode(reply[...], identifier: 0x1235) == nil)
        reply[2] = 0x01   // a query, not a response
        #expect(DNSProbe.responseCode(reply[...], identifier: 0x1234) == nil)
        #expect(DNSProbe.responseCode(reply[..<11], identifier: 0x1234) == nil)
    }

    @Test func planMeasuresInUseThenRouterThenPublic() {
        let plan = DNSBenchmark.plan(inUse: ["192.0.2.1", "fe80::1", "192.0.2.1"], router: "192.0.2.1")
        #expect(plan.servers == ["192.0.2.1", "1.1.1.1", "8.8.8.8", "9.9.9.9"])
        #expect(plan.roles["192.0.2.1"] == .inUseRouter)
        let other = DNSBenchmark.plan(inUse: ["2001:db8::53"], router: "192.0.2.1")
        #expect(other.servers == ["2001:db8::53", "192.0.2.1", "1.1.1.1", "8.8.8.8", "9.9.9.9"])
        #expect(other.roles["192.0.2.1"] == .router)
    }

    @Test func linkLocalAndScopedServersAreNotUsable() {
        #expect(DNSBenchmark.isUsable("203.0.113.53"))
        #expect(DNSBenchmark.isUsable("2001:db8::53"))
        #expect(!DNSBenchmark.isUsable("fe80::1"))
        #expect(!DNSBenchmark.isUsable("febf::1"))
        #expect(!DNSBenchmark.isUsable("2001:db8::53%en0"))
        #expect(!DNSBenchmark.isUsable("not an address"))
    }
}

struct WiFiSignalTests {
    @Test func qualityFollowsWindowsMapping() {
        #expect(WiFiSignal.State.quality(rssi: -50) == 100)
        #expect(WiFiSignal.State.quality(rssi: -75) == 50)
        #expect(WiFiSignal.State.quality(rssi: -110) == 0)
    }

    @Test func unreadableValuesRenderAsUnknown() {
        let mac = WiFiSignal.State(state: "connected", channel: 40, radioType: "802.11ax", signal: 88, rssi: -56, txMbps: 720)
        let result = WiFiSignal.evaluate(mac)
        #expect(result.status == .ok)
        guard case .message(let network) = result.details[0], case .message(let rates) = result.details[2] else {
            Issue.record("unexpected details \(result.details)")
            return
        }
        #expect(network.params["ssid"] == .message(Message("diag.common.unknown")))
        #expect(rates.params["rx"] == .message(Message("diag.common.unknown")))
        #expect(rates.params["tx"] == .number(720))
    }
}

struct MinuteStoreTests {
    let base = 1_790_000_040
    let url = FileManager.default.temporaryDirectory.appending(path: "steady-test-\(UUID().uuidString).sqlite")

    func minute(_ start: Int, _ rows: [PingQuality.Row]) -> MinuteAggregator.Minute {
        MinuteAggregator.Minute(start: start, rows: rows)
    }

    @Test func minutesSurviveReopening() throws {
        defer { try? FileManager.default.removeItem(at: url) }
        do {
            let store = try MinuteStore(url: url)
            try store.insert(minute(base, [.init(target: "router", sent: 60, lost: 1, jitter: 1.5),
                                           .init(target: "cloudflare", sent: 60, lost: 0, jitter: nil)]))
            try store.insert(minute(base + 60, [.init(target: "router", sent: 60, lost: 0, jitter: 2)]))
        }
        let reopened = try MinuteStore(url: url)
        let minutes = try reopened.minutes(since: base)
        #expect(minutes.map(\.start) == [base, base + 60])
        #expect(minutes[0].rows == [.init(target: "cloudflare", sent: 60, lost: 0, jitter: nil),
                                    .init(target: "router", sent: 60, lost: 1, jitter: 1.5)])
        #expect(try reopened.minutes(since: base + 1).map(\.start) == [base + 60])
    }

    @Test func aPartialMinuteWrittenTwiceIsMerged() throws {
        defer { try? FileManager.default.removeItem(at: url) }
        let store = try MinuteStore(url: url)
        try store.insert(minute(base, [.init(target: "router", sent: 20, lost: 1, jitter: 1, avg: 10, max: 30)]))
        try store.insert(minute(base, [.init(target: "router", sent: 40, lost: 2, jitter: nil, avg: 20, max: 25)]))
        // avg weighted by replies: (10 × 19 + 20 × 38) / 57 = 16.67 -> 16.7; the higher max wins
        #expect(try store.minutes(since: base)[0].rows == [.init(target: "router", sent: 60, lost: 3, jitter: 1, avg: 16.7, max: 30)])
    }

    @Test func rowsOlderThan30DaysArePurged() throws {
        defer { try? FileManager.default.removeItem(at: url) }
        let store = try MinuteStore(url: url)
        let now = Double(base)
        try store.insert(minute(base - 31 * 86400, [.init(target: "router", sent: 60, lost: 0)]))
        try store.insert(minute(base - 29 * 86400, [.init(target: "router", sent: 60, lost: 0)]))
        #expect(try store.purge(now: now) == 1)
        #expect(try store.minutes(since: 0).count == 1)
    }

    @MainActor @Test func aNewMonitorStartsFromTheStoredHour() throws {
        defer { try? FileManager.default.removeItem(at: url) }
        let clock = LiveMonitorTests.FakeClock()
        let first = LiveMonitor(store: try MinuteStore(url: url), now: { clock.now })
        for _ in 0..<60 {
            first.record([("router", 3), ("cloudflare", 20), ("google", 21)])
            clock.advance(1)
        }
        first.record([])   // closes the minute, which is saved
        clock.advance(30)
        let second = LiveMonitor(store: try MinuteStore(url: url), now: { clock.now })
        #expect(second.minutes.count == 1)
        #expect(second.pingQuality.status == .ok)
    }
}

struct VPNReaderTests {
    @Test func onlyTunnelsCarryingTrafficCount() {
        #expect(VPNReader.isTunnel("utun4") && VPNReader.isTunnel("ipsec0") && VPNReader.isTunnel("ppp0"))
        #expect(!VPNReader.isTunnel("en0") && !VPNReader.isTunnel("awdl0") && !VPNReader.isTunnel("bridge0"))
        let adapters = VPNReader.adapters(pathInterfaces: ["utun7", "en0"])
        #expect(adapters.contains(VPNCheck.Adapter(name: "utun7", status: "Up", isTunnel: true)))
        #expect(!adapters.contains { $0.name == "en0" })
    }

    @Test func anAppleTunnelIsAVPNWhateverItsName() {
        let result = VPNCheck.evaluate([VPNCheck.Adapter(name: "utun7", status: "Up", isTunnel: true)])
        #expect(result.status == .info)
        #expect(result.summary == Message("diag.vpn.up", ["count": 1]))
        #expect(result.details == [.text("utun7: Up")])
        #expect(VPNCheck.evaluate([]).summary == Message("diag.vpn.none"))
    }
}

struct PausedRoundTests {
    @Test func aRoundLongerThanItsTimeoutIsDropped() {
        // As seen on an iPhone after a moment in the background: one reply "took" seconds.
        #expect(LiveMonitor.ranOnTime(.milliseconds(905), timeout: .milliseconds(900)))
        #expect(LiveMonitor.ranOnTime(.milliseconds(1400), timeout: .milliseconds(900)))
        #expect(!LiveMonitor.ranOnTime(.seconds(4), timeout: .milliseconds(900)))
        #expect(!LiveMonitor.ranOnTime(.seconds(12), timeout: .seconds(3)))
    }
    @MainActor @Test func roundsRightAfterAResumeDoNotCount() {
        // As seen on an iPhone: the first TCP probes after the app came back failed at once.
        let monitor = LiveMonitor()
        #expect(monitor.isSettled())
        monitor.resumed()
        #expect(!monitor.isSettled())
        #expect(monitor.isSettled(at: .now + LiveMonitor.settleTime))
    }
}

struct CellularRouterTests {
    @Test func mobileDataHasNoRouterToMeasure() {
        // As seen on an iPhone over 5G: the path's gateway is the carrier's and never answers.
        let cellular = NetworkPath(status: .connected, link: .cellular, gateways: ["198.51.100.10"], interfaces: ["pdp_ip0"])
        #expect(cellular.routers.isEmpty && cellular.routerIPv4 == nil)
        let wifi = NetworkPath(status: .connected, link: .wifi, gateways: ["fe80::1", "192.0.2.1"], interfaces: ["en0"])
        #expect(wifi.routerIPv4 == "192.0.2.1")
        let hotspot = NetworkPath(status: .connected, link: .wifi, isExpensive: true, gateways: ["172.20.10.1"], interfaces: ["en0"])
        #expect(hotspot.routerIPv4 == "172.20.10.1")   // a phone's hotspot is a router on the local network
    }
}

@MainActor
struct NetworkChangeTests {
    @Test func liveSamplesRestartWhenTheRouteChangesButHistoryStays() {
        let clock = LiveMonitorTests.FakeClock()
        let monitor = LiveMonitor(now: { clock.now })
        let wifi = NetworkPath(status: .connected, link: .wifi, gateways: ["192.0.2.1"], interfaces: ["en0"])
        let vpn = NetworkPath(status: .connected, link: .other, gateways: ["192.0.2.1"], interfaces: ["utun5", "en0"])
        monitor.networkChanged(to: wifi)
        for _ in 0..<61 {
            monitor.record([("cloudflare", 20)])
            clock.advance(1)
        }
        #expect(monitor.minutes.count == 1)
        monitor.networkChanged(to: wifi)           // the same route: nothing happens
        #expect(monitor.samples["cloudflare"]?.count == 60)
        monitor.networkChanged(to: vpn)            // a VPN takes the traffic
        #expect(monitor.samples.isEmpty)
        #expect(monitor.minutes.count == 1)
        #expect(wifi.route != vpn.route)
    }

    @Test func check5LeavesOutTheMinutesAroundAChangeAlsoAfterARelaunch() throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "steady-test-\(UUID().uuidString).sqlite")
        defer { try? FileManager.default.removeItem(at: url) }
        let clock = LiveMonitorTests.FakeClock()   // starts on a minute
        let monitor = LiveMonitor(store: try MinuteStore(url: url), now: { clock.now })
        let wifi = NetworkPath(status: .connected, link: .wifi, gateways: ["192.0.2.1"], interfaces: ["en0"])
        let vpn = NetworkPath(status: .connected, link: .other, gateways: ["192.0.2.1"], interfaces: ["utun5", "en0"])
        func minute(lost: Int) {
            for second in 0..<60 {
                monitor.record([("cloudflare", second < lost ? nil : 20)])
                clock.advance(1)
            }
        }
        monitor.networkChanged(to: wifi)
        minute(lost: 0)
        minute(lost: 0)
        monitor.networkChanged(to: vpn)   // at the start of minute 2, which loses half its pings
        minute(lost: 30)
        for _ in 0..<3 { minute(lost: 0) }
        monitor.record([])                      // closes minute 5
        let leftOut = MessageValue.message(Message("diag.ping.left_out", ["count": .number(2)]))
        #expect(monitor.minutes.count == 6)
        #expect(monitor.pingQuality.status == .ok)   // 30 of 360 lost would be bad
        #expect(monitor.pingQuality.details.last == leftOut)

        let relaunched = LiveMonitor(store: try MinuteStore(url: url), now: { clock.now })
        #expect(relaunched.networkChanges == [1_790_000_040 + 120])
        #expect(relaunched.pingQuality.details.last == leftOut)
        try relaunched.deleteHistory()
        #expect(relaunched.networkChanges.isEmpty)
        #expect(try MinuteStore(url: url).networkChanges(since: 0).isEmpty)
    }

    @Test func losingTheConnectionIsAnOutageNotASwitch() {
        let clock = LiveMonitorTests.FakeClock()
        let monitor = LiveMonitor(now: { clock.now })
        let home = NetworkPath(status: .connected, link: .wifi, gateways: ["192.0.2.1"], interfaces: ["en0"])
        let lost = NetworkPath(status: .disconnected)
        let other = NetworkPath(status: .connected, link: .wifi, gateways: ["198.51.100.1"], interfaces: ["en0"])
        monitor.networkChanged(to: home)
        monitor.record([("cloudflare", 20)])
        monitor.networkChanged(to: lost)
        #expect(monitor.samples.isEmpty)               // the live numbers restart
        monitor.networkChanged(to: home)
        #expect(monitor.networkChanges.isEmpty)        // the same route back: the outage still counts
        clock.advance(10)
        monitor.networkChanged(to: lost)
        monitor.networkChanged(to: other)              // back on another network: a switch
        #expect(monitor.networkChanges == [1_790_000_040 + 10])
    }
}

@MainActor
struct LocalNetworkRefusalTests {
    @Test func aRefusedRouterIsLeftOutNotCountedAsLost() {
        let monitor = LiveMonitor()
        monitor.routerAddress = "192.0.2.1"
        monitor.record(refusals: [("router", .refused), ("cloudflare", .reply(20)), ("google", .noReply)])
        #expect(monitor.routerRefused)
        #expect(monitor.samples["router"] == nil)
        #expect(monitor.samples["google"] == [nil])
        #expect(!monitor.targets.contains { $0.id == "router" })
        monitor.record(refusals: [("router", .reply(3)), ("cloudflare", .reply(20))])   // permission granted
        #expect(!monitor.routerRefused)
        #expect(monitor.samples["router"] == [3])
        #expect(monitor.targets.first?.id == "router")
    }

    @Test func theNoteNamesTheVPNWhenATunnelIsUp() {
        let monitor = LiveMonitor()
        #expect(monitor.refusalNote(vpnUp: true) == nil)   // nothing refused, nothing to say
        monitor.record(refusals: [("router", .refused)])
        #expect(monitor.refusalNote(vpnUp: false) == Message("ui.path.reason.local_network_denied"))
        #expect(monitor.refusalNote(vpnUp: true) == Message("ui.path.reason.vpn_blocks_router"))
    }

    @Test func refusalErrorsAreRecognised() {
        #expect(ICMPPing.isRefusal(EHOSTUNREACH) && ICMPPing.isRefusal(EPERM))
        #expect(!ICMPPing.isRefusal(ETIMEDOUT))
    }
}

struct MinuteStoreFailureTests {
    @Test func aDatabaseThatCannotOpenThrowsWithoutCrashing() {
        let missing = URL(filePath: "/nonexistent-\(UUID().uuidString)/metrics.sqlite")
        #expect(throws: MinuteStore.StoreError.self) { try MinuteStore(url: missing) }
    }
}

struct VPNPreferredInterfaceTests {
    @Test func onlyThePreferredTunnelCounts() {
        #expect(!VPNReader.adapters(pathInterfaces: ["en0", "utun3"]).contains { $0.name == "utun3" })
        #expect(VPNReader.adapters(pathInterfaces: ["utun3", "en0"]).contains { $0.name == "utun3" })
    }
}

struct HistorySeriesTests {
    let base = 1_790_000_040   // a minute boundary, also a 10-minute and an hour boundary? no: only a minute

    func minute(_ start: Int, router: (Int, Int, Double?), internet: [(String, Int, Int, Double?)]) -> MinuteAggregator.Minute {
        MinuteAggregator.Minute(start: start, rows: [PingQuality.Row(target: "router", sent: router.0, lost: router.1, avg: router.2)]
            + internet.map { PingQuality.Row(target: $0.0, sent: $0.1, lost: $0.2, avg: $0.3) }
            + [PingQuality.Row(target: "tcp_cloudflare", sent: 6, lost: 6)])   // TCP probes are not drawn
    }

    @Test func internetCombinesBothPingTargetsWeightedByReplies() {
        let minutes = [minute(base, router: (60, 0, 3), internet: [("cloudflare", 60, 0, 40), ("google", 60, 30, 70)])]
        let points = HistorySeries.points(minutes, range: .hour, now: Date(timeIntervalSince1970: TimeInterval(base + 30)))
        let internet = points.first { $0.line == .internet }!
        #expect(abs(internet.latency! - 50) < 1e-9)          // (40 × 60 + 70 × 30) / 90
        #expect(abs(internet.lossPercent - 25) < 1e-9)       // 30 of 120
        #expect(points.first { $0.line == .router }?.latency == 3)
    }

    @Test func gapsStartANewSegmentAndOldMinutesAreLeftOut() {
        let now = Date(timeIntervalSince1970: TimeInterval(base + 600))
        let minutes = [minute(base - 7200, router: (60, 0, 3), internet: []),        // outside the hour
                       minute(base, router: (60, 0, 3), internet: []),
                       minute(base + 60, router: (60, 0, 4), internet: []),
                       minute(base + 300, router: (60, 60, nil), internet: [])]      // after a gap, nothing answered
        let router = HistorySeries.points(minutes, range: .hour, now: now).filter { $0.line == .router }
        #expect(router.map(\.segment) == [0, 0, 1])
        #expect(router.last?.latency == nil && router.last?.lossPercent == 100)
    }
}

struct ProblemReportTests {
    @Test func addressesAreMaskedButPublicResolversStay() {
        let text = "router 192.168.1.1 dns 203.0.113.53 via fe80::1%en0 and 2001:db8::53; public 1.1.1.1, 9.9.9.9; at 10:15:17Z"
        let redacted = ProblemReport.redact(text)
        #expect(!redacted.contains("192.168") && !redacted.contains("203.0.113") && !redacted.contains("fe80") && !redacted.contains("2001:db8"))
        #expect(redacted.contains("1.1.1.1") && redacted.contains("9.9.9.9") && redacted.contains("10:15:17Z"))
    }

    @Test func theReportHasTheChecksTheDescriptionAndNoDetails() {
        let dns = CheckResult(id: 6, key: "dns", status: .warn, summary: Message("diag.dns.broken", ["servers": .list([.text("192.0.2.1")])]),
                              details: [.text("secret detail 192.0.2.1")], advice: .message(Message("diag.dns.advice_broken")))
        let context = ProblemReport.Context(appVersion: "0.1.0", build: "1", system: "iPadOS 27.0.1", device: "iPad14,2",
                                            language: "en", connection: "wifi", checks: [dns],
                                            minutes: [MinuteAggregator.Minute(start: 1_790_000_040, rows: [PingQuality.Row(target: "router", sent: 60, lost: 1, avg: 4.2)])],
                                            log: ["10:00:00Z [measurement] minute 1790000040 router: sent 60, lost 1"], crashes: [],
                                            description: "  Slow at night  ")
        let text = ProblemReport.text(context, renderer: Localizer(bundle: .main, language: "en"))
        #expect(text.hasPrefix("## Description\nSlow at night\n"))
        #expect(text.contains("#6 dns: warn - diag.dns.broken"))   // no bundle in tests: keys render as themselves
        #expect(text.contains("router 60/1 4.2"))
        #expect(!text.contains("secret detail") && !text.contains("192.0.2.1"))
    }
}

struct MinuteStoreDeleteTests {
    let url = FileManager.default.temporaryDirectory.appending(path: "steady-test-\(UUID().uuidString).sqlite")

    @MainActor @Test func deleteHistoryEmptiesStoreAndMemory() throws {
        defer { try? FileManager.default.removeItem(at: url) }
        let store = try MinuteStore(url: url)
        try store.insert(MinuteAggregator.Minute(start: 1_790_000_040, rows: [.init(target: "router", sent: 60, lost: 0)]))
        try store.insert(MinuteAggregator.Minute(start: 1_790_000_100, rows: [.init(target: "router", sent: 60, lost: 0),
                                                                             .init(target: "cloudflare", sent: 60, lost: 0)]))
        #expect(try store.minuteCount() == 2)
        let monitor = LiveMonitor(store: store, now: { 1_790_000_200 })
        #expect(monitor.history(seconds: 3600).count == 2)
        try monitor.deleteHistory()
        #expect(monitor.storedMinutes == 0 && monitor.minutes.isEmpty)
    }
}
