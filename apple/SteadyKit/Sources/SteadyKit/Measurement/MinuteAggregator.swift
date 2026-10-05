/// Groups samples into per-minute rows, a port of `MinuteAggregator` in app/monitor.py, so the
/// rules see the same numbers as on Windows: sent, lost, and jitter = mean absolute difference
/// between consecutive replies, rounded to 0.1 ms.
public struct MinuteAggregator: Sendable {
    public struct Minute: Sendable, Equatable {
        /// Start of the minute, seconds since 1970.
        public let start: Int
        public let rows: [PingQuality.Row]
    }

    private struct Bucket: Sendable {
        var sent = 0
        var lost = 0
        var replies: [Double] = []
    }

    private var minute: Int?
    private var buckets: [String: Bucket] = [:]
    private var order: [String] = []

    public init() {}

    /// When `time` is past the current minute, closes it and returns its rows.
    public mutating func roll(at time: Double) -> Minute? {
        let start = Self.minuteStart(time)
        guard let current = minute else {
            minute = start
            return nil
        }
        guard start > current else { return nil }
        let closed = close()
        minute = start
        return closed
    }

    /// Closes the current, possibly partial, minute (app/monitor.py flush), e.g. when measuring stops.
    public mutating func flush() -> Minute? {
        guard minute != nil else { return nil }
        let closed = close()
        minute = nil
        return closed
    }

    public mutating func add(_ target: String, rttMs: Double?) {
        if buckets[target] == nil {
            order.append(target)
        }
        buckets[target, default: Bucket()].sent += 1
        if let rttMs {
            buckets[target, default: Bucket()].replies.append(rttMs)
        } else {
            buckets[target, default: Bucket()].lost += 1
        }
    }

    private mutating func close() -> Minute? {
        defer {
            buckets = [:]
            order = []
        }
        guard let minute, !order.isEmpty else { return nil }
        let rows = order.map { target in
            let bucket = buckets[target]!
            return PingQuality.Row(target: target, sent: bucket.sent, lost: bucket.lost,
                                   jitter: Self.jitter(bucket.replies).map(Self.roundToTenth))
        }
        return Minute(start: minute, rows: rows)
    }

    static func minuteStart(_ time: Double) -> Int {
        Int((time / 60).rounded(.down)) * 60
    }

    /// Mean absolute difference between consecutive values; nil with fewer than two.
    static func jitter(_ replies: [Double]) -> Double? {
        guard replies.count > 1 else { return nil }
        let total = zip(replies.dropFirst(), replies).reduce(0) { $0 + abs($1.0 - $1.1) }
        return total / Double(replies.count - 1)
    }

    /// Python's round(x, 1), which rounds the exact binary value: 0.35 is 0.3499999… and gives 0.3,
    /// 30.05 is 30.0500000…07 and gives 30.1. The C library's "%.1f" rounds the same way;
    /// (x * 10).rounded() / 10 does not.
    static func roundToTenth(_ value: Double) -> Double {
        guard value.isFinite else { return value }
        return Double(String(format: "%.1f", value)) ?? value
    }

    /// Python's math.fsum: the correctly rounded sum (Shewchuk's partials, as CPython does), so
    /// means match statistics.fmean to the last bit.
    static func exactSum(_ values: some Sequence<Double>) -> Double {
        var partials: [Double] = []
        for value in values {
            var x = value
            var i = 0
            for var y in partials {
                if abs(x) < abs(y) { swap(&x, &y) }
                let high = x + y
                let low = y - (high - x)
                if low != 0 {
                    partials[i] = low
                    i += 1
                }
                x = high
            }
            partials.removeSubrange(i...)
            partials.append(x)
        }
        guard var n = partials.indices.last else { return 0 }
        var high = partials[n]
        var low = 0.0
        while n > 0 {
            let x = high
            n -= 1
            let y = partials[n]
            high = x + y
            low = y - (high - x)
            if low != 0 { break }
        }
        if n > 0, (low < 0 && partials[n - 1] < 0) || (low > 0 && partials[n - 1] > 0) {
            let y = low * 2
            let x = high + y
            if y == x - high { high = x }
        }
        return high
    }
}

/// What the screen shows for one target: the latest result and the last measurements' loss and jitter.
public struct LiveStats: Equatable, Sendable {
    /// Oldest first; nil = no reply.
    public let samples: [Double?]

    public init(samples: [Double?]) {
        self.samples = samples
    }

    public var isEmpty: Bool { samples.isEmpty }
    /// Round-trip time of the most recent measurement; nil when it got no reply.
    public var latest: Double?? { samples.last }
    public var lossPercent: Double {
        samples.isEmpty ? 0 : 100 * Double(samples.filter { $0 == nil }.count) / Double(samples.count)
    }
    public var jitter: Double? { MinuteAggregator.jitter(samples.compactMap { $0 }) }
}
