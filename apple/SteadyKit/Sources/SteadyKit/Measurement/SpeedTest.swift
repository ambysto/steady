import Foundation

/// The speed test (ADR-0023): check #14's run read as the four figures a speed test app shows.
/// One run sets both this and the Bufferbloat result, so the line is loaded once.
public enum SpeedTest {
    public struct Result: Codable, Equatable, Sendable {
        public var measuredAt: Date
        /// Nil when the direction was refused or moved nothing.
        public var downloadMbps: Double?
        public var uploadMbps: Double?
        /// The load stopped at its byte limit, so the line is at least this fast.
        public var downloadCapped: Bool
        public var uploadCapped: Bool
        /// Median ping to the Internet while idle, and the worse direction's median under load.
        public var idlePingMs: Double?
        public var loadedPingMs: Double?
        /// HTTP status when the test server refused a direction, and until when it asked to wait.
        public var refusedStatus: Int?
        public var retryUntil: Date?

        public init(measuredAt: Date, downloadMbps: Double? = nil, uploadMbps: Double? = nil,
                    downloadCapped: Bool = false, uploadCapped: Bool = false, idlePingMs: Double? = nil,
                    loadedPingMs: Double? = nil, refusedStatus: Int? = nil, retryUntil: Date? = nil) {
            self.measuredAt = measuredAt
            self.downloadMbps = downloadMbps
            self.uploadMbps = uploadMbps
            self.downloadCapped = downloadCapped
            self.uploadCapped = uploadCapped
            self.idlePingMs = idlePingMs
            self.loadedPingMs = loadedPingMs
            self.refusedStatus = refusedStatus
            self.retryUntil = retryUntil
        }

        /// At least one direction was measured.
        public var hasFigures: Bool { downloadMbps != nil || uploadMbps != nil }
    }

    /// The target whose pings are the figures (the router's are check #14's alone).
    static let internet = "internet"

    public static func result(from run: BufferbloatTest.Run, at date: Date) -> Result {
        let measurement = run.measurement
        let directions = [(measurement.download, run.downloadSteadyMbps), (measurement.upload, run.uploadSteadyMbps)]
        let figures = directions.map { phase, steady in figure(phase, steady: steady) }
        let loaded = directions.filter { phase, _ in phase.refused == nil && (phase.mbps ?? 0) > 0 }
            .compactMap { phase, _ in medianPing(phase) }
        let refusals = [measurement.download, measurement.upload].filter { $0.refused != nil }
        let wait = refusals.compactMap(\.retryAfterS).max()
        return Result(measuredAt: date, downloadMbps: figures[0], uploadMbps: figures[1],
                      downloadCapped: figures[0] != nil && measurement.download.capped == true,
                      uploadCapped: figures[1] != nil && measurement.upload.capped == true,
                      idlePingMs: medianPing(measurement.idle), loadedPingMs: loaded.max(),
                      refusedStatus: refusals.first?.refused, retryUntil: wait.map { date.addingTimeInterval($0) })
    }

    /// The direction's speed: the rate after the ramp, or the whole phase's when the load ended
    /// before the ramp did (a very fast line hitting the byte limit). Nil when refused or nothing moved.
    static func figure(_ phase: Bufferbloat.Phase, steady: Double?) -> Double? {
        guard phase.refused == nil, let average = phase.mbps, average > 0 else { return nil }
        return steady ?? average
    }

    static func medianPing(_ phase: Bufferbloat.Phase) -> Double? {
        let replies = phase.rtts.first { $0.label == internet }?.samples.compactMap { $0 } ?? []
        return replies.isEmpty ? nil : Bufferbloat.median(replies)
    }

    /// Ask before running: every time on a metered path (mobile data, a hotspot), otherwise only the
    /// first time.
    public static func needsConfirmation(metered: Bool, confirmedBefore: Bool) -> Bool {
        metered || !confirmedBefore
    }
}
