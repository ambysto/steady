/// Check #5 "Ping quality" (docs/DIAGNOSTICS.md), a port of `evaluate_ping` in app/diagnostics.py.
/// spec/diagnosis/ping.json holds the cases both implementations must agree on.
public enum PingQuality {
    /// One minute of measurements to one target. Targets starting with "tcp_" or "http_" are
    /// TCP/HTTP probes, "router" is the gateway, anything else is an Internet ping target.
    public struct Row: Equatable, Sendable, Decodable {
        public var target: String
        public var sent: Int
        public var lost: Int
        /// Mean jitter of the minute in ms, when measured.
        public var jitter: Double?
        /// Mean and highest round-trip time of the minute's replies in ms (minute_stats avg/max).
        /// Not used by the rule; the History screen draws them.
        public var avg: Double?
        public var max: Double?

        public init(target: String, sent: Int, lost: Int, jitter: Double? = nil, avg: Double? = nil, max: Double? = nil) {
            self.target = target
            self.sent = sent
            self.lost = lost
            self.jitter = jitter
            self.avg = avg
            self.max = max
        }
    }

    public static let router = "router"
    static let probePrefixes = ["tcp_", "http_"]

    /// - Parameters:
    ///   - hour: rows of the last hour, the window the verdict is based on.
    ///   - recent: rows of the last 5 minutes, only shown in the details.
    public static func evaluate(hour: [Row], recent: [Row], minSent: Int = 60, minProbeSent: Int = 20) -> CheckResult {
        let totals = aggregate(hour)
        let icmpHour = totals.filter { !isProbe($0.key) && $0.value.sent >= minSent }
        let probeHour = totals.filter { isProbe($0.key) && $0.value.sent >= minProbeSent }
        guard !icmpHour.isEmpty || !probeHour.isEmpty else {
            return CheckResult(id: 5, key: "ping", status: .info, summary: Message("diag.ping.no_data"))
        }
        let recentTotals = aggregate(recent)
        var statuses: [String: CheckStatus] = [:]
        var details: [MessageValue] = []

        for (target, total) in icmpHour.sorted(by: routerFirst) {
            var status = lossStatus(total.loss)
            if let jitter = total.jitter, jitter > 30 {
                status = max(status, .warn)
            }
            statuses[target] = status
            let jitter: MessageValue = total.jitter.map { .message(Message("diag.ping.jitter", ["jitter": .number($0)])) } ?? .empty
            var last5: MessageValue = .empty
            if let r = recentTotals[target], r.sent > 0 {
                last5 = .message(Message("diag.ping.recent", ["loss": .number(r.loss)]))
            }
            details.append(.message(Message("diag.ping.line", [
                "target": .text(target), "loss": .number(total.loss), "lost": .number(Double(total.lost)),
                "sent": .number(Double(total.sent)), "jitter": jitter, "recent": last5,
            ])))
        }
        for (target, total) in probeHour.sorted(by: { $0.key < $1.key }) {
            details.append(.message(Message("diag.ping.probe_line", [
                "target": .text(target), "loss": .number(total.loss), "lost": .number(Double(total.lost)),
                "sent": .number(Double(total.sent)),
            ])))
        }

        // "Does real traffic work?" = the best probe: one target blocking port 443 must not raise an alarm.
        let probeBest = probeHour.values.map(\.loss).min()
        let probesOK = probeBest.map { $0 <= 1.0 } ?? false
        let routerStatus = statuses[router]
        let routerBad = routerStatus == .warn || routerStatus == .bad
        let icmpInternet = CheckStatus.worst(statuses.filter { $0.key != Self.router }.values)
        let icmpLimited = !routerBad && icmpInternet >= .warn && probesOK
        if icmpLimited {   // ping to the Internet is lossy but real connections are fine: not a fault
            for target in statuses.keys where target != Self.router {
                statuses[target] = .info
            }
        }
        let overall = CheckStatus.worst(Array(statuses.values) + (probeBest.map { [lossStatus($0)] } ?? []))

        let summary: String
        if routerBad {
            summary = "diag.ping.router_loss"
        } else if icmpLimited {
            summary = "diag.ping.icmp_limited"
        } else if let probeBest, probeBest > 1.0 {
            summary = "diag.ping.wan_loss"
        } else if icmpInternet >= .warn {
            summary = "diag.ping.wan_loss_ping_only"
        } else {
            summary = "diag.ping.ok"
        }
        let advice: MessageValue = if routerBad {
            .message(Message("diag.ping.advice_router"))
        } else if icmpLimited {
            .message(Message("diag.ping.advice_icmp_limited"))
        } else if overall != .ok {
            .message(Message("diag.ping.advice_wan"))
        } else {
            .empty
        }
        return CheckResult(id: 5, key: "ping", status: overall, summary: Message(summary), details: details, advice: advice)
    }

    struct Total {
        var sent = 0
        var lost = 0
        var jitters: [Double] = []

        var loss: Double { sent > 0 ? 100.0 * Double(lost) / Double(sent) : 0 }
        /// statistics.fmean: an exact sum, then one division.
        var jitter: Double? { jitters.isEmpty ? nil : MinuteAggregator.exactSum(jitters) / Double(jitters.count) }
    }

    static func aggregate(_ rows: [Row]) -> [String: Total] {
        var totals: [String: Total] = [:]
        for row in rows {
            totals[row.target, default: Total()].sent += row.sent
            totals[row.target, default: Total()].lost += row.lost
            if let jitter = row.jitter {
                totals[row.target, default: Total()].jitters.append(jitter)
            }
        }
        return totals
    }

    static func isProbe(_ target: String) -> Bool {
        probePrefixes.contains { target.hasPrefix($0) }
    }

    static func lossStatus(_ lossPercent: Double) -> CheckStatus {
        lossPercent > 3 ? .bad : lossPercent > 1 ? .warn : .ok
    }

    private static func routerFirst(_ a: (key: String, value: Total), _ b: (key: String, value: Total)) -> Bool {
        let aRouter = a.key == router, bRouter = b.key == router
        return aRouter != bRouter ? aRouter : a.key < b.key
    }
}
