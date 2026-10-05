import Foundation

/// Check #14 "Bufferbloat" (docs/DIAGNOSTICS.md), a port of `evaluate_bufferbloat` in
/// app/diagnostics.py. On demand only: measuring it moves up to ~200 MB.
/// spec/diagnosis/bufferbloat.json holds the cases both implementations must agree on.
public enum Bufferbloat {
    /// Ping samples to one target during a phase, in measuring order; nil = lost.
    public struct Samples: Equatable, Sendable, Decodable {
        public var label: String
        public var samples: [Double?]

        public init(label: String, samples: [Double?]) {
            self.label = label
            self.samples = samples
        }
    }

    /// One phase of the measurement (app/bufferbloat.py Phase).
    public struct Phase: Equatable, Sendable, Decodable {
        /// Per target, in the order the targets were given ("router" before "internet").
        public var rtts: [Samples]
        /// Load reached; nil while idle.
        public var mbps: Double?
        /// Why the load could not be generated, or "".
        public var error: String

        public init(rtts: [Samples], mbps: Double? = nil, error: String = "") {
            self.rtts = rtts
            self.mbps = mbps
            self.error = error
        }
    }

    public struct Measurement: Equatable, Sendable, Decodable {
        public var idle: Phase
        public var download: Phase
        public var upload: Phase

        public init(idle: Phase, download: Phase, upload: Phase) {
            self.idle = idle
            self.download = download
            self.upload = upload
        }
    }

    static let warnMs = 30.0, badMs = 100.0, badLossPercent = 20.0
    static let minMbps = 1.0     // below this the "load" did not really load the line
    static let minSamples = 8

    public static func evaluate(_ measurement: Measurement) -> CheckResult {
        var idleMedian: [(label: String, median: Double)] = []
        for target in measurement.idle.rtts {
            let got = target.samples.compactMap { $0 }
            if got.count >= minSamples / 2 {
                idleMedian.append((target.label, median(got)))
            }
        }
        func idle(_ label: String) -> Double? { idleMedian.first { $0.label == label }?.median }

        guard idle("internet") != nil else {
            return CheckResult(id: 14, key: "bufferbloat", status: .info, summary: Message("diag.bufferbloat.no_idle"),
                               advice: .message(Message("diag.bufferbloat.advice_retry")))
        }
        var details: [MessageValue] = [.message(Message("diag.bufferbloat.idle", [
            "values": .list(idleMedian.map { .text("\($0.label) \(String(format: "%.0f", $0.median)) ms") }),
        ]))]
        var statuses: [CheckStatus] = []
        var deltas: [(label: String, delta: Double, loss: Double)] = []
        var notes: [MessageValue] = []

        for (phase, direction) in [(measurement.download, "download"), (measurement.upload, "upload")] {
            let name = MessageValue.message(Message("diag.bufferbloat.\(direction)"))
            let mbps = phase.mbps ?? 0
            if !phase.error.isEmpty && mbps == 0 {
                notes.append(.message(Message("diag.bufferbloat.no_load", ["phase": name, "error": .text(phase.error)])))
                continue
            }
            if mbps < minMbps {
                notes.append(.message(Message("diag.bufferbloat.weak_load", ["phase": name, "mbps": .number(mbps)])))
                continue
            }
            for target in phase.rtts {
                guard let idleValue = idle(target.label), target.samples.count >= minSamples else { continue }
                let got = target.samples.compactMap { $0 }
                let loss = 100.0 * Double(target.samples.count - got.count) / Double(target.samples.count)
                let delta: Double
                if got.isEmpty {
                    delta = .infinity
                    details.append(.message(Message("diag.bufferbloat.all_lost", ["label": .text(target.label), "phase": name])))
                } else {
                    delta = median(got) - idleValue
                    details.append(.message(Message("diag.bufferbloat.line", [
                        "label": .text(target.label), "phase": name, "mbps": .number(mbps),
                        "median": .number(median(got)), "p95": .number(percentile95(got)),
                        "delta": .number(delta), "loss": .number(loss),
                    ])))
                }
                statuses.append(status(delta: delta, loss: loss, replies: got.count))
                deltas.append((target.label, delta, loss))
            }
        }
        details += notes
        guard !statuses.isEmpty else {
            return CheckResult(id: 14, key: "bufferbloat", status: .info, summary: Message("diag.bufferbloat.no_result"),
                               details: details, advice: .message(Message("diag.bufferbloat.advice_unreachable")))
        }

        let overall = CheckStatus.worst(statuses)
        let internet = deltas.filter { $0.label == "internet" }
        let router = deltas.filter { $0.label == "router" }
        let biggest = internet.map(\.delta).max() ?? 0
        let worstLoss = internet.map(\.loss).max() ?? 0
        let summary: Message
        if overall == .ok {
            summary = Message("diag.bufferbloat.ok", ["delta": .number(max(biggest, 0))])
        } else if biggest < warnMs {
            // Median latency is fine; what fails is packet loss under load (a full queue dropping pings).
            summary = Message("diag.bufferbloat.loss_only", ["loss": .number(worstLoss)])
        } else {
            let `where`: MessageValue = if let routerMax = router.map(\.delta).max(), routerMax >= warnMs {
                .message(Message("diag.bufferbloat.where_local"))
            } else {
                .message(Message("diag.bufferbloat.where_wan"))
            }
            let amount: MessageValue = biggest == .infinity
                ? .message(Message("diag.bufferbloat.unbounded"))
                : .message(Message("diag.bufferbloat.by", ["delta": .number(biggest)]))
            summary = Message("diag.bufferbloat.rise", ["amount": amount, "where": `where`])
        }
        let advice: MessageValue = overall == .ok ? .empty : .message(Message("diag.bufferbloat.advice"))
        return CheckResult(id: 14, key: "bufferbloat", status: overall, summary: summary, details: details, advice: advice)
    }

    static func status(delta: Double, loss: Double, replies: Int) -> CheckStatus {
        if replies == 0 || loss >= badLossPercent { return .bad }
        return delta > badMs ? .bad : delta >= warnMs ? .warn : .ok
    }

    /// statistics.median.
    static func median(_ values: [Double]) -> Double {
        let sorted = values.sorted()
        let middle = sorted.count / 2
        return sorted.count.isMultiple(of: 2) ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
    }

    /// statistics.quantiles(values, n=20)[18] (the "exclusive" method), or the maximum below 20 values.
    static func percentile95(_ values: [Double]) -> Double {
        let sorted = values.sorted()
        guard sorted.count >= 20 else { return sorted.last ?? 0 }
        let n = 20, i = 19, m = sorted.count + 1
        let j = min(max(i * m / n, 1), sorted.count - 1)
        let delta = Double(i * m - j * n)
        return (sorted[j - 1] * (Double(n) - delta) + sorted[j] * delta) / Double(n)
    }
}
