import Foundation

/// The series the floating monitor draws and reads its numbers from (the Windows `liveTicks` and
/// `series()` in web/js/mini.js, on the Mac's own measurements).
public enum SweepSeries {
    /// Ping to the Internet: for each round, the quickest reply of the Internet targets; nil where
    /// every one of them lost it. The router is left out: it is not the Internet.
    public static func ping(_ timed: [String: [LiveMonitor.TimedSample]], targets: [String]) -> [Sweep.Sample] {
        var rounds: [Double: [Double?]] = [:]
        for target in targets {
            for sample in timed[target] ?? [] {
                rounds[sample.ts, default: []].append(sample.rttMs)
            }
        }
        return rounds.keys.sorted().map { ts in
            let replies = rounds[ts, default: []].compactMap { $0 }
            return Sweep.Sample(ts: ts, value: replies.min())
        }
    }

    /// Download and upload in Mbit/s, one sample per second the interface was read.
    public static func throughput(_ samples: [Throughput.Sample]) -> (down: [Sweep.Sample], up: [Sweep.Sample]) {
        (samples.map { Sweep.Sample(ts: $0.ts, value: $0.downBps / 1e6) },
         samples.map { Sweep.Sample(ts: $0.ts, value: $0.upBps / 1e6) })
    }

    /// The newest value that was not lost.
    public static func latest(_ samples: [Sweep.Sample]) -> Double? {
        samples.reversed().first { $0.value != nil }?.value
    }

    /// The last `count` samples were all lost: one lost ping is common (ISPs rate-limit ICMP), so
    /// the number says "Lost" only after a run of them.
    public static func lostInARow(_ samples: [Sweep.Sample], count: Int = 2) -> Bool {
        samples.count >= count && samples.suffix(count).allSatisfy { $0.value == nil }
    }
}
