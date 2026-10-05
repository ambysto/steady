import Foundation
import Observation
import os

/// Measures the router and the Internet while the app is open, with the Windows monitor's
/// defaults (app/config.py): ICMP every second with a 900 ms timeout to the router, 1.1.1.1 and
/// 8.8.8.8; a TCP handshake to port 443 of both every 10 seconds with a 3 s timeout. iOS gives
/// apps no continuous background time, so measuring stops when `run()` is cancelled or the app
/// is suspended. Closed minutes go to the `MinuteStore` (ADR-0011), so the last hour survives
/// the app being closed.
@MainActor
@Observable
public final class LiveMonitor {
    public struct Target: Identifiable, Hashable, Sendable {
        public enum Kind: Sendable { case icmp, tcp }

        /// Same names as the Windows monitor's `target` column ("router", "cloudflare", "tcp_cloudflare"...).
        public let id: String
        public let address: String
        public let kind: Kind
        public let port: UInt16

        public init(id: String, address: String, kind: Kind = .icmp, port: UInt16 = 0) {
            self.id = id
            self.address = address
            self.kind = kind
            self.port = port
        }

        /// "1.1.1.1" or "1.1.1.1:443".
        public var endpoint: String { kind == .tcp ? "\(address):\(port)" : address }
    }

    public nonisolated static let router = PingQuality.router
    public nonisolated static let internetTargets = [Target(id: "cloudflare", address: "1.1.1.1"),
                                         Target(id: "google", address: "8.8.8.8")]
    public nonisolated static let probeTargets = [Target(id: "tcp_cloudflare", address: "1.1.1.1", kind: .tcp, port: 443),
                                      Target(id: "tcp_google", address: "8.8.8.8", kind: .tcp, port: 443)]
    static let pingInterval = Duration.seconds(1)
    static let pingTimeout = Duration.milliseconds(900)
    static let probeInterval = Duration.seconds(10)
    static let probeTimeout = Duration.seconds(3)
    static let liveWindow = 60
    static let historySeconds = 3600

    /// IPv4 address of the router, from the network path; nil (not pinged) when unknown.
    public var routerAddress: String? {
        didSet {
            if routerAddress != oldValue {
                samples[Self.router] = nil   // a different router: its old numbers no longer apply
            }
        }
    }

    public private(set) var samples: [String: [Double?]] = [:]
    /// The system refuses to send to the router: the local network permission was declined.
    /// The router is then left out (not counted as lost) until a ping gets through again.
    public private(set) var routerRefused = false
    private var network: String?
    public private(set) var minutes: [MinuteAggregator.Minute] = []
    private var aggregator = MinuteAggregator()
    /// One line per closed minute and target (`log stream --predicate 'subsystem == "com.ambysto.steady"'`):
    /// target names and counts only, never addresses.
    private static let log = Logger(subsystem: "com.ambysto.steady", category: "measurement")
    private let now: @Sendable () -> Double
    private let store: MinuteStore?

    /// - Parameter store: where minutes are kept across launches; nil keeps them in memory only.
    public init(store: MinuteStore? = nil, now: @escaping @Sendable () -> Double = { Date().timeIntervalSince1970 }) {
        self.store = store
        self.now = now
        guard let store else { return }
        do {
            try store.purge(now: now())
            minutes = try store.minutes(since: Int(now()) - Self.historySeconds)
            Self.log.info("history: \(self.minutes.count, privacy: .public) minutes of the last hour loaded")
        } catch {
            Self.log.error("history unavailable: \(String(describing: error), privacy: .public)")
        }
    }

    /// What the screen lists: the router only while it can be measured.
    public var targets: [Target] {
        (routerRefused ? [] : routerTarget) + Self.internetTargets + Self.probeTargets
    }

    private var routerTarget: [Target] {
        routerAddress.map { [Target(id: Self.router, address: $0)] } ?? []
    }

    /// Minutes stored on the device, or nil without a store.
    public var storedMinutes: Int? {
        try? store?.minuteCount()
    }

    /// Minutes of the last `seconds` from the store (the History screen); the in-memory hour
    /// when there is no store.
    public func history(seconds: Int) -> [MinuteAggregator.Minute] {
        let start = Int(now()) - seconds
        guard let store, let stored = try? store.minutes(since: start) else {
            return minutes.filter { $0.start >= start }
        }
        return stored
    }

    /// Settings > Delete history: the stored minutes and the hour kept in memory.
    public func deleteHistory() throws {
        try store?.deleteAll()
        minutes.removeAll()
    }

