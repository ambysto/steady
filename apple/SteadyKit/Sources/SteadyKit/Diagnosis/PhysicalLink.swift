import Foundation

/// Check #13 "Poor physical link" (docs/DIAGNOSTICS.md), a port of `evaluate_link` and
/// `join_minutes` in app/diagnostics.py, over the connected minutes of the last 24 hours.
/// spec/diagnosis/link.json holds the cases both implementations must agree on.
///
/// Windows judges the receive rate; macOS reports only the transmit rate, which the Mac uses
/// instead. The thresholds were calibrated on Windows' Rx, so on the Mac they are provisional.
public enum PhysicalLink {
    /// One connected minute with Wi‑Fi numbers and enough router pings (app/diagnostics.py Minute).
    public struct Minute: Equatable, Sendable {
        public var ts: Int
        public var rssi: Int
        /// Link rate in Mbps: Rx on Windows, Tx on the Mac.
        public var rateMbps: Double
        public var routerLossPercent: Double

        public init(ts: Int, rssi: Int, rateMbps: Double, routerLossPercent: Double) {
            self.ts = ts
            self.rssi = rssi
            self.rateMbps = rateMbps
            self.routerLossPercent = routerLossPercent
        }
    }

    static let historySeconds = 24 * 3600
    static let minMinutes = 30
    static let warnFraction = 0.20
    static let infoFraction = 0.05
    static let recentSeconds = 3 * 3600
    static let recentMinMinutes = 15

    static func isBad(_ minute: Minute) -> Bool {
        minute.rateMbps <= 30 && minute.routerLossPercent >= 5
    }

    /// - Parameter now: enables the comparison with the last 3 hours.
    public static func evaluate(_ minutes: [Minute], now: Int?) -> CheckResult {
        guard minutes.count >= minMinutes else {
            return CheckResult(id: 13, key: "physical_link", status: .info,
                               summary: Message("diag.physical_link.not_enough", ["count": .number(Double(minutes.count)),
                                                                                  "needed": .number(Double(minMinutes))]))
        }
        let bad = minutes.filter(isBad).count
        let fraction = Double(bad) / Double(minutes.count)
        let rssiMedian = median(minutes.map(\.rssi))
        var details: [MessageValue] = [
            .message(Message("diag.physical_link.bad_minutes", ["bad": .number(Double(bad)),
                                                                "total": .number(Double(minutes.count)),
                                                                "fraction": .number(fraction)])),
            .message(Message("diag.physical_link.rssi", ["rssi": .number(rssiMedian)])),
        ]
        guard fraction >= infoFraction else {
            return CheckResult(id: 13, key: "physical_link", status: .ok, summary: Message("diag.physical_link.ok"),
                               details: details)
        }
        if let now {
            let recent = minutes.filter { $0.ts >= now - recentSeconds }
            if recent.count >= recentMinMinutes {
                let recentFraction = Double(recent.filter(isBad).count) / Double(recent.count)
                details.append(.message(Message("diag.physical_link.recent", ["fraction": .number(recentFraction),
                                                                              "total": .number(Double(recent.count))])))
                if recentFraction < infoFraction {
                    return CheckResult(id: 13, key: "physical_link", status: .info,
                                       summary: Message("diag.physical_link.improved"), details: details,
                                       advice: .message(Message("diag.physical_link.advice_improved")))
                }
            }
        }
        if rssiMedian < -76 {
            details.append(.message(Message("diag.physical_link.weak_signal")))
        }
        let status: CheckStatus = fraction >= warnFraction ? .warn : .info
        return CheckResult(id: 13, key: "physical_link", status: status,
                           summary: Message(status == .warn ? "diag.physical_link.warn" : "diag.physical_link.info"),
                           details: details, advice: .message(Message("diag.physical_link.advice")))
    }

    /// statistics.median: the middle value, or the mean of the two middle ones.
    static func median(_ values: [Int]) -> Double {
        let sorted = values.sorted()
        let middle = sorted.count / 2
        return sorted.count % 2 == 1 ? Double(sorted[middle]) : Double(sorted[middle - 1] + sorted[middle]) / 2
    }

    /// One minute per connected Wi‑Fi row that has a rate and ≥ `minSent` router pings in the
    /// same minute (`join_minutes`). The rate is Rx when the platform reports it, else Tx.
    public static func join(wifi: [WiFiMinute], ping: [PingQuality.Row], minSent: Int = 30) -> [Minute] {
        var router: [Int: PingQuality.Row] = [:]
        for row in ping where row.target == PingQuality.router {
            if let ts = row.ts { router[ts] = row }
        }
        return wifi.compactMap { stat in
            guard let row = router[stat.ts], stat.state == "connected", let rssi = stat.rssi,
                  let rate = stat.rxMbps ?? stat.txMbps, row.sent >= minSent else { return nil }
            return Minute(ts: stat.ts, rssi: rssi, rateMbps: rate, routerLossPercent: 100 * Double(row.lost) / Double(row.sent))
        }
    }
}

/// The Wi‑Fi interface at the end of one minute: a row of `wifi_stats` in app/storage.py. The
/// Mac leaves the network's name and BSSID out (they need location permission) and has no Rx rate.
public struct WiFiMinute: Equatable, Sendable {
    public var ts: Int
    public var state: String
    public var channel: Int?
    /// Signal quality in percent, as Windows reports it.
    public var signal: Double?
    public var rssi: Int?
    public var rxMbps: Double?
    public var txMbps: Double?

    public init(ts: Int, state: String, channel: Int? = nil, signal: Double? = nil, rssi: Int? = nil,
                rxMbps: Double? = nil, txMbps: Double? = nil) {
        self.ts = ts
        self.state = state
        self.channel = channel
        self.signal = signal
        self.rssi = rssi
        self.rxMbps = rxMbps
        self.txMbps = txMbps
    }
}
