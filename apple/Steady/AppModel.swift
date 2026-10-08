import SteadyKit
import SwiftUI

enum AppTab: String, Hashable {
    case overview, diagnostics, history, settings
}

/// Everything the tabs share: the network path, the live monitor and the checks. It lives for
/// the whole app, so measuring goes on whichever tab is shown.
@MainActor
@Observable
final class AppModel {
    /// Opened once for the app's lifetime.
    private static let history = try? MinuteStore.standard()

    var selectedTab: AppTab = .overview
    /// The History tab's range; the Overview's week card opens it at a week.
    var historyRange: HistorySeries.Range = .hour
    private(set) var path: NetworkPath?
    let monitor: LiveMonitor
    /// App Store screenshots: a sample network instead of measurements (debug builds only).
    private let isStoreScreenshots: Bool
    private(set) var vpn: CheckResult?
    /// A tunnel interface is up: a VPN, which may keep the router out of reach.
    private(set) var vpnUp = false
    /// Checks #2 and #3: on the Mac only (CoreWLAN).
    private(set) var signal: CheckResult?
    private(set) var interference: CheckResult?
    private(set) var dns: CheckResult?
    private(set) var dnsRunning = false
    private(set) var bufferbloat: CheckResult?
    private(set) var bufferbloatStage: BufferbloatTest.Stage?

    /// Only the latest DNS run may show its result: the router can change while one is running.
    private var dnsRun = 0
    private var dnsTask: Task<Void, Never>?
    private var dnsTrigger: String?
    private var bufferbloatTask: Task<Void, Never>?
    /// The Mac and the iPad may open several windows: measuring goes on while any of them is open.
    private var windows = 0
    private var measuring: Task<Void, Never>?

    /// The Overview's "Check my connection" (ADR-0020): the latest run, kept while the app is open.
    private(set) var checkRun: CheckRun?
    private var checkTask: Task<Void, Never>?

    #if os(macOS)
    /// The floating monitor (ADR-0022): a window of its own on top of the others. Closed by default,
    /// and it starts as the bar each time it opens.
    private(set) var floatingShown = false
    var floatingExpanded = false {
        didSet { floatingController?.setExpanded(floatingExpanded) }
    }
    var floatingLook = FloatingLook(rawValue: UserDefaults.standard.string(forKey: "floating.look") ?? "") ?? .glass {
        didSet {
            UserDefaults.standard.set(floatingLook.rawValue, forKey: "floating.look")
            floatingController?.applyLook(floatingLook)
        }
    }
    var floatingTheme = FloatingTheme(rawValue: UserDefaults.standard.string(forKey: "floating.theme") ?? "") ?? .system {
        didSet { UserDefaults.standard.set(floatingTheme.rawValue, forKey: "floating.theme") }
    }
    /// Download and upload over the last two minutes, sampled while the monitor is open.
    private(set) var throughput = Throughput()
    /// The Mac's address on the path's interface and its DNS servers, refreshed every 10 s while open.
    private(set) var localIPv4: String?
    private(set) var dnsServers: [String] = []
    private var floatingController: FloatingMonitorController?
    #endif

    /// The checks the run goes through on this platform, in the order of docs/DIAGNOSTICS.md.
    /// Bufferbloat (#14) loads the line, so it stays on the Diagnostics tab.
    static var checkSteps: [CheckRun.Step] {
        #if os(macOS)
        [.init(id: 2, key: "signal"), .init(id: 3, key: "interference"), .init(id: 5, key: "ping"),
         .init(id: 6, key: "dns"), .init(id: 8, key: "vpn"), .init(id: 13, key: "physical_link")]
        #else
        [.init(id: 5, key: "ping"), .init(id: 6, key: "dns"), .init(id: 8, key: "vpn")]
        #endif
    }

    /// The checks that have a result, in the order of docs/DIAGNOSTICS.md.
    var checks: [CheckResult] {
        [signal, interference, monitor.pingQuality, vpn, dns, monitor.physicalLink, bufferbloat]
            .compactMap { $0 }.sorted { $0.id < $1.id }
    }

    var isConnected: Bool { path?.status == .connected }