    /// Starts the live numbers afresh when the network changes (a VPN turned on or off, another
    /// Wi‑Fi network): mixing samples from two routes shows loss and jitter that belong to
    /// neither. `network` identifies the route; the first call only records it. The per-minute
    /// history, and so check #5, is not affected.
    public func networkChanged(to network: String) {
        defer { self.network = network }
        guard let previous = self.network, previous != network else { return }
        samples.removeAll()
        Self.log.info("network changed: live samples cleared")
    }

    public func stats(for target: Target) -> LiveStats {
        LiveStats(samples: samples[target.id] ?? [])
    }

    /// Check #5 over the completed minutes of the last hour, as on Windows.
    public var pingQuality: CheckResult {
        let time = Int(now())
        return PingQuality.evaluate(hour: rows(since: time - 3600), recent: rows(since: time - 300))
    }

    /// Measures until the calling task is cancelled, then keeps the partial minute.
    public func run() async {
        await withDiscardingTaskGroup { group in
            group.addTask { await self.repeatEvery(Self.pingInterval) { await self.pingRound() } }
            group.addTask { await self.repeatEvery(Self.probeInterval) { await self.probeRound() } }
        }
        if let partial = aggregator.flush() {
            keep(partial)
        }
    }

    /// Records one round of results: closes the minute when it has passed, then adds the samples.
    func record(_ results: [(target: String, rttMs: Double?)]) {
        if let minute = aggregator.roll(at: now()) {
            keep(minute)
        }
        for result in results {
            aggregator.add(result.target, rttMs: result.rttMs)
            var window = samples[result.target] ?? []
            window.append(result.rttMs)
            samples[result.target] = Array(window.suffix(Self.liveWindow))
        }
    }

    private func keep(_ minute: MinuteAggregator.Minute) {
        for row in minute.rows {
            Self.log.info("minute \(minute.start, privacy: .public) \(row.target, privacy: .public): sent \(row.sent, privacy: .public), lost \(row.lost, privacy: .public), jitter \(row.jitter ?? -1, privacy: .public)")
        }
        minutes.append(minute)
        minutes.removeAll { $0.start < minute.start - Self.historySeconds }
        do {
            try store?.insert(minute)
        } catch {
            Self.log.error("could not save a minute: \(String(describing: error), privacy: .public)")
        }
    }

    private func rows(since start: Int) -> [PingQuality.Row] {
        minutes.filter { $0.start >= start }.flatMap(\.rows)
    }

    private func pingRound() async {
        // The router is still tried while refused, to notice when the permission is granted.
        let outcomes = await Self.ping(routerTarget + Self.internetTargets)
        record(refusals: outcomes)
    }

    /// Records a ping round; a refused router is noted, not counted as a lost ping.
    func record(refusals outcomes: [(target: String, outcome: ICMPPing.Outcome)]) {
        if let router = outcomes.first(where: { $0.target == Self.router }) {
            routerRefused = router.outcome == .refused
        }
        record(outcomes.filter { $0.outcome != .refused }.map { ($0.target, $0.outcome.rttMs) })
    }

    private nonisolated static func ping(_ targets: [Target]) async -> [(target: String, outcome: ICMPPing.Outcome)] {
        await withTaskGroup(of: (target: String, outcome: ICMPPing.Outcome).self) { group in
            for target in targets {
                group.addTask { (target.id, await ICMPPing.outcome(target.address, timeout: pingTimeout)) }
            }
            var results: [(target: String, outcome: ICMPPing.Outcome)] = []
            for await result in group {
                results.append(result)
            }
            return targets.compactMap { target in results.first { $0.target == target.id } }
        }
    }

    private func probeRound() async {
        record(await Self.measure(Self.probeTargets))
    }

    private nonisolated static func measure(_ targets: [Target]) async -> [(target: String, rttMs: Double?)] {
        await withTaskGroup(of: (target: String, rttMs: Double?).self) { group in
            for target in targets {
                group.addTask {
                    switch target.kind {
                    case .icmp: (target.id, await ICMPPing.ping(target.address, timeout: pingTimeout))
                    case .tcp: (target.id, await TCPProbe.connect(host: target.address, port: target.port, timeout: probeTimeout))
                    }
                }
            }
            var results: [(target: String, rttMs: Double?)] = []
            for await result in group {
                results.append(result)
            }
            return targets.compactMap { target in results.first { $0.target == target.id } }
        }
    }

    private func repeatEvery(_ interval: Duration, _ body: () async -> Void) async {
        let clock = ContinuousClock()
        var next = clock.now
        while !Task.isCancelled {
            await body()
            next += interval
            if next < clock.now - interval * 5 {   // after the app was suspended: no burst to catch up
                next = clock.now
            }
            try? await Task.sleep(until: next, clock: clock)
        }
    }
}
