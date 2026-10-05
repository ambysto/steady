import CFNetwork
import Foundation

/// Finds VPN tunnels on iOS and macOS, where apps cannot list network adapters. The system keeps
/// several utun interfaces of its own (iCloud, AirDrop…) whether or not a VPN runs, so an
/// interface counts only when it carries traffic: it has scoped network settings, or it is the
/// interface the current path uses.
public enum VPNReader {
    static let tunnelPrefixes = ["utun", "ipsec", "ppp", "tap", "tun"]

    /// - Parameter pathInterfaces: names of the interfaces the current network path can use,
    ///   preferred first.
    public static func adapters(pathInterfaces: [String]) -> [VPNCheck.Adapter] {
        var names = scopedInterfaces().filter(isTunnel)
        // Only the interface the path prefers carries the traffic; the others are merely available.
        if let preferred = pathInterfaces.first, isTunnel(preferred), !names.contains(preferred) {
            names.append(preferred)
        }
        return names.map { VPNCheck.Adapter(name: $0, status: "Up", isTunnel: true) }
    }

    static func isTunnel(_ name: String) -> Bool {
        tunnelPrefixes.contains { name.hasPrefix($0) }
    }

    /// Interfaces with their own network settings ("__SCOPED__" in the system proxy settings):
    /// the active network and any connected VPN.
    static func scopedInterfaces() -> [String] {
        guard let settings = CFNetworkCopySystemProxySettings()?.takeRetainedValue() as? [String: Any],
              let scoped = settings["__SCOPED__"] as? [String: Any] else { return [] }
        return scoped.keys.sorted()
    }
}
