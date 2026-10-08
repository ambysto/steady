/// The Overview's "Last 7 days" card on Apple platforms (ADR-0020 point 11, SIC-108): what the
/// minutes stored on the device say about the week, and nothing they do not. The app measures
/// only while it is open, so every number is about the time it was open.
public struct WeekSummary: Equatable, Sendable {
    public static let seconds = 7 * 86_400
    /// Below this many measured minutes the card shows only how long it measured, like check #5,
    /// which needs an hour.
    public static let enoughMinutes = 60

    /// Minutes with any measurement.
    public let measuredMinutes: Int
    /// Medians over the measured minutes of each minute's mean round-trip time.
    public let routerMedianMs: Double?
    public let internetMedianMs: Double?
    /// Lost over sent to the Internet ping targets, as the History chart counts it.
    public let internetLossPercent: Double?
    /// Times the Internet was unreachable while the app was open: runs of consecutive minutes in
    /// which every Internet target, pings and TCP probes alike, lost everything it sent.
    public let outages: Int
    public let outageMinutes: Int

    public var hasEnoughData: Bool { measuredMinutes >= Self.enoughMinutes }

    public init(_ minutes: [MinuteAggregator.Minute], now: Double) {
        let start = Int(now) - Self.seconds
        let week = minutes.filter { $0.start >= start && $0.start <= Int(now) && !$0.rows.isEmpty }
            .sorted { $0.start < $1.start }
        measuredMinutes = week.count

        func isInternetPing(_ target: String) -> Bool { LiveMonitor.internetTargets.contains { $0.id == target } }
        func isInternet(_ target: String) -> Bool { target != PingQuality.router }
        func median(_ values: [Double]) -> Double? {
            let sorted = values.sorted()
            guard !sorted.isEmpty else { return nil }
            let middle = sorted.count / 2
            return sorted.count.isMultiple(of: 2) ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle]
        }
        /// Mean of the minute's replies to the targets `include` picks, weighted by replies.
        func minuteMean(_ minute: MinuteAggregator.Minute, _ include: (String) -> Bool) -> Double? {
            var total = 0.0
            var replies = 0
            for row in minute.rows where include(row.target) {
                guard let avg = row.avg, row.sent > row.lost else { continue }
                total += avg * Double(row.sent - row.lost)
                replies += row.sent - row.lost
            }
            return replies > 0 ? total / Double(replies) : nil
        }

        routerMedianMs = median(week.compactMap { minuteMean($0) { $0 == PingQuality.router } })
        internetMedianMs = median(week.compactMap { minuteMean($0, isInternetPing) })
        let pings = week.flatMap(\.rows).filter { isInternetPing($0.target) }
        let sent = pings.reduce(0) { $0 + $1.sent }
        internetLossPercent = sent > 0 ? 100 * Double(pings.reduce(0) { $0 + $1.lost }) / Double(sent) : nil

        var outages = 0
        var outageMinutes = 0
        var previousOffline: Int?
        for minute in week {
            let internet = minute.rows.filter { isInternet($0.target) && $0.sent > 0 }
            let offline = !internet.isEmpty && internet.allSatisfy { $0.lost == $0.sent }
            guard offline else {
                previousOffline = nil
                continue
            }
            outageMinutes += 1
            if previousOffline.map({ minute.start - $0 > 60 }) ?? true {
                outages += 1   // a new run; a gap (app closed) also ends one
            }
            previousOffline = minute.start
        }
        self.outages = outages
        self.outageMinutes = outageMinutes
    }
}
