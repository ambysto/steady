import Foundation
import Synchronization

/// Measures check #14 like app/bufferbloat.py: pings while idle, then while downloading, then
/// while uploading. Generates real traffic (about 250 MB per 100 Mbps of line speed, at most 2 GB),
/// so it runs only when the user asks. The same run is the speed test (ADR-0023, `SpeedTest`).
public enum BufferbloatTest {
    public enum Stage: Sendable {
        case idle, download, upload
    }

    /// Where a run is: the phase, and while loading the rate over the last second.
    public struct Progress: Equatable, Sendable {
        public var stage: Stage
        public var liveMbps: Double?

        public init(stage: Stage, liveMbps: Double? = nil) {
            self.stage = stage
            self.liveMbps = liveMbps
        }
    }

    /// What a run measured: check #14's measurement, and each direction's rate after the ramp
    /// (the speed test's figure; `Phase.mbps` stays the whole phase's average, as on Windows).
    public struct Run: Equatable, Sendable {
        public var measurement: Bufferbloat.Measurement
        public var downloadSteadyMbps: Double?
        public var uploadSteadyMbps: Double?

        public init(measurement: Bufferbloat.Measurement, downloadSteadyMbps: Double? = nil,
                    uploadSteadyMbps: Double? = nil) {
            self.measurement = measurement
            self.downloadSteadyMbps = downloadSteadyMbps
            self.uploadSteadyMbps = uploadSteadyMbps
        }
    }

    static let downloadURL = URL(string: "https://speed.cloudflare.com/__down?bytes=25000000")!
    static let uploadURL = URL(string: "https://speed.cloudflare.com/__up")!
    static let idleSeconds = 4.0, loadSeconds = 10.0, interval = 0.2
    static let rampSeconds = 2.0   // samples taken while the line is still picking up speed are dropped
    static let connections = 4
    static let meterInterval = 0.25   // how often the live rate is read while a direction loads
    /// Each direction runs for `loadSeconds` and stops early only here (MAX_BYTES in app/bufferbloat.py,
    /// 800 Mbps over 10 s); a phase that reaches it is marked `capped`, as the line may be faster.
    public static let maxBytes: Int64 = 1_000_000_000
    /// The limit on a metered path (mobile data, a personal hotspot): 250 MB each way, 500 MB in all,
    /// so a line above 200 Mbps reads as capped (the result is then "incomplete", never a false "ok").
    static let meteredMaxBytes: Int64 = 250_000_000

    /// The byte limit for each direction on a path; nil when the test should not run at all
    /// (Low Data Mode: the user asked the system to save data).
    public static func byteLimit(expensive: Bool, constrained: Bool) -> Int64? {
        constrained ? nil : expensive ? meteredMaxBytes : maxBytes
    }
    /// speed.cloudflare.com refuses a single request above ~25 MB, so workers repeat 25 MB requests.
    static let bytesPerRequest = 25_000_000

    /// - Parameters:
    ///   - router: IPv4 address of the router, pinged alongside the Internet when known.
    ///   - maxBytes: the limit for each direction, from `byteLimit(expensive:constrained:)`.
    ///   - progress: called as each phase starts, then with the live rate every `meterInterval`.
    public static func run(router: String?, maxBytes: Int64 = maxBytes,
                           progress: @escaping @Sendable (Progress) async -> Void = { _ in }) async -> Run {
        var targets: [(label: String, address: String)] = []
        if let router { targets.append(("router", router)) }
        targets.append(("internet", "1.1.1.1"))

        await progress(Progress(stage: .idle))
        let idle = Bufferbloat.Phase(rtts: await sample(targets, seconds: idleSeconds).samples)
        await progress(Progress(stage: .download))
        let download = await loaded(targets, upload: false, maxBytes: maxBytes) {
            await progress(Progress(stage: .download, liveMbps: $0))
        }
        await progress(Progress(stage: .upload))
        let upload = await loaded(targets, upload: true, maxBytes: maxBytes) {
            await progress(Progress(stage: .upload, liveMbps: $0))
        }
        return Run(measurement: Bufferbloat.Measurement(idle: idle, download: download.phase, upload: upload.phase),
                   downloadSteadyMbps: download.steadyMbps, uploadSteadyMbps: upload.steadyMbps)
    }

