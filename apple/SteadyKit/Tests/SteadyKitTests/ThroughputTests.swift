import Testing
@testable import SteadyKit

/// The download and upload rates the floating monitor draws (Network/Throughput.swift).
struct ThroughputTests {
    @Test func aRateNeedsTwoReadingsAndIsBitsPerSecond() {
        var throughput = Throughput()
        throughput.add(ts: 0, interface: "en0", rx: 1_000, tx: 500)
        #expect(throughput.samples.isEmpty)
        throughput.add(ts: 1, interface: "en0", rx: 2_000, tx: 1_500)
        #expect(throughput.samples == [Throughput.Sample(ts: 1, downBps: 8_000, upBps: 8_000)])
    }

    @Test func aRateOverTwoSecondsIsHalfOfTheBytes() {
        var throughput = Throughput()
        throughput.add(ts: 0, interface: "en0", rx: 0, tx: 0)
        throughput.add(ts: 2, interface: "en0", rx: 4_000, tx: 0)
        #expect(throughput.samples.first?.downBps == 16_000)
    }

    @Test func the32BitCountersWrapRoundWithoutAGlitch() {
        var throughput = Throughput()
        throughput.add(ts: 0, interface: "en0", rx: 0xFFFF_FF00, tx: 0)
        throughput.add(ts: 1, interface: "en0", rx: 0x0000_0100, tx: 0)   // 512 bytes went by
        #expect(throughput.samples.first?.downBps == 4_096)
    }

    @Test func aPauseLongerThanTwoAndAHalfSecondsGivesNoRate() {
        var throughput = Throughput()
        throughput.add(ts: 0, interface: "en0", rx: 0, tx: 0)
        throughput.add(ts: 10, interface: "en0", rx: 9_999_999, tx: 0)   // asleep meanwhile
        #expect(throughput.samples.isEmpty)
    }

    @Test func aCounterThatWentBackwardsIsNotTraffic() {
        var throughput = Throughput()
        throughput.add(ts: 0, interface: "en0", rx: 1_000_000_000, tx: 0)
        throughput.add(ts: 1, interface: "en0", rx: 0, tx: 0)   // wraps to about 3.3 GB in one second
        #expect(throughput.samples.isEmpty)
    }

    @Test func aNewInterfaceStartsAfresh() {
        var throughput = Throughput()
        throughput.add(ts: 0, interface: "en0", rx: 0, tx: 0)
        throughput.add(ts: 1, interface: "en0", rx: 100, tx: 0)
        throughput.add(ts: 2, interface: "utun3", rx: 0, tx: 0)
        #expect(throughput.samples.isEmpty)
    }

    @Test func onlyTheLastTwoMinutesAreKept() {
        var throughput = Throughput()
        for second in 0...200 {
            throughput.add(ts: Double(second), interface: "en0", rx: UInt32(second * 100), tx: 0)
        }
        #expect(throughput.samples.first?.ts == 80)   // 120 s before the newest reading (200)
        #expect(throughput.samples.last?.ts == 200)
    }

    @Test func theLoopbackInterfaceHasCountersAndItsAddress() {
        #expect(Throughput.counters(of: "lo0") != nil)
        #expect(Throughput.ipv4(of: "lo0") == "127.0.0.1")
        #expect(Throughput.counters(of: "no-such-interface") == nil)
    }
}
