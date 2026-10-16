import Foundation

/// The floating monitor's chart, in the style of a heart monitor: the trace is written left to
/// right, wraps around and overwrites itself, a short blank gap runs ahead of the pen, and older
/// trace fades. Port of `web/js/ecg.js` (`sweepLayout`, `niceScale`): the same numbers on every
/// platform. The drawing itself is in the app; this only works out where things go.
public enum Sweep {
    /// One pass across the chart.
    public static let sweepSeconds = 60.0
    /// Blank space ahead of the pen.
    public static let gapSeconds = 3.0
    /// A longer pause between two samples breaks the line (no data).
    public static let maxStepSeconds = 2.5
    /// Room kept above the highest value.
    public static let padTop = 6.0

    public static let mbpsSteps: [Double] = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
    public static let msSteps: [Double] = [25, 50, 100, 150, 250, 500, 1000, 2000, 5000]

    /// A sample: `value` is nil where it was lost.
    public struct Sample: Equatable, Sendable {
        public let ts: Double
        public let value: Double?

        public init(ts: Double, value: Double?) {
            self.ts = ts
            self.value = value
        }
    }

    public struct Point: Equatable, Sendable {
        public let x: Double
        public let y: Double
        /// 0 for the newest trace, 1 for the oldest; drawn fainter as it ages.
        public let age: Double
    }

    /// A lost sample, marked along the bottom edge.
    public struct Mark: Equatable, Sendable {
        public let x: Double
        public let age: Double
    }

    public struct Layout: Equatable, Sendable {
        public var lines: [[Point]] = []
        public var lost: [Mark] = []
        public var pen: Point?
    }

    /// Smallest step at or above the largest value plus headroom; the last step when none fits.
    public static func niceScale(_ values: [Double], steps: [Double]) -> Double {
        let top = max(0, values.filter(\.isFinite).max() ?? 0)
        return steps.first { $0 >= top * 1.15 } ?? steps[steps.count - 1]
    }

    /// The samples still on the chart with the pen at `now`, which the scale is worked out from:
    /// a spike that has scrolled off no longer flattens the trace. Unlike Windows, which scales
    /// over its whole two-minute history.
    public static func shown(_ samples: [Sample], now: Double) -> [Sample] {
        samples.filter { $0.ts >= now - (sweepSeconds - gapSeconds) }
    }

    /// Lays out the samples for a chart `width` x `height` points, the pen at time `now`.
    /// Samples after `now` are not drawn yet, except that the segment toward the next one is drawn
    /// up to `now`, so the trace grows smoothly between one-second samples.
    public static func layout(_ samples: [Sample], now: Double, width: Double, height: Double, ceiling: Double) -> Layout {
        let oldest = now - (sweepSeconds - gapSeconds)
        var visible: [Sample] = []
        for sample in samples {
            if sample.ts > now {
                // The head: interpolate between the last drawn sample and this one.
                if let last = visible.last, let lastValue = last.value, let value = sample.value,
                   sample.ts - last.ts <= maxStepSeconds {
                    let fraction = (now - last.ts) / (sample.ts - last.ts)
                    visible.append(Sample(ts: now, value: lastValue + (value - lastValue) * fraction))
                }
                break
            }
            if sample.ts > oldest {
                visible.append(sample)
            }
        }

        func xOf(_ ts: Double) -> Double {
            let phase = ((ts.truncatingRemainder(dividingBy: sweepSeconds)) + sweepSeconds).truncatingRemainder(dividingBy: sweepSeconds)
            return phase / sweepSeconds * width
        }
        func yOf(_ value: Double) -> Double {
            height - (min(value, ceiling) / ceiling) * (height - padTop)
        }

        var layout = Layout()
        var inLine = false
        var previous: Double?
        for sample in visible {
            let age = min(1, max(0, (now - sample.ts) / (sweepSeconds - gapSeconds)))
            guard let value = sample.value else {
                layout.lost.append(Mark(x: xOf(sample.ts), age: age))
                inLine = false
                previous = nil
                continue
            }
            let point = Point(x: xOf(sample.ts), y: yOf(value), age: age)
            // A new line after a lost sample, a long pause, or the trace wrapping round the chart.
            if !inLine || sample.ts - (previous ?? sample.ts) > maxStepSeconds
                || point.x < (layout.lines.last?.last?.x ?? point.x) {
                layout.lines.append([])
                inLine = true
            }
            layout.lines[layout.lines.count - 1].append(point)
            previous = sample.ts
            layout.pen = point
        }
        return layout
    }
}