    /// Pings every target once per `interval` for `seconds`, the targets in parallel. `starts` holds
    /// when each round began, in seconds after `since` (or after the first round).
    static func sample(_ targets: [(label: String, address: String)], seconds: Double,
                       since: ContinuousClock.Instant? = nil) async -> (samples: [Bufferbloat.Samples], starts: [Double]) {
        var samples = targets.map { Bufferbloat.Samples(label: $0.label, samples: []) }
        var starts: [Double] = []
        let clock = ContinuousClock()
        let origin = since ?? clock.now
        let end = clock.now + .seconds(seconds)
        while clock.now < end, !Task.isCancelled {
            let started = clock.now
            starts.append((started - origin).inMilliseconds / 1000)
            let round = await withTaskGroup(of: (Int, Double?).self) { group in
                for (index, target) in targets.enumerated() {
                    group.addTask { (index, await ICMPPing.ping(target.address)) }
                }
                var results = [Double?](repeating: nil, count: targets.count)
                for await (index, rtt) in group {
                    results[index] = rtt
                }
                return results
            }
            for index in samples.indices {
                samples[index].samples.append(round[index])
            }
            try? await Task.sleep(until: started + .seconds(interval), clock: clock)
        }
        return (samples, starts)
    }

    /// Drops the rounds that began during the ramp. By time, not by count, as app/bufferbloat.py does:
    /// on a bloated line each round waits for slow or lost pings, so a fixed count can cover most of the phase.
    static func droppingRamp(_ samples: [Bufferbloat.Samples], starts: [Double]) -> [Bufferbloat.Samples] {
        let keep = starts.firstIndex { $0 >= rampSeconds } ?? starts.count
        return samples.map { Bufferbloat.Samples(label: $0.label, samples: Array($0.samples.dropFirst(keep))) }
    }

    static func loaded(_ targets: [(label: String, address: String)], upload: Bool, maxBytes: Int64,
                       live: @escaping @Sendable (Double?) async -> Void) async
        -> (phase: Bufferbloat.Phase, steadyMbps: Double?) {
        let load = LoadGenerator(upload: upload, maxBytes: maxBytes)
        let clock = ContinuousClock()
        let started = clock.now
        // When the load ended: before the pings do when it stops at its byte limit.
        let loadTask = Task { (await load.run(seconds: loadSeconds), (clock.now - started).inMilliseconds / 1000) }
        let meterTask = Task { await meter(load, since: started, live: live) }
        let (samples, starts) = await withTaskCancellationHandler {
            await sample(targets, seconds: loadSeconds, since: started)
        } onCancel: {
            load.stop()   // leaving the screen must not leave up to 1 GB of transfers running
            meterTask.cancel()
        }
        load.stop()
        let (outcome, loadEnded) = await loadTask.value
        meterTask.cancel()
        let atRamp = await meterTask.value
        let elapsed = max((clock.now - started).inMilliseconds / 1000, 1e-6)
        return (phase(rtts: droppingRamp(samples, starts: starts), outcome: outcome, elapsed: elapsed, maxBytes: maxBytes),
                steadyMbps(atRamp: atRamp, bytes: outcome.bytes, seconds: loadEnded))
    }

    /// Reads the load's byte total every `meterInterval` until cancelled, reports the rate over the
    /// last second, and returns the first reading taken once the ramp was over.
    static func meter(_ load: LoadGenerator, since started: ContinuousClock.Instant,
                      live: @Sendable (Double?) async -> Void) async -> (seconds: Double, bytes: Int64)? {
        let clock = ContinuousClock()
        var rate = LiveRate()
        var atRamp: (seconds: Double, bytes: Int64)?
        while !Task.isCancelled {
            let seconds = (clock.now - started).inMilliseconds / 1000
            let bytes = load.bytes
            if atRamp == nil, seconds >= rampSeconds {
                atRamp = (seconds, bytes)
            }
            await live(rate.add(seconds: seconds, bytes: bytes))
            try? await Task.sleep(for: .seconds(meterInterval))
        }
        return atRamp
    }

