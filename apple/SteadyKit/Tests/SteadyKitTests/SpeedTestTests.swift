import Foundation
import Testing
@testable import SteadyKit

struct LiveRateTests {
    @Test func theFirstReadingHasNoRate() {
        var rate = LiveRate()
        #expect(rate.add(seconds: 0, bytes: 0) == nil)
        #expect(rate.add(seconds: 0, bytes: 10) == nil)   // no time has passed
    }

    @Test func theRateCoversAboutTheLastSecond() {
        var rate = LiveRate()
        _ = rate.add(seconds: 0, bytes: 0)
        // 12.5 MB/s = 100 Mbit/s for the first second, then 25 MB/s = 200 Mbit/s.
        #expect(rate.add(seconds: 0.5, bytes: 6_250_000) == 100)
        _ = rate.add(seconds: 1.0, bytes: 12_500_000)
        _ = rate.add(seconds: 1.5, bytes: 25_000_000)
        #expect(rate.add(seconds: 2.0, bytes: 37_500_000) == 200)
    }

    @Test func aTotalThatGoesBackReadsAsZero() {
        var rate = LiveRate()
        _ = rate.add(seconds: 0, bytes: 1000)
        #expect(rate.add(seconds: 1, bytes: 0) == 0)
    }
}

struct SpeedTestTests {
    let date = Date(timeIntervalSince1970: 1_800_000_000)

    func phase(_ internet: [Double?], mbps: Double? = nil, capped: Bool? = nil, refused: Int? = nil,
               retryAfterS: Double? = nil) -> Bufferbloat.Phase {
        Bufferbloat.Phase(rtts: [.init(label: "router", samples: [1, 1, 1]), .init(label: "internet", samples: internet)],
                          mbps: mbps, capped: capped, refused: refused, retryAfterS: retryAfterS)
    }

    @Test func theSteadyRateIsWhatMovedAfterTheRamp() {
        // 25 MB in the 8 s after a reading at 2 s: 25 Mbit/s.
        #expect(BufferbloatTest.steadyMbps(atRamp: (2, 5_000_000), bytes: 30_000_000, seconds: 10) == 25)
        #expect(BufferbloatTest.steadyMbps(atRamp: nil, bytes: 30_000_000, seconds: 10) == nil)
        #expect(BufferbloatTest.steadyMbps(atRamp: (2, 5_000_000), bytes: 6_000_000, seconds: 2.3) == nil)
        #expect(BufferbloatTest.steadyMbps(atRamp: (2, 5_000_000), bytes: 5_000_000, seconds: 10) == nil)
    }

    @Test func jitterIsTheMeanStepBetweenReplies() {
        #expect(SpeedTest.jitter([10, 12, nil, 11, 15]) == 7.0 / 3)   // |2| + |-1| + |4| over 3 steps
        #expect(SpeedTest.jitter([10, nil]) == nil)
        #expect(SpeedTest.loss([10, nil, 12, nil]) == 50)
        #expect(SpeedTest.loss([]) == nil)
        #expect(SpeedTest.median([nil, nil]) == nil)
    }

    @Test func aFullRunGivesEveryFigure() {
        let series = [SpeedTest.Point(seconds: 0.25, mbps: 100), SpeedTest.Point(seconds: 0.5, mbps: 200)]
        let run = BufferbloatTest.Run(
            measurement: .init(idle: phase([10, 12, 11, nil]),
                               download: phase([30, 40, 50], mbps: 180),
                               upload: phase([60, 70, 80, nil], mbps: 20)),
            downloadSteadyMbps: 210, uploadSteadyMbps: 22, downloadSeries: series, uploadSeries: series, server: "HKG")
        let result = SpeedTest.result(from: run, at: date)
        #expect(result.downloadMbps == 210)
        #expect(result.uploadMbps == 22)
        #expect(result.idle == SpeedTest.Latency(medianMs: 11, jitterMs: 1.5))
        #expect(result.duringDownload == SpeedTest.Latency(medianMs: 40, jitterMs: 10))
        #expect(result.duringUpload?.medianMs == 70)
        #expect(result.lossPercent == 200.0 / 11)   // 2 of the 11 Internet pings
        #expect(result.downloadSeries == series)
        #expect(result.server == "HKG")
        #expect(result.hasFigures)
    }

    @Test func withoutASteadyRateThePhaseAverageIsUsed() {
        let run = BufferbloatTest.Run(measurement: .init(idle: phase([10]), download: phase([20], mbps: 800, capped: true),
                                                         upload: phase([20], mbps: 5)))
        let result = SpeedTest.result(from: run, at: date)
        #expect(result.downloadMbps == 800)
        #expect(result.downloadCapped)
        #expect(result.uploadMbps == 5)
        #expect(!result.uploadCapped)
    }

