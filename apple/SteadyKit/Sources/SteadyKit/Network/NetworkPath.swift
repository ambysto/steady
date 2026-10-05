import Foundation
import Network

/// What the system reports about the current route to the network (`NWPath`), reduced to plain
/// values so views and tests do not depend on the Network framework.
public struct NetworkPath: Equatable, Sendable {
    public enum Status: Sendable {
        case connected
        case disconnected
        /// Not usable yet, but the system can bring it up on demand (VPN on demand, cellular).
        case requiresConnection
    }

    public enum Link: Sendable {
        case wifi, wired, cellular, other
    }

    /// Why the path is not usable, when the system says.
    public enum Reason: Sendable {
        case notAvailable, cellularDenied, wifiDenied, localNetworkDenied, vpnInactive
    }

    public var status: Status
    public var link: Link?
    public var reason: Reason?
    /// Mobile data or a personal hotspot.
    public var isExpensive: Bool
    /// Low Data Mode is on.
    public var isConstrained: Bool
    public var supportsIPv4: Bool
    public var supportsIPv6: Bool
    public var supportsDNS: Bool
    /// Router addresses, as text.
    public var gateways: [String]
    /// Names of the interfaces the path can use, preferred first ("en0", "utun4"…).
    public var interfaces: [String]

    /// Changes when traffic takes another route: connected or not, the preferred interface
    /// ("en0" → "utun5" when a VPN starts), the router.
    public var route: String {
        "\(status)|\(interfaces.first ?? "")|\(routerIPv4 ?? "")"
    }

    /// The routers of the local network. Mobile data has none: its gateway is the carrier's,
    /// which does not answer pings and says nothing about the user's own network.
    public var routers: [String] {
        link == .cellular ? [] : gateways
    }

    /// The first IPv4 router address: what the monitor pings (ICMP is IPv4 only, as on Windows).
    public var routerIPv4: String? {
        routers.first { $0.contains(".") && !$0.contains(":") }
    }

    public init(status: Status, link: Link? = nil, reason: Reason? = nil, isExpensive: Bool = false,
                isConstrained: Bool = false, supportsIPv4: Bool = false, supportsIPv6: Bool = false,
                supportsDNS: Bool = false, gateways: [String] = [], interfaces: [String] = []) {
        self.status = status
        self.link = link
        self.reason = reason
        self.isExpensive = isExpensive
        self.isConstrained = isConstrained
        self.supportsIPv4 = supportsIPv4
        self.supportsIPv6 = supportsIPv6
        self.supportsDNS = supportsDNS
        self.gateways = gateways
        self.interfaces = interfaces
    }
}

extension NetworkPath {
    public init(_ path: NWPath) {
        let status: Status = switch path.status {
        case .satisfied: .connected
        case .requiresConnection: .requiresConnection
        default: .disconnected
        }
        let link: Link? = if path.usesInterfaceType(.wifi) { .wifi }
            else if path.usesInterfaceType(.wiredEthernet) { .wired }
            else if path.usesInterfaceType(.cellular) { .cellular }
            else if status == .disconnected { nil }
            else { .other }
        let reason: Reason? = if status == .connected { nil } else {
            switch path.unsatisfiedReason {
            case .cellularDenied: .cellularDenied
            case .wifiDenied: .wifiDenied
            case .localNetworkDenied: .localNetworkDenied
            case .vpnInactive: .vpnInactive
            case .notAvailable: .notAvailable
            @unknown default: .notAvailable
            }
        }
        self.init(status: status, link: link, reason: reason, isExpensive: path.isExpensive,
                  isConstrained: path.isConstrained, supportsIPv4: path.supportsIPv4,
                  supportsIPv6: path.supportsIPv6, supportsDNS: path.supportsDNS,
                  gateways: path.gateways.compactMap(Self.address),
                  interfaces: path.availableInterfaces.map(\.name))
    }

