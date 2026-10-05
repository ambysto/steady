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
    /// The last route that reached the network.
    private var connectedRoute: String?
    public private(set) var minutes: [MinuteAggregator.Minute] = []
    /// Times the route changed (seconds since 1970) that can still touch the last hour's minutes.
    public private(set) var networkChanges: [Int] = []
    /// The Wi‑Fi interface as last read (the Mac, every few seconds); stored with each minute
    /// for check #13. Always nil on iPhone and iPad, which cannot read it.
    public var wifi: WiFiSignal.State? {
        didSet {
            if oldValue == nil, wifi != nil, physicalLink == nil { updatePhysicalLink() }
        }
    }
    /// Check #13 over the last 24 hours; nil where there is no Wi‑Fi data (iPhone, iPad).
    public private(set) var physicalLink: CheckResult?
    /// The Wi‑Fi rows of the last 24 hours when there is no store.
    private var wifiMinutes: [WiFiMinute] = []
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
            networkChanges = try store.networkChanges(since: Self.changesStart(now: Int(now())))
            updatePhysicalLink()
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
        networkChanges.removeAll()
        wifiMinutes.removeAll()
        updatePhysicalLink()
    }

    /// Starts the live numbers afresh when the route changes (a VPN turned on or off, another
    /// Wi‑Fi network, the connection lost): mixing samples from two routes shows loss and jitter
    /// that belong to neither. The first call only records the route.
    ///
    /// A switch from one working route to another is also kept, so check #5 leaves out the
    /// minutes around it (as on Windows). Losing the connection and getting the same route back
    /// is not a switch but an outage, which the check must still see: the app has no check #4.
    public func networkChanged(to path: NetworkPath) {
        let route = path.route
        if let previous = network, previous != route {
            samples.removeAll()
            Self.log.info("network changed: live samples cleared")
        }
        network = route
        guard path.status == .connected else { return }
        defer { connectedRoute = route }
        guard let previous = connectedRoute, previous != route else { return }
        let time = Int(now())
        networkChanges.append(time)
        networkChanges.removeAll { $0 < Self.changesStart(now: time) }
        do {
            try store?.addNetworkChange(at: time)
        } catch {
            Self.log.error("could not save a network change: \(String(describing: error), privacy: .public)")
        }
    }

    /// Changes earlier than this cannot leave out a minute of the last hour.
    private static func changesStart(now: Int) -> Int {
        now - historySeconds - 60 * PingQuality.changeMinutes
    }

    public func stats(for target: Target) -> LiveStats {
        LiveStats(samples: samples[target.id] ?? [])
    }

    /// Check #5 over the completed minutes of the last hour, as on Windows.
    public var pingQuality: CheckResult {
        let time = Int(now())
        return PingQuality.evaluate(hour: rows(since: time - 3600), recent: rows(since: time - 300),
                                    changes: networkChanges)
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
        if let wifi {
            let row = WiFiMinute(ts: minute.start, state: wifi.state, channel: wifi.channel,
                                 signal: wifi.signal.map(Double.init), rssi: wifi.rssi,
                                 rxMbps: wifi.rxMbps.map(Double.init), txMbps: wifi.txMbps.map(Double.init))
            wifiMinutes.append(row)
            wifiMinutes.removeAll { $0.ts < minute.start - PhysicalLink.historySeconds }
            do {
                try store?.insert(row)
            } catch {
                Self.log.error("could not save a Wi-Fi minute: \(String(describing: error), privacy: .public)")
            }
        }
        updatePhysicalLink()
    }

    /// Check #13 from the stored day (or what memory holds without a store); nil until there is
    /// any Wi‑Fi data.
    private func updatePhysicalLink() {
        let time = Int(now())
        let start = time - PhysicalLink.historySeconds
        do {
            let wifiRows = try store?.wifiMinutes(since: start) ?? wifiMinutes.filter { $0.ts >= start }
            // iPhone and iPad never have Wi‑Fi rows: no need to read the day's minutes.
            guard !wifiRows.isEmpty || wifi != nil else {
                physicalLink = nil
                return
            }
            let pingRows = try store.map { Self.rows(of: try $0.minutes(since: start)) } ?? rows(since: start)
            physicalLink = PhysicalLink.evaluate(PhysicalLink.join(wifi: wifiRows, ping: pingRows), now: time)
        } catch {
            Self.log.error("history unavailable for check #13: \(String(describing: error), privacy: .public)")
        }
    }

    private func rows(since start: Int) -> [PingQuality.Row] {
        Self.rows(of: minutes.filter { $0.start >= start })
    }

    /// The minutes' rows, each with its minute's start.
    private static func rows(of minutes: [MinuteAggregator.Minute]) -> [PingQuality.Row] {
        minutes.flatMap { minute in
            minute.rows.map { row in
                var row = row
                row.ts = minute.start
                return row
            }
        }
    }

    private func pingRound() async {
        // The router is still tried while refused, to notice when the permission is granted.
        let started = ContinuousClock.now
        let outcomes = await Self.ping(routerTarget + Self.internetTargets)
        guard Self.ranOnTime(ContinuousClock.now - started, timeout: Self.pingTimeout) else {
            Self.log.info("ping round dropped: the app was paused while measuring")
            return
        }
        record(refusals: outcomes)
    }

    /// A round that took longer than its timeout ran while iOS had paused the app (in the
    /// background, or the device locked): its round-trip times include the pause and its
    /// timeouts are not losses, so it is dropped rather than counted.
    nonisolated static func ranOnTime(_ elapsed: Duration, timeout: Duration) -> Bool {
        elapsed <= timeout + .milliseconds(500)
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
        let started = ContinuousClock.now
        let results = await Self.measure(Self.probeTargets)
        guard Self.ranOnTime(ContinuousClock.now - started, timeout: Self.probeTimeout) else {
            Self.log.info("probe round dropped: the app was paused while measuring")
            return
        }
        record(results)
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
