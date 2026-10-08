import Testing
@testable import SteadyKit

/// The floating monitor's chart layout, the port of web/js/ecg.js (tests/js/ checks the same cases).
struct SweepTests {
    static let width = 600.0, height = 100.0

    static func layout(_ samples: [(Double, Double?)], now: Double, ceiling: Double = 100) -> Sweep.Layout {
        Sweep.layout(samples.map { Sweep.Sample(ts: $0.0, value: $0.1) }, now: now, width: width, height: height, ceiling: ceiling)
    }

    @Test func niceScaleIsTheSmallestStepWithHeadroom() {
        #expect(Sweep.niceScale([3], steps: Sweep.msSteps) == 25)         // 3 * 1.15 = 3.45, the first step is 25
        #expect(Sweep.niceScale([25], steps: Sweep.msSteps) == 50)        // 25 * 1.15 = 28.75
        #expect(Sweep.niceScale([], steps: Sweep.mbpsSteps) == 0.5)       // nothing yet: the first step
        #expect(Sweep.niceScale([20_000], steps: Sweep.mbpsSteps) == 10_000)   // above every step: the last
        #expect(Sweep.niceScale([.nan, 4], steps: Sweep.mbpsSteps) == 5)  // a NaN is ignored
    }

    @Test func scaleLeavesOutSamplesThatScrolledOff() {
        // The chart shows 60 - 3 = 57 s: at now = 100 the oldest shown sample is at 43 s.
        let samples = [Sweep.Sample(ts: 42, value: 9), Sweep.Sample(ts: 43, value: 0.1), Sweep.Sample(ts: 99, value: 0.2)]
        let shown = Sweep.shown(samples, now: 100)
        #expect(shown.map(\.ts) == [43, 99])
        #expect(Sweep.niceScale(shown.compactMap(\.value), steps: Sweep.mbpsSteps) == 0.5)
    }

    @Test func traceWrapsRoundTheChartAsItWritesAgain() {
        // x = ts / 60 * width, so 60 s is back at the left edge: a new line starts there.
        let layout = Self.layout([(58, 10), (59, 10), (60, 10)], now: 60.5)
        #expect(layout.lines.count == 2)
        #expect(layout.lines[0].map(\.x) == [58 / 60 * Self.width, 59 / 60 * Self.width])
        #expect(layout.lines[1].map(\.x) == [0])
    }

    @Test func aLostSampleIsAMarkAndBreaksTheLine() {
        let layout = Self.layout([(10, 1), (11, nil), (12, 2)], now: 12.5)
        #expect(layout.lines.count == 2)
        #expect(layout.lost.count == 1)
        #expect(layout.lost[0].x == 11 / 60 * Self.width)
    }

    @Test func aLongPauseBreaksTheLine() {
        let layout = Self.layout([(10, 1), (13.5, 2)], now: 14)   // 3.5 s apart, more than 2.5 s
        #expect(layout.lines.count == 2)
    }

    @Test func theHeadIsInterpolatedUpToThePen() {
        // The next sample (t = 11, value 20) is not drawn yet at t = 10.5: the pen sits halfway.
        let layout = Self.layout([(10, 10), (11, 20)], now: 10.5)
        let pen = layout.pen
        #expect(pen?.x == 10.5 / 60 * Self.width)
        #expect(pen?.y == Self.height - (15 / 100) * (Self.height - Sweep.padTop))
    }

    @Test func olderTraceFadesAndLeavesOutAfterAMinute() {
        let layout = Self.layout([(0, 5), (30, 5)], now: 60)
        #expect(layout.lines.count == 1)                  // t = 0 is older than the 57 s shown
        #expect(layout.lines[0].count == 1)
        #expect(abs(layout.lines[0][0].age - 30.0 / 57.0) < 1e-9)
    }
}