    private static func address(_ endpoint: NWEndpoint) -> String? {
        guard case .hostPort(let host, _) = endpoint else { return nil }
        return address(host)
    }

    /// "192.0.2.1", or "fe80::1%en0" with the scope once: the Host's own description repeats
    /// the interface name ("fe80::1%en0%en0").
    static func address(_ host: NWEndpoint.Host) -> String {
        switch host {
        case .ipv4(let address):
            return numeric(address.rawValue, family: AF_INET) ?? "\(address)"
        case .ipv6(let address):
            var bytes = address.rawValue
            if bytes.count == 16, bytes[0] == 0xFE, bytes[1] & 0xC0 == 0x80 {
                // fe80::/10: the kernel embeds the interface index in bytes 2–3 ("fe80:b::1");
                // those bits are zero on the wire, and the scope is shown as "%en0" instead.
                bytes[2] = 0
                bytes[3] = 0
            }
            let text = numeric(bytes, family: AF_INET6) ?? "\(address)".components(separatedBy: "%")[0]
            return address.interface.map { "\(text)%\($0.name)" } ?? text
        case .name(let name, _):
            return name
        @unknown default:
            return "\(host)"
        }
    }

    private static func numeric(_ bytes: Data, family: Int32) -> String? {
        var buffer = [CChar](repeating: 0, count: Int(INET6_ADDRSTRLEN))
        let ok = bytes.withUnsafeBytes { raw in
            inet_ntop(family, raw.baseAddress, &buffer, socklen_t(buffer.count)) != nil
        }
        return ok ? String(decoding: buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self) : nil
    }

    /// Path updates for as long as the caller keeps iterating; the first value arrives at once.
    public static func updates() -> AsyncStream<NetworkPath> {
        AsyncStream { continuation in
            let monitor = NWPathMonitor()
            monitor.pathUpdateHandler = { continuation.yield(NetworkPath($0)) }
            continuation.onTermination = { _ in monitor.cancel() }
            monitor.start(queue: DispatchQueue(label: "com.ambysto.steady.network-path"))
        }
    }
}

// MARK: - Text

extension NetworkPath {
    public var statusMessage: Message {
        switch status {
        case .connected: Message("ui.path.connected")
        case .disconnected: Message("ui.path.disconnected")
        case .requiresConnection: Message("ui.path.requires_connection")
        }
    }

    public var linkMessage: Message? {
        link.map { Message("ui.path.via." + $0.keySuffix) }
    }

    public var reasonMessage: Message? {
        reason.map { Message("ui.path.reason." + $0.keySuffix) }
    }

    /// "IPv4 · IPv6" (no words, so not translated) or the translated "None".
    public var ipVersions: MessageValue {
        let versions = [supportsIPv4 ? "IPv4" : nil, supportsIPv6 ? "IPv6" : nil].compactMap { $0 }
        return versions.isEmpty ? .message(Message("ui.path.none")) : .text(versions.joined(separator: " · "))
    }

    public var dnsMessage: Message {
        Message(supportsDNS ? "ui.path.available" : "ui.path.unavailable")
    }

    /// Conditions worth a note under the status: metered network, Low Data Mode, why it is down.
    public var notes: [Message] {
        [reason == .notAvailable ? nil : reasonMessage,   // "No connection" already says it
         isExpensive ? Message("ui.path.expensive") : nil,
         isConstrained ? Message("ui.path.constrained") : nil].compactMap { $0 }
    }
}

extension NetworkPath.Link {
    var keySuffix: String {
        switch self {
        case .wifi: "wifi"
        case .wired: "wired"
        case .cellular: "cellular"
        case .other: "other"
        }
    }
}

extension NetworkPath.Reason {
    var keySuffix: String {
        switch self {
        case .notAvailable: "not_available"
        case .cellularDenied: "cellular_denied"
        case .wifiDenied: "wifi_denied"
        case .localNetworkDenied: "local_network_denied"
        case .vpnInactive: "vpn_inactive"
        }
    }
}
