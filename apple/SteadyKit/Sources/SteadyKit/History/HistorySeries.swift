import Foundation

/// Per-minute history turned into chart points: latency and loss to the router and to the
/// Internet (the ICMP targets 1.1.1.1 and 8.8.8.8 together), in buckets that suit the range.
public enum HistorySeries {
    public enum Range: String, CaseIterable, Identifiable, Sendable {
        case hour, day, week

        public var id: String { rawValue }
        public var seconds: Int {
            switch self {
            case .hour: 3600
            case .day: 86_400
            case .week: 7 * 86_400
            }
        }
        /// Width of one point: a minute, 10 minutes, an hour.
        public var bucket: Int {
            switch self {
            case .hour: 60
            case .day: 600
            case .week: 3600
            }
        }
        public var titleKey: String { "ui.history.range.\(rawValue)" }
    }

    public enum Line: String, CaseIterable, Sendable {
        case router, internet

        public var titleKey: String { self == .router ? "ui.overview.router" : "ui.overview.internet" }

        func includes(_ target: String) -> Bool {
            switch self {
            case .router: target == PingQuality.router
            case .internet: LiveMonitor.internetTargets.contains { $0.id == target }
            }
        }
    }

    public struct Point: Identifiable, Equatable, Sendable {
        public let line: Line
        public let start: Date
        /// Mean round-trip time of the bucket's replies; nil when nothing answered.
        public let latency: Double?
        public let lossPercent: Double
        /// Increases at every gap in the data, so a chart does not draw a line across the gap.
        public let segment: Int

        public var id: String { "\(line.rawValue)-\(Int(start.timeIntervalSince1970))" }
    }

    /// Points for `range` ending at `now`, oldest first per line.
    public static func points(_ minutes: [MinuteAggregator.Minute], range: Range, now: Date) -> [Point] {
        let end = Int(now.timeIntervalSince1970)
        let start = end - range.seconds
        var result: [Point] = []
        for line in Line.allCases {
            var buckets: [Int: (sent: Int, lost: Int, replies: Int, total: Double)] = [:]
            for minute in minutes where minute.start >= start && minute.start <= end {
                let bucket = minute.start - minute.start % range.bucket
                for row in minute.rows where line.includes(row.target) {
                    var entry = buckets[bucket] ?? (0, 0, 0, 0)
                    entry.sent += row.sent
                    entry.lost += row.lost
                    let replies = row.sent - row.lost
                    if let avg = row.avg, replies > 0 {
                        entry.replies += replies
                        entry.total += avg * Double(replies)
                    }
                    buckets[bucket] = entry
                }
            }
            var segment = 0
            var previous: Int?
            for bucket in buckets.keys.sorted() {
                let entry = buckets[bucket]!
                guard entry.sent > 0 else { continue }
                if let previous, bucket - previous > range.bucket {
                    segment += 1
                }
                previous = bucket
                result.append(Point(line: line, start: Date(timeIntervalSince1970: TimeInterval(bucket)),
                                    latency: entry.replies > 0 ? entry.total / Double(entry.replies) : nil,
                                    lossPercent: 100 * Double(entry.lost) / Double(entry.sent), segment: segment))
            }
        }
        return result
    }
}
