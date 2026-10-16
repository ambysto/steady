import Foundation

/// The speed test (ADR-0023): check #14's run read as the figures a speed test app shows.
/// One run sets both this and the Bufferbloat result, so the line is loaded once.
public enum SpeedTest {
    public typealias Stage = BufferbloatTest.Stage

    /// One reading of the live rate, `seconds` after its direction began.
    public struct Point: Codable, Equatable, Sendable {
        public var seconds: Double
        public var mbps: Double

        public init(seconds: Double, mbps: Double) {
            self.seconds = seconds
            self.mbps = mbps
        }
    }

    /// Latency and jitter of one phase, from the Internet target's replies.
    public struct Latency: Codable, Equatable, Sendable {
        public var medianMs: Double?
        public var jitterMs: Double?

        public init(medianMs: Double? = nil, jitterMs: Double? = nil) {
            self.medianMs = medianMs
            self.jitterMs = jitterMs
        }

        init(_ replies: [Double?]) {
            self.init(medianMs: SpeedTest.median(replies), jitterMs: SpeedTest.jitter(replies))
        }
    }

    public struct Result: Codable, Equatable, Sendable {
        public var measuredAt: Date
        /// Nil when the direction was refused or moved nothing.
        public var downloadMbps: Double?
        public var uploadMbps: Double?
        /// The load stopped at its byte limit, so the line is at least this fast.
        public var downloadCapped: Bool
        public var uploadCapped: Bool
        /// While idle, and while each direction loaded (after the ramp).
        public var idle: Latency?
        public var duringDownload: Latency?
        public var duringUpload: Latency?
        /// Internet pings lost while idle and while loaded, after the ramp.
        public var lossPercent: Double?
        public var downloadSeries: [Point]?
        public var uploadSeries: [Point]?
        public var server: String?
        /// HTTP status when the test server refused a direction, and until when it asked to wait.
        public var refusedStatus: Int?
        public var retryUntil: Date?
        /// The run's check #14 measurement, so the Bufferbloat result comes back with the speed test's
        /// after a relaunch. Nil in a result saved before it was kept.
        public var measurement: Bufferbloat.Measurement?

        public init(measuredAt: Date, downloadMbps: Double? = nil, uploadMbps: Double? = nil,
                    downloadCapped: Bool = false, uploadCapped: Bool = false, idle: Latency? = nil,
                    duringDownload: Latency? = nil, duringUpload: Latency? = nil, lossPercent: Double? = nil,
                    downloadSeries: [Point]? = nil, uploadSeries: [Point]? = nil, server: String? = nil,
                    refusedStatus: Int? = nil, retryUntil: Date? = nil, measurement: Bufferbloat.Measurement? = nil) {
            self.measuredAt = measuredAt
            self.downloadMbps = downloadMbps
            self.uploadMbps = uploadMbps
            self.downloadCapped = downloadCapped
            self.uploadCapped = uploadCapped
            self.idle = idle
            self.duringDownload = duringDownload
            self.duringUpload = duringUpload
            self.lossPercent = lossPercent
            self.downloadSeries = downloadSeries
            self.uploadSeries = uploadSeries
            self.server = server
            self.refusedStatus = refusedStatus
            self.retryUntil = retryUntil
            self.measurement = measurement
        }

        /// At least one direction was measured.
        public var hasFigures: Bool { downloadMbps != nil || uploadMbps != nil }
        /// Both directions were measured.
        public var isComplete: Bool { downloadMbps != nil && uploadMbps != nil }
    }

    /// The target whose pings are the figures (the router's are check #14's alone).
    static let internet = "internet"
    /// A stored series keeps at most this many points: plenty for a chart a few hundred points wide.
    static let maxPoints = 48

    public static func result(from run: BufferbloatTest.Run, at date: Date) -> Result {
        let measurement = run.measurement
        let download = figure(measurement.download, steady: run.downloadSteadyMbps)
        let upload = figure(measurement.upload, steady: run.uploadSteadyMbps)
        let refusals = [measurement.download, measurement.upload].filter { $0.refused != nil }
        let wait = refusals.compactMap(\.retryAfterS).max()
        // The loaded phases' replies are after the ramp already; a phase that did not load says nothing.
        let loadedReplies = [(measurement.download, download), (measurement.upload, upload)]
            .filter { $0.1 != nil }.flatMap { replies($0.0) }
        return Result(measuredAt: date, downloadMbps: download, uploadMbps: upload,
                      downloadCapped: download != nil && measurement.download.capped == true,
                      uploadCapped: upload != nil && measurement.upload.capped == true,
                      idle: Latency(replies(measurement.idle)),
                      duringDownload: download == nil ? nil : Latency(replies(measurement.download)),
                      duringUpload: upload == nil ? nil : Latency(replies(measurement.upload)),
                      lossPercent: loss(replies(measurement.idle) + loadedReplies),
                      downloadSeries: download == nil ? nil : thinned(run.downloadSeries),
                      uploadSeries: upload == nil ? nil : thinned(run.uploadSeries),
                      server: run.server,
                      refusedStatus: refusals.first?.refused, retryUntil: wait.map { date.addingTimeInterval($0) },
                      measurement: measurement)
    }