    @Test func aRefusedDirectionHasNoFigureAndSaysWhenToComeBack() {
        let run = BufferbloatTest.Run(
            measurement: .init(idle: phase([10]), download: phase([30], mbps: 100),
                               upload: phase([500, nil], mbps: 3, refused: 429, retryAfterS: 120)),
            downloadSteadyMbps: 110, uploadSteadyMbps: 4,
            uploadSeries: [SpeedTest.Point(seconds: 1, mbps: 3)])
        let result = SpeedTest.result(from: run, at: date)
        #expect(result.downloadMbps == 110)
        #expect(result.uploadMbps == nil)
        #expect(result.duringUpload == nil)   // the refused direction's pings are not a loaded line's
        #expect(result.uploadSeries == nil)
        #expect(result.lossPercent == 0)
        #expect(result.refusedStatus == 429)
        #expect(result.retryUntil == date.addingTimeInterval(120))
    }

    @Test func aRunThatMovedNothingHasNoFigures() {
        let run = BufferbloatTest.Run(measurement: .init(idle: phase([nil, nil]), download: phase([nil], mbps: 0),
                                                         upload: phase([nil], mbps: 0)))
        let result = SpeedTest.result(from: run, at: date)
        #expect(!result.hasFigures)
        #expect(result.idle?.medianMs == nil)
        #expect(result.lossPercent == 100)
        #expect(result.refusedStatus == nil)
    }

    @Test func aLongSeriesIsThinnedKeepingItsEnds() {
        let series = (0..<200).map { SpeedTest.Point(seconds: Double($0) / 20, mbps: Double($0)) }
        let thinned = SpeedTest.thinned(series)
        #expect(thinned.count == SpeedTest.maxPoints)
        #expect(thinned.first == series.first)
        #expect(thinned.last == series.last)
        #expect(SpeedTest.thinned(Array(series.prefix(10))) == Array(series.prefix(10)))
    }

    @Test func aResultSurvivesARelaunch() throws {
        let result = SpeedTest.Result(measuredAt: date, downloadMbps: 245.5, downloadCapped: true,
                                      idle: .init(medianMs: 12, jitterMs: 1), lossPercent: 0,
                                      downloadSeries: [.init(seconds: 1, mbps: 2)], server: "HKG", refusedStatus: 403)
        let decoded = try JSONDecoder().decode(SpeedTest.Result.self, from: JSONEncoder().encode(result))
        #expect(decoded == result)
        #expect((try? JSONDecoder().decode(SpeedTest.Result.self, from: Data("{\"old\":1}".utf8))) == nil)
    }

    @Test func aResultSavedBeforeTheChartsStillDecodes() throws {
        let saved = #"{"measuredAt":800000000,"downloadMbps":763,"uploadMbps":705,"downloadCapped":false,"uploadCapped":false,"idlePingMs":23,"loadedPingMs":68}"#
        let result = try JSONDecoder().decode(SpeedTest.Result.self, from: Data(saved.utf8))
        #expect(result.downloadMbps == 763)
        #expect(result.downloadSeries == nil)
    }

    @Test func confirmationIsAskedOnceExceptOnAMeteredPath() {
        #expect(SpeedTest.needsConfirmation(metered: false, confirmedBefore: false))
        #expect(!SpeedTest.needsConfirmation(metered: false, confirmedBefore: true))
        #expect(SpeedTest.needsConfirmation(metered: true, confirmedBefore: true))
    }
}

struct SpeedTestLiveTests {
    @Test func theLiveViewFollowsTheEvents() {
        var live = SpeedTest.Live()
        live.apply(.stage(.idle))
        live.apply(.ping(.idle, seconds: 0, ms: 20))
        live.apply(.ping(.idle, seconds: 0.2, ms: 24))
        #expect(live.latency(.idle) == SpeedTest.Latency(medianMs: 22, jitterMs: 4))
        #expect(live.mbps(.download) == nil)
        #expect(live.fraction == 0.2 / BufferbloatTest.duration)

        live.apply(.stage(.download))
        live.apply(.rate(.download, seconds: 1, mbps: 100))
        live.apply(.ping(.download, seconds: 1, ms: 300))   // still in the ramp
        live.apply(.rate(.download, seconds: 3, mbps: 500))
        #expect(live.mbps(.download) == 500)   // the latest while it loads
        #expect(live.latency(.download)?.medianMs == 300)   // nothing after the ramp yet: all of it
        live.apply(.ping(.download, seconds: 3, ms: 60))
        live.apply(.ping(.download, seconds: 3.2, ms: nil))
        #expect(live.latency(.download)?.medianMs == 60)   // after the ramp only
        #expect(live.lossPercent == 25)   // 1 of 4 replies kept (2 idle, 2 after the ramp)
        #expect(live.fraction == (4 + 3.2) / BufferbloatTest.duration)

        live.apply(.stage(.upload))
        live.apply(.rate(.download, seconds: 9, mbps: 700))
        #expect(live.mbps(.download) == 600)   // ended: the mean after the ramp
        #expect(live.mbps(.upload) == nil)
        #expect(live.downloadSeries.count == 3)
    }
}
