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

    /// The checks that have a result, in the order of docs/DIAGNOSTICS.md.
    var checks: [CheckResult] {
        [signal, interference, monitor.pingQuality, vpn, dns, monitor.physicalLink, bufferbloat]
            .compactMap { $0 }.sorted { $0.id < $1.id }
    }

    var isConnected: Bool { path?.status == .connected }

    init() {
        #if DEBUG
        // `-StoreScreenshots [-StoreTab diagnostics]`: the made-up network of apple/AppStore.
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("-StoreScreenshots") {
            isStoreScreenshots = true
            #if os(macOS)
            monitor = SampleNetwork.monitor(now: Date().timeIntervalSince1970, wifi: SampleNetwork.wifi)
            signal = WiFiSignal.evaluate(SampleNetwork.wifi)
            interference = SampleNetwork.interference
            #else
            monitor = SampleNetwork.monitor(now: Date().timeIntervalSince1970, wifi: nil)
            #endif
            path = SampleNetwork.path
            vpn = VPNCheck.evaluate([])
            dns = SampleNetwork.dns
            if let index = arguments.firstIndex(of: "-StoreTab"), arguments.indices.contains(index + 1) {
                selectedTab = AppTab(rawValue: arguments[index + 1]) ?? .overview
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
            #endif
        }
    }

    func runDNS() {
        dnsTask?.cancel()
        dnsTask = Task { await measureDNS() }
    }

    func runBufferbloat() {
        guard bufferbloatTask == nil else { return }
        bufferbloatTask = Task {
            bufferbloatStage = .idle
            let measurement = await BufferbloatTest.run(router: path?.routerIPv4) { stage in
                await MainActor.run { self.bufferbloatStage = stage }
            }
            bufferbloat = Bufferbloat.evaluate(measurement)
            bufferbloatStage = nil
            bufferbloatTask = nil
        }
    }

    private func followPath() async {
        for await update in NetworkPath.updates() {
            path = update
            monitor.networkChanged(to: update)
            monitor.routerAddress = update.routerIPv4
            // Turning a VPN on or off changes the path, so check #8 follows it.
            let adapters = VPNReader.adapters(pathInterfaces: update.interfaces)
            vpn = VPNCheck.evaluate(adapters)
            vpnUp = adapters.contains { $0.isTunnel && $0.status == "Up" }
            // Check #6 runs once connected, and again when the router changes.
            let trigger = update.status == .connected ? (update.routerIPv4 ?? "-") : nil
            if trigger != dnsTrigger {
                dnsTrigger = trigger
                if trigger != nil { runDNS() }
            }
        }
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
                let seen = await Task.detached { WiFiReader.surroundings(allowScan: true) }.value
                if let seen {
                    let (connection, networks) = Interference.anonymous(current: seen.current, around: seen.around)
                    interference = Interference.evaluate(connection, scan: networks)
                } else {
                    interference = Interference.evaluate(nil, scan: [])
                }
            }
            try? await Task.sleep(for: .seconds(5))
        }
    }
    #endif
}
