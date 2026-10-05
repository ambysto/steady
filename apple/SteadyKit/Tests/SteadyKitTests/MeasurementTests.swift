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
        #expect(rows == [PingQuality.Row(target: "router", sent: 4, lost: 1, jitter: 15)])
    }

    @Test func allLostAndSingleSampleHaveNoJitter() {
        var aggregator = MinuteAggregator()
        _ = aggregator.roll(at: base)
        aggregator.add("dead", rttMs: nil)
        aggregator.add("dead", rttMs: nil)
        aggregator.add("one", rttMs: 7)
        let rows = aggregator.roll(at: base + 60)?.rows
        #expect(rows == [PingQuality.Row(target: "dead", sent: 2, lost: 2, jitter: nil),
                         PingQuality.Row(target: "one", sent: 1, lost: 0, jitter: nil)])
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