    /// The direction's speed: the rate after the ramp, or the whole phase's when the load ended
    /// before the ramp did (a very fast line hitting the byte limit). Nil when refused, or when the
    /// load stayed under `Bufferbloat.minMbps` (it did not load the line, as check #14 says too).
    static func figure(_ phase: Bufferbloat.Phase, steady: Double?) -> Double? {
        guard phase.refused == nil, let average = phase.mbps, average >= Bufferbloat.minMbps else { return nil }
        return steady ?? average
    }

    static func replies(_ phase: Bufferbloat.Phase) -> [Double?] {
        phase.rtts.first { $0.label == internet }?.samples ?? []
    }

    // --- statistics shared by the live view and the result

    static func median(_ replies: [Double?]) -> Double? {
        let got = replies.compactMap { $0 }
        return got.isEmpty ? nil : Bufferbloat.median(got)
    }

    /// The mean absolute difference between consecutive replies, as speed.cloudflare.com reports it.
    static func jitter(_ replies: [Double?]) -> Double? {
        let got = replies.compactMap { $0 }
        guard got.count >= 2 else { return nil }
        return zip(got, got.dropFirst()).map { abs($1 - $0) }.reduce(0, +) / Double(got.count - 1)
    }

    static func loss(_ replies: [Double?]) -> Double? {
        replies.isEmpty ? nil : 100 * Double(replies.filter { $0 == nil }.count) / Double(replies.count)
    }

    /// Every n-th point, so at most `maxPoints` are kept; the last one always is.
    static func thinned(_ series: [Point]) -> [Point] {
        guard series.count > maxPoints else { return series }
        let step = Double(series.count - 1) / Double(maxPoints - 1)
        return (0..<maxPoints).map { series[Int((Double($0) * step).rounded())] }
    }

    /// Ask before running: every time on a metered path (mobile data, a hotspot), otherwise only the
    /// first time.
    public static func needsConfirmation(metered: Bool, confirmedBefore: Bool) -> Bool {
        metered || !confirmedBefore
    }
}

extension SpeedTest {
    /// A run as it goes, built from its events: what the speed test shows before the result exists.
    public struct Live: Equatable, Sendable {
        public private(set) var stage: Stage = .idle
        /// Seconds into the current phase, from the latest event.
        public private(set) var seconds = 0.0
        public private(set) var downloadSeries: [Point] = []
        public private(set) var uploadSeries: [Point] = []
        private var pings: [Stage: [Reply]] = [:]
        /// Each ended direction's figure, as the result will show it (nil: it has none).
        private var figures: [Stage: Double?] = [:]

        private struct Reply: Equatable, Sendable {
            let seconds: Double
            let ms: Double?
        }

        public init() {}

        public mutating func apply(_ event: BufferbloatTest.Event) {
            switch event {
            case .stage(let next):
                stage = next
                seconds = 0
            case .rate(let phase, let at, let mbps):
                if phase == stage { seconds = max(seconds, at) }
                if phase == .upload {
                    uploadSeries.append(Point(seconds: at, mbps: mbps))
                } else {
                    downloadSeries.append(Point(seconds: at, mbps: mbps))
                }
            case .ping(let phase, let at, let ms):
                if phase == stage { seconds = max(seconds, at) }
                pings[phase, default: []].append(Reply(seconds: at, ms: ms))
            case .figure(let phase, let mbps):
                figures[phase] = .some(mbps)   // kept when nil: the direction ended without a figure
            }
        }

        /// A direction's figure: the latest rate while it loads, the result's figure once it has
        /// ended (so it does not change when the result arrives), nil before. Without that figure,
        /// the mean of the rates read after the ramp.
        public func mbps(_ direction: Stage) -> Double? {
            if let figure = figures[direction] { return figure }
            let series = direction == .upload ? uploadSeries : downloadSeries
            if direction == stage { return series.last?.mbps }
            let steady = series.filter { $0.seconds >= BufferbloatTest.rampSeconds }.map(\.mbps)
            let values = steady.isEmpty ? series.map(\.mbps) : steady
            return values.isEmpty ? nil : values.reduce(0, +) / Double(values.count)
        }

        /// A phase's latency so far. Under load, the replies after the ramp once there are any.
        public func latency(_ phase: Stage) -> Latency? {
            let replies = replies(phase)
            return replies.isEmpty ? nil : Latency(replies)
        }

        /// Internet pings lost so far, over the phases measured.
        public var lossPercent: Double? {
            SpeedTest.loss([Stage.idle, .download, .upload].flatMap(replies))
        }

        /// How far the run is, 0…1, by the phases' planned lengths.
        public var fraction: Double {
            let before = switch stage {
            case .idle: 0.0
            case .download: BufferbloatTest.idleSeconds
            case .upload: BufferbloatTest.idleSeconds + BufferbloatTest.loadSeconds
            }
            let length = stage == .idle ? BufferbloatTest.idleSeconds : BufferbloatTest.loadSeconds
            return (before + min(seconds, length)) / BufferbloatTest.duration
        }

        private func replies(_ phase: Stage) -> [Double?] {
            let all = pings[phase] ?? []
            guard phase != .idle else { return all.map(\.ms) }
            let steady = all.filter { $0.seconds >= BufferbloatTest.rampSeconds }
            return (steady.isEmpty ? all : steady).map(\.ms)
        }
    }
}