    init() {
        #if DEBUG
        // `-StoreScreenshots [-StoreTab diagnostics] [-SampleProblems] [-RunCheck [-StaleCheck]]`: the made-up
        // network of apple/AppStore; with `-SampleProblems` it has a fair signal (Mac), failing DNS
        // and loss past the router, and `-RunCheck` runs Check my connection on it at launch.
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("-StoreScreenshots") {
            isStoreScreenshots = true
            let problems = arguments.contains("-SampleProblems")
            let now = Date().timeIntervalSince1970
            #if os(macOS)
            let wifi = problems ? SampleNetwork.fairWiFi : SampleNetwork.wifi
            monitor = SampleNetwork.monitor(now: now, wifi: wifi, internetLoss: problems)
            signal = WiFiSignal.evaluate(wifi)
            interference = SampleNetwork.interference
            #else
            monitor = SampleNetwork.monitor(now: now, wifi: nil, internetLoss: problems)
            #endif
            path = SampleNetwork.path
            vpn = VPNCheck.evaluate([])
            dns = problems ? SampleNetwork.brokenDNS : SampleNetwork.dns
            if let index = arguments.firstIndex(of: "-StoreTab"), arguments.indices.contains(index + 1) {
                selectedTab = AppTab(rawValue: arguments[index + 1]) ?? .overview
            }
            if arguments.contains("-RunCheck") {
                // `-StaleCheck`: as if that run had ended 3 h 25 min ago, for the stale state.
                runCheck(endedAgo: arguments.contains("-StaleCheck") ? 3 * 3600 + 25 * 60 : 0)
            }
            return
        }
        #endif
        isStoreScreenshots = false
        monitor = LiveMonitor(store: Self.history)
    }

