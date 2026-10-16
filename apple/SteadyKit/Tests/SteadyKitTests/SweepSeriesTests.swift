import Foundation
import Testing
@testable import SteadyKit

/// What the floating monitor draws from the measurements (Chart/SweepSeries.swift).
struct SweepSeriesTests {
    static func timed(_ rounds: [(Double, Double?)]) -> [LiveMonitor.TimedSample] {
        rounds.map { LiveMonitor.TimedSample(ts: $0.0, rttMs: $0.1) }
    }

    @Test func pingIsTheQuickestInternetReplyOfEachRound() {
        let series = SweepSeries.ping([
            "cloudflare": Self.timed([(10, 20), (11, nil), (12, nil)]),
            "google": Self.timed([(10, 35), (11, 40), (12, nil)]),
            "router": Self.timed([(10, 1), (11, 1), (12, 1)]),
        ], targets: ["cloudflare", "google"])
        #expect(series == [Sweep.Sample(ts: 10, value: 20), Sweep.Sample(ts: 11, value: 40),
                           Sweep.Sample(ts: 12, value: nil)])   // the router is not the Internet
    }

    @Test func aRoundWithNoTargetYetGivesNothing() {
        #expect(SweepSeries.ping([:], targets: ["cloudflare", "google"]).isEmpty)
    }

    @Test func latestIsTheNewestValueThatArrived() {
        let samples = [Sweep.Sample(ts: 1, value: 12), Sweep.Sample(ts: 2, value: nil)]
        #expect(SweepSeries.latest(samples) == 12)
        #expect(SweepSeries.latest([]) == nil)
    }

    @Test func oneLostPingIsNotLostButTwoInARowAre() {
        let one = [Sweep.Sample(ts: 1, value: 12), Sweep.Sample(ts: 2, value: nil)]
        let two = one + [Sweep.Sample(ts: 3, value: nil)]
        #expect(!SweepSeries.lostInARow(one))
        #expect(SweepSeries.lostInARow(two))
    }

    @Test func throughputIsInMbps() {
        let rates = [Throughput.Sample(ts: 1, downBps: 8_000_000, upBps: 2_000_000)]
        let (down, up) = SweepSeries.throughput(rates)
        #expect(down == [Sweep.Sample(ts: 1, value: 8)])
        #expect(up == [Sweep.Sample(ts: 1, value: 2)])
    }
}