    /// The rate after the ramp: what moved between the ramp's reading and the end of the load, over
    /// that time. Nil when the load ended within half a second of the ramp (too short to trust).
    static func steadyMbps(atRamp: (seconds: Double, bytes: Int64)?, bytes: Int64, seconds: Double) -> Double? {
        guard let atRamp, seconds - atRamp.seconds >= 0.5, bytes > atRamp.bytes else { return nil }
        return Double(bytes - atRamp.bytes) * 8 / (seconds - atRamp.seconds) / 1e6
    }

    /// The phase a load produced, as `loaded` in app/bufferbloat.py `measure` builds it.
    static func phase(rtts: [Bufferbloat.Samples], outcome: LoadGenerator.Outcome, elapsed: Double,
                      maxBytes: Int64) -> Bufferbloat.Phase {
        var phase = Bufferbloat.Phase(rtts: rtts, mbps: Double(outcome.bytes) * 8 / elapsed / 1e6,
                                      error: outcome.error ?? "", capped: outcome.bytes >= maxBytes)
        if let refusal = outcome.refusal {
            // Even after some data: the load was not kept up, so the phase measured a part-time load.
            phase.refused = refusal.status
            phase.retryAfterS = refusal.retryAfterS
            phase.error = "HTTP \(refusal.status)"
        }
        return phase
    }

    /// Retry-After in seconds; nil when absent or an HTTP date (rare, and a rough "later" is enough).
    static func retryAfter(_ value: String?) -> Double? {
        guard let value, let seconds = Double(value.trimmingCharacters(in: .whitespaces)), seconds.isFinite else {
            return nil
        }
        return max(0, seconds)
    }
}

/// Keeps the line busy over several connections (app/bufferbloat.py http_download / http_upload):
/// each worker has its own URLSession, so each is its own TCP connection, and repeats 25 MB
/// requests until the deadline, `stop()` or `maxBytes` in total.
final class LoadGenerator: Sendable {
    /// The test server refused the load (HTTP 429/403): a limit on its side, not a property of the line.
    /// speed.cloudflare.com does this per address after a few runs and says when to come back.
    struct Refusal: Equatable, Sendable {
        let status: Int
        let retryAfterS: Double?
    }

    struct Outcome: Sendable {
        let bytes: Int64
        let error: String?
        var refusal: Refusal? = nil
    }

    private let upload: Bool
    let maxBytes: Int64
    let downloadURL: URL
    let configuration: URLSessionConfiguration?
    private let state = Mutex(State())

    private struct State {
        var bytes: Int64 = 0
        var errors: [String] = []
        var refusal: Refusal?
        var stopped = false
        var workers: [LoadWorker] = []
    }

    /// - Parameter configuration: for tests (a stub URLProtocol); nil = an ephemeral session per worker.
    init(upload: Bool, maxBytes: Int64 = BufferbloatTest.maxBytes, downloadURL: URL = BufferbloatTest.downloadURL,
         configuration: URLSessionConfiguration? = nil) {
        self.upload = upload
        self.maxBytes = maxBytes
        self.downloadURL = downloadURL
        self.configuration = configuration
    }

    func run(seconds: Double) async -> Outcome {
        let deadline = ContinuousClock.now + .seconds(seconds)
        let workers = (0..<BufferbloatTest.connections).map { _ in LoadWorker(generator: self) }
        state.withLock { $0.workers = workers }
        let timer = Task {
            try? await Task.sleep(until: deadline, clock: .continuous)
            self.stop()
        }
        await withTaskGroup(of: Void.self) { group in
            for worker in workers {
                group.addTask {
                    while !self.isDone(deadline: deadline) {
                        guard await worker.transfer(self.request(), body: self.upload ? Self.body : nil) else { break }
                    }
                }
            }
        }
        timer.cancel()
        workers.forEach { $0.close() }
        return state.withLock {
            Outcome(bytes: $0.bytes, error: $0.bytes == 0 ? $0.errors.first : nil, refusal: $0.refusal)
        }
    }

    func stop() {
        let workers = state.withLock { state in
            state.stopped = true
            return state.workers
        }
        workers.forEach { $0.cancel() }
    }

