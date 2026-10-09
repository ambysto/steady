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

    @Test func aFullRunGivesFourFigures() {
        let run = BufferbloatTest.Run(
            measurement: .init(idle: phase([10, 12, 11, nil]),
                               download: phase([30, 40, 50], mbps: 180),
                               upload: phase([60, 70, 80, nil], mbps: 20)),
            downloadSteadyMbps: 210, uploadSteadyMbps: 22)
        let result = SpeedTest.result(from: run, at: date)
        #expect(result == SpeedTest.Result(measuredAt: date, downloadMbps: 210, uploadMbps: 22,
                                           idlePingMs: 11, loadedPingMs: 70))
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
                               upload: phase([500], mbps: 3, refused: 429, retryAfterS: 120)),
            downloadSteadyMbps: 110, uploadSteadyMbps: 4)
        let result = SpeedTest.result(from: run, at: date)
        #expect(result.downloadMbps == 110)
        #expect(result.uploadMbps == nil)
        #expect(result.loadedPingMs == 30)   // the refused direction's pings are not a loaded line's
        #expect(result.refusedStatus == 429)
        #expect(result.retryUntil == date.addingTimeInterval(120))
    }

    @Test func aRunThatMovedNothingHasNoFigures() {
        let run = BufferbloatTest.Run(measurement: .init(idle: phase([nil, nil]), download: phase([nil], mbps: 0),
                                                         upload: phase([nil], mbps: 0)))
        let result = SpeedTest.result(from: run, at: date)
        #expect(!result.hasFigures)
        #expect(result.idlePingMs == nil)
        #expect(result.loadedPingMs == nil)
        #expect(result.refusedStatus == nil)
    }

    @Test func aResultSurvivesARelaunch() throws {
        let result = SpeedTest.Result(measuredAt: date, downloadMbps: 245.5, uploadMbps: nil, downloadCapped: true,
                                      idlePingMs: 12, refusedStatus: 403)
        let decoded = try JSONDecoder().decode(SpeedTest.Result.self, from: JSONEncoder().encode(result))
        #expect(decoded == result)
        #expect((try? JSONDecoder().decode(SpeedTest.Result.self, from: Data("{\"old\":1}".utf8))) == nil)
    }

    @Test func confirmationIsAskedOnceExceptOnAMeteredPath() {
        #expect(SpeedTest.needsConfirmation(metered: false, confirmedBefore: false))
        #expect(!SpeedTest.needsConfirmation(metered: false, confirmedBefore: true))
        #expect(SpeedTest.needsConfirmation(metered: true, confirmedBefore: true))
    }
}