    /// Called by each window for as long as it is open; measures while at least one is.
    func run() async {
        guard !isStoreScreenshots else { return }
        windows += 1
        if measuring == nil {
            measuring = Task { await measure() }
        }
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(3600))
        }
        windows -= 1
        if windows == 0 {
            measuring?.cancel()
            measuring = nil
        }
    }

    private func measure() async {
        await withDiscardingTaskGroup { group in
            group.addTask { await self.followPath() }
            group.addTask { await self.monitor.run() }
            #if os(macOS)
            group.addTask { await self.readSignal() }
            group.addTask { await self.readThroughput() }
            #endif
        }
    }

    #if os(macOS)
    /// Opens the floating monitor (ADR-0022). Its view keeps the measuring going while it is shown.
    func showFloatingMonitor() {
        floatingShown = true
        let controller = floatingController ?? FloatingMonitorController(model: self)
        floatingController = controller
        controller.show(expanded: floatingExpanded, look: floatingLook)
    }

    /// Closes it: its view goes, so the measuring it kept going stops with it.
    func hideFloatingMonitor() {
        floatingController?.hide()
        floatingShown = false
        floatingExpanded = false
    }

    func toggleFloatingMonitor() {
        if floatingShown {
            hideFloatingMonitor()
        } else {
            showFloatingMonitor()
        }
    }

    /// Solid while the pointer is on the floating monitor, see-through otherwise for Low and High.
    func floatingPointer(over: Bool) {
        floatingController?.pointerOver(over)
    }

    /// Samples the download and upload once a second while the floating monitor is open, and the
    /// Mac's address and DNS servers every 10 s. Nothing is read while it is closed.
    private func readThroughput() async {
        var lastFacts: ContinuousClock.Instant?
        while !Task.isCancelled {
            if floatingShown, let interface = path?.interfaces.first {
                if let counters = Throughput.counters(of: interface) {
                    throughput.add(ts: Date().timeIntervalSince1970, interface: interface,
                                   rx: counters.rx, tx: counters.tx)
                }
                if lastFacts.map({ $0.duration(to: .now) >= .seconds(10) }) ?? true {
                    lastFacts = .now
                    localIPv4 = Throughput.ipv4(of: interface)
                    dnsServers = DNSProbe.systemServers()
                }
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }
    #endif

    func runDNS() {
        dnsTask?.cancel()
        dnsTask = Task { await measureDNS() }
    }

    /// The byte limit check #14 would use on the current path; nil in Low Data Mode.
    var bufferbloatLimit: Int64? {
        BufferbloatTest.byteLimit(expensive: path?.isExpensive ?? false, constrained: path?.isConstrained ?? false)
    }

    /// The current path is metered (mobile data, a personal hotspot): check #14 uses a lower limit.
    var bufferbloatMetered: Bool { path?.isExpensive ?? false }

    func runBufferbloat() {
        guard bufferbloatTask == nil, let limit = bufferbloatLimit else { return }
        bufferbloatTask = Task {
            bufferbloatStage = .idle
            let measurement = await BufferbloatTest.run(router: path?.routerIPv4, maxBytes: limit) { stage in
                await MainActor.run { self.bufferbloatStage = stage }
            }
            bufferbloat = Bufferbloat.evaluate(measurement)
            bufferbloatStage = nil
            bufferbloatTask = nil
        }
    }

    /// Runs the checks one after another and records each as it really starts and finishes: no
    /// minimum duration, so a check that only reads what the app already measured is instant.
    func runCheck() {
        runCheck(endedAgo: 0)
    }

    private func runCheck(endedAgo: TimeInterval) {
        guard checkTask == nil else { return }
        checkTask = Task {
            checkRun = CheckRun(steps: Self.checkSteps)
            for step in Self.checkSteps {
                checkRun?.current = step.id
                if let result = await evaluate(step) {
                    checkRun?.results.append(result)
                }
                checkRun?.done += 1
            }
            checkRun?.current = nil
            checkRun?.measured = measuredNow()
            checkRun?.finishedAt = .now - endedAgo
            checkTask = nil
        }
    }

    /// The numbers behind a result with nothing to fix, from the live measurements.
    private func measuredNow() -> CheckRun.Measured {
        let router = monitor.targets.first { $0.id == LiveMonitor.router }.map { monitor.stats(for: $0) }
        let internet = LiveMonitor.internetTargets.first.map { monitor.stats(for: $0) }
        return CheckRun.Measured(routerMs: router?.medianRTT, internetMs: internet?.medianRTT,
                                 internetLossPercent: internet.flatMap { $0.isEmpty ? nil : $0.lossPercent })
    }

    /// One check of the run. The live checks the other tabs show are refreshed with it.
    private func evaluate(_ step: CheckRun.Step) async -> CheckResult? {
        switch step.id {
        #if os(macOS)
        case 2:
            if !isStoreScreenshots {
                monitor.wifi = WiFiReader.current()
                signal = WiFiSignal.evaluate(monitor.wifi)
            }
            return signal
        case 3:
            if !isStoreScreenshots {
                interference = await lookAround()
            }
            return interference
        case 13:
            return monitor.physicalLink
        #endif
        case 5:
            return monitor.pingQuality
        case 6:
            if !isStoreScreenshots {
                dnsTask?.cancel()   // this run measures DNS itself
                await measureDNS()
            }
            return dns
        case 8:
            if !isStoreScreenshots, let path {
                readVPN(path)
            }
            return vpn
        default:
            return nil
        }
    }

    private func followPath() async {
        for await update in NetworkPath.updates() {
            path = update
            monitor.networkChanged(to: update)
            monitor.routerAddress = update.routerIPv4
            // Turning a VPN on or off changes the path, so check #8 follows it.
            readVPN(update)
            // Check #6 runs once connected, and again when the router changes.
            let trigger = update.status == .connected ? (update.routerIPv4 ?? "-") : nil
            if trigger != dnsTrigger {
                dnsTrigger = trigger
                if trigger != nil { runDNS() }
            }
        }
    }

    private func readVPN(_ path: NetworkPath) {
        let adapters = VPNReader.adapters(pathInterfaces: path.interfaces)
        vpn = VPNCheck.evaluate(adapters)
        vpnUp = adapters.contains { $0.isTunnel && $0.status == "Up" }
    }

    private func measureDNS() async {
        dnsRun += 1
        let run = dnsRun
        dnsRunning = true
        let result = await DNSCheck.run(router: path?.routerIPv4)
        guard run == dnsRun else { return }   // a newer run owns the card
        dnsRunning = false
        if !Task.isCancelled {
            dns = result
        }
    }

    #if os(macOS)
    /// Re-reads the Wi‑Fi signal every 5 s, like the Windows monitor; the readings also go with
    /// each stored minute for check #13. Check #3 looks at the networks around every 5 minutes
    /// and when the channel changes.
    private func readSignal() async {
        var lastLook: ContinuousClock.Instant?
        var lastChannel: Int?
        while !Task.isCancelled {
            let state = WiFiReader.current()
            signal = WiFiSignal.evaluate(state)
            monitor.wifi = state
            if lastLook.map({ $0.duration(to: .now) >= .seconds(300) }) ?? true || state?.channel != lastChannel {
                lastLook = .now
                lastChannel = state?.channel
                interference = await lookAround()
            }
            try? await Task.sleep(for: .seconds(5))
        }
    }

    /// Check #3 from the networks around (the system's cached scan, see WiFiReader).
    private func lookAround() async -> CheckResult {
        guard let seen = await Task.detached(operation: { WiFiReader.surroundings(allowScan: true) }).value else {
            return Interference.evaluate(nil, scan: [])
        }
        let (connection, networks) = Interference.anonymous(current: seen.current, around: seen.around)
        return Interference.evaluate(connection, scan: networks)
    }
    #endif
}

/// One run of "Check my connection": the steps, the results as they come in, and when it ended.
struct CheckRun {
    struct Step: Identifiable {
        let id: Int
        let key: String

        var title: Message { Message("diag.\(key).title") }
    }

    let steps: [Step]
    /// In the order the checks finished; a check with nothing to report (no reading) is absent.
    var results: [CheckResult] = []
    /// The check running now, and how many have finished.
    var current: Int?
    var done = 0
    var finishedAt: Date?

    /// Median latency and Internet loss over the live window when the run ended.
    struct Measured {
        var routerMs: Double?
        var internetMs: Double?
        var internetLossPercent: Double?
    }

    var measured = Measured()

    var isRunning: Bool { finishedAt == nil }
    var problems: [CheckFlow.Problem] { CheckFlow.problems(results) }
    var okCount: Int { CheckFlow.okCount(results) }

    func result(of step: Step) -> CheckResult? {
        results.first { $0.id == step.id }
    }
}
