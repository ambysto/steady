import Foundation
import Synchronization

/// Measures check #14 like app/bufferbloat.py: pings while idle, then while downloading, then
/// while uploading. Generates real traffic (up to ~200 MB), so it runs only when the user asks.
public enum BufferbloatTest {
    public enum Stage: Sendable {
        case idle, download, upload
    }

    static let downloadURL = URL(string: "https://speed.cloudflare.com/__down?bytes=25000000")!
    static let uploadURL = URL(string: "https://speed.cloudflare.com/__up")!
    static let idleSeconds = 4.0, loadSeconds = 10.0, interval = 0.2
    static let rampSeconds = 2.0   // samples taken while the line is still picking up speed are dropped
    static let connections = 4
    static let maxBytes: Int64 = 100_000_000
    /// speed.cloudflare.com refuses a single request above ~25 MB, so workers repeat 25 MB requests.
    static let bytesPerRequest = 25_000_000

    /// - Parameters:
    ///   - router: IPv4 address of the router, pinged alongside the Internet when known.
    ///   - stage: called as each phase starts.
    public static func run(router: String?, stage: @escaping @Sendable (Stage) async -> Void = { _ in }) async
        -> Bufferbloat.Measurement {
        var targets: [(label: String, address: String)] = []
        if let router { targets.append(("router", router)) }
        targets.append(("internet", "1.1.1.1"))

        await stage(.idle)
        let idle = Bufferbloat.Phase(rtts: await sample(targets, seconds: idleSeconds))
        await stage(.download)
        let download = await loaded(targets, upload: false)
        await stage(.upload)
        let upload = await loaded(targets, upload: true)
        return Bufferbloat.Measurement(idle: idle, download: download, upload: upload)
    }

    /// Pings every target once per `interval` for `seconds`, the targets in parallel.
    static func sample(_ targets: [(label: String, address: String)], seconds: Double) async -> [Bufferbloat.Samples] {
        var samples = targets.map { Bufferbloat.Samples(label: $0.label, samples: []) }
        let clock = ContinuousClock()
        let end = clock.now + .seconds(seconds)
        while clock.now < end, !Task.isCancelled {
            let started = clock.now
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
        return samples
    }

    static func loaded(_ targets: [(label: String, address: String)], upload: Bool) async -> Bufferbloat.Phase {
        let load = LoadGenerator(upload: upload)
        let clock = ContinuousClock()
        let started = clock.now
        let loadTask = Task { await load.run(seconds: loadSeconds) }
        let samples = await sample(targets, seconds: loadSeconds)
        load.stop()
        let outcome = await loadTask.value
        let elapsed = max((clock.now - started).inMilliseconds / 1000, 1e-6)
        let drop = Int(rampSeconds / interval)
        let kept = samples.map { Bufferbloat.Samples(label: $0.label, samples: $0.samples.count > drop ? Array($0.samples.dropFirst(drop)) : []) }
        return Bufferbloat.Phase(rtts: kept, mbps: Double(outcome.bytes) * 8 / elapsed / 1e6, error: outcome.error ?? "")
    }
}

/// Keeps the line busy over several connections (app/bufferbloat.py http_download / http_upload):
/// each worker has its own URLSession, so each is its own TCP connection, and repeats 25 MB
/// requests until the deadline, `stop()` or 100 MB in total.
final class LoadGenerator: Sendable {
    struct Outcome: Sendable {
        let bytes: Int64
        let error: String?
    }

    private let upload: Bool
    private let state = Mutex(State())

    private struct State {
        var bytes: Int64 = 0
        var errors: [String] = []
        var stopped = false
        var workers: [LoadWorker] = []
    }

    init(upload: Bool) {
        self.upload = upload
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
        return state.withLock { Outcome(bytes: $0.bytes, error: $0.bytes == 0 ? $0.errors.first : nil) }
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
            return state.bytes < BufferbloatTest.maxBytes
        }
    }

    func fail(_ message: String) {
        state.withLock { state in
            if !state.stopped { state.errors.append(message) }
        }
    }

    private func isDone(deadline: ContinuousClock.Instant) -> Bool {
        ContinuousClock.now >= deadline || state.withLock { $0.stopped || $0.bytes >= BufferbloatTest.maxBytes }
    }

    private static let body = Data(count: BufferbloatTest.bytesPerRequest)
    private static let userAgent = "AmbystoSteady/"
        + ((Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0")

    private func request() -> URLRequest {
        var request = URLRequest(url: upload ? BufferbloatTest.uploadURL : BufferbloatTest.downloadURL,
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
    private let session = Mutex<URLSession?>(nil)

    init(generator: LoadGenerator) {
        self.generator = generator
        super.init()
        let configuration = URLSessionConfiguration.ephemeral
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
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        if dataTask.originalRequest?.httpMethod == "GET", status != 200 {
            generator.fail("HTTP \(status)")
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
        var keepGoing = true
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
