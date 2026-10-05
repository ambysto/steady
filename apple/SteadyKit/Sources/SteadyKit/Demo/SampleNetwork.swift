#if DEBUG
import Foundation
import Synchronization

/// A made-up, healthy home network for the App Store screenshots (apple/AppStore/README.md):
/// the Simulator has no router, DNS or IPv4/IPv6 to show. Debug builds only, started by the
/// `-StoreScreenshots` launch argument; never in a release. Addresses come from the ranges kept
/// for documentation (RFC 5737), so no real network appears in a screenshot.
public enum SampleNetwork {
    public static let router = "192.0.2.1"

    public static var path: NetworkPath {
        NetworkPath(status: .connected, link: .wifi, supportsIPv4: true, supportsIPv6: true, supportsDNS: true,
                    gateways: [router], interfaces: ["en0"])
    }

    /// The Mac's Wi‑Fi reading: a good signal on 5 GHz.
    public static let wifi = WiFiSignal.State(state: "connected", channel: 44, radioType: "802.11ax",
                                              signal: WiFiSignal.State.quality(rssi: -52), rssi: -52, txMbps: 864)

    /// A monitor holding the hour before `now` and live samples, measured nowhere.
    @MainActor
    public static func monitor(now: Double, wifi: WiFiSignal.State?) -> LiveMonitor {
        let clock = Clock(time: now - 3600)
        let monitor = LiveMonitor(now: { clock.time.withLock { $0 } })
        monitor.routerAddress = router
        monitor.wifi = wifi
        var random = Random(seed: 7)
        for second in 0..<3600 {
            // Steady numbers with a little noise, and one busier stretch around minute 35.
            let busy = (2_040..<2_220).contains(second) ? 18.0 : 0
            var round: [(target: String, rttMs: Double?)] = [
                (LiveMonitor.router, 3 + random.next(2.5)),
                ("cloudflare", 18 + busy + random.next(6)),
                ("google", 21 + busy + random.next(6)),
            ]
            if second % 10 == 0 {
                round += [("tcp_cloudflare", 24 + busy + random.next(8)), ("tcp_google", 27 + busy + random.next(8))]
            }
            monitor.record(round)
            clock.time.withLock { $0 += 1 }
        }
        monitor.record([])   // closes the last minute
        return monitor
    }

    /// Check #6: the router's DNS in use, fast and error-free, next to the public resolvers.
    public static var dns: CheckResult {
        func server(_ address: String, _ base: Double) -> DNSBenchmark.Server {
            DNSBenchmark.Server(server: address, sent: 5, replies: 5, rtts: [base, base + 1.2, base + 0.4, base + 2.1, base + 0.8])
        }
        return DNSBenchmark.evaluate([server(router, 6.5), server("1.1.1.1", 19), server("8.8.8.8", 23), server("9.9.9.9", 26)],
                                     inUse: [router], roles: [router: .inUseRouter, "1.1.1.1": .public,
                                                              "8.8.8.8": .public, "9.9.9.9": .public])
    }

    /// Check #3 on the Mac: two quiet neighbours on other channels.
    public static var interference: CheckResult {
        let (connection, networks) = Interference.anonymous(
            current: Interference.Sighting(rssi: -52, channel: 44, band: "5 GHz"),
            around: [Interference.Sighting(rssi: -50, channel: 44, band: "5 GHz"),
                     Interference.Sighting(rssi: -74, channel: 36, band: "5 GHz"),
                     Interference.Sighting(rssi: -81, channel: 6, band: "2.4 GHz")])
        return Interference.evaluate(connection, scan: networks)
    }

    private final class Clock: Sendable {
        let time: Mutex<Double>
        init(time: Double) { self.time = Mutex(time) }
    }

    /// The same numbers every time, so screenshots can be retaken.
    private struct Random {
        var state: UInt64
        init(seed: UInt64) { state = seed }
        mutating func next(_ range: Double) -> Double {
            state = state &* 6364136223846793005 &+ 1442695040888963407
            return Double(state >> 11) / Double(1 << 53) * range
        }
    }
}
#endif