    /// Counts bytes moved; returns false once the total cap is reached.
    func add(_ count: Int64) -> Bool {
        state.withLock { state in
            state.bytes += count
            return state.bytes < maxBytes
        }
    }

    /// Records the first refusal and stops every worker: asking again would only prolong the limit.
    func refuse(_ refusal: Refusal) {
        state.withLock { state in
            if state.refusal == nil { state.refusal = refusal }
        }
        stop()
    }

    func fail(_ message: String) {
        state.withLock { state in
            if !state.stopped { state.errors.append(message) }
        }
    }

    /// Bytes moved so far, read while the load runs (the live rate).
    var bytes: Int64 {
        state.withLock { $0.bytes }
    }

    var isStopped: Bool {
        state.withLock { $0.stopped }
    }

    private func isDone(deadline: ContinuousClock.Instant) -> Bool {
        ContinuousClock.now >= deadline || state.withLock { $0.stopped || $0.bytes >= maxBytes }
    }

    private static let body = Data(count: BufferbloatTest.bytesPerRequest)
    private static let userAgent = "AmbystoSteady/"
        + ((Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0")

    private func request() -> URLRequest {
        var request = URLRequest(url: upload ? BufferbloatTest.uploadURL : downloadURL,
                                 cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 5)
        request.httpMethod = upload ? "POST" : "GET"
        request.setValue(Self.userAgent, forHTTPHeaderField: "User-Agent")
        if upload {
            request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        }
        return request
    }
}

/// One connection of a `LoadGenerator`: runs one transfer at a time and reports bytes as they move.
final class LoadWorker: NSObject, URLSessionDataDelegate, Sendable {
    private unowned let generator: LoadGenerator
    private let current = Mutex<(task: URLSessionTask?, continuation: CheckedContinuation<Bool, Never>?)>((nil, nil))
    /// Set when the server refused a request: the worker stops instead of asking again
    /// (app/bufferbloat.py returns from the worker on a non-200 reply).
    private let refused = Mutex(false)
    private let session = Mutex<URLSession?>(nil)

    init(generator: LoadGenerator) {
        self.generator = generator
        super.init()
        let configuration = generator.configuration ?? URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.httpMaximumConnectionsPerHost = 1
        configuration.waitsForConnectivity = false
        session.withLock { $0 = URLSession(configuration: configuration, delegate: self, delegateQueue: nil) }
    }

    /// Runs one request to completion or cancellation; false when the worker should stop.
    func transfer(_ request: URLRequest, body: Data?) async -> Bool {
        guard let session = session.withLock({ $0 }) else { return false }
        return await withCheckedContinuation { continuation in
            let task = body.map { session.uploadTask(with: request, from: $0) } ?? session.dataTask(with: request)
            current.withLock { $0 = (task, continuation) }
            task.resume()
            if generator.isStopped {   // stop() came just before this task was registered
                task.cancel()
            }
        }
    }

    func cancel() {
        current.withLock { $0.task }?.cancel()
    }

    func close() {
        session.withLock { session in
            session?.invalidateAndCancel()   // a URLSession keeps its delegate until invalidated
            session = nil
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        let http = response as? HTTPURLResponse
        let status = http?.statusCode ?? 0
        if dataTask.originalRequest?.httpMethod == "GET", status == 429 || status == 403 {
            refused.withLock { $0 = true }
            generator.refuse(.init(status: status,
                                   retryAfterS: BufferbloatTest.retryAfter(http?.value(forHTTPHeaderField: "Retry-After"))))
            completionHandler(.cancel)
            return
        }
        if dataTask.originalRequest?.httpMethod == "GET", status != 200 {
            generator.fail("HTTP \(status)")
            refused.withLock { $0 = true }
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if !generator.add(Int64(data.count)) {
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        if !generator.add(bytesSent) {
            task.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        var keepGoing = !refused.withLock { $0 }
        if let error {
            let cancelled = (error as? URLError)?.code == .cancelled
            if !cancelled {
                generator.fail(error.localizedDescription)
                keepGoing = false
            }
        }
        let continuation = current.withLock { current in
            defer { current = (nil, nil) }
            return current.continuation
        }
        continuation?.resume(returning: keepGoing)
    }
}
