import Foundation

/// Check #8 "VPN / virtual adapters" (docs/DIAGNOSTICS.md), a port of `evaluate_vpn` in
/// app/diagnostics.py. spec/diagnosis/vpn.json holds the cases both implementations must agree on.
public enum VPNCheck {
    /// A network adapter as Windows lists it (Get-NetAdapter).
    public struct Adapter: Equatable, Sendable, Decodable {
        public var name: String
        public var description: String
        /// "Up" when connected.
        public var status: String
        /// Apple platforms: a tunnel interface carrying traffic (utun, ipsec…), a VPN whatever its
        /// name. Windows adapters are recognised by name only.
        public var isTunnel: Bool

        public init(name: String, description: String = "", status: String, isTunnel: Bool = false) {
            self.name = name
            self.description = description
            self.status = status
            self.isTunnel = isTunnel
        }

        enum CodingKeys: String, CodingKey {
            case name = "Name", description = "InterfaceDescription", status = "Status"
        }

        public init(from decoder: any Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.init(name: try container.decode(String.self, forKey: .name),
                      description: try container.decode(String.self, forKey: .description),
                      status: try container.decode(String.self, forKey: .status))
        }
    }

    /// _VPN_RE in app/diagnostics.py.
    static let vendors = try! NSRegularExpression(
        pattern: #"VPN|WireGuard|Wintun|\bTAP\b|TAP-|OpenVPN|NetBird|Surfshark|Tailscale|ZeroTier|NordLynx|"#
            + #"Proton|WARP|Fortinet|Cisco AnyConnect|Cloudflare"#,
        options: .caseInsensitive)

    public static func evaluate(_ adapters: [Adapter]) -> CheckResult {
        let vpn = adapters.filter { $0.isTunnel || isVPN("\($0.name) \($0.description)") }
        let up = vpn.filter { $0.status == "Up" }
        let details: [MessageValue] = vpn.map {
            .text($0.description.isEmpty ? "\($0.name): \($0.status)" : "\($0.name) (\($0.description)): \($0.status)")
        }
        if !up.isEmpty {
            return CheckResult(id: 8, key: "vpn", status: .info,
                               summary: Message("diag.vpn.up", ["count": .number(Double(up.count))]),
                               details: details, advice: .message(Message("diag.vpn.advice")))
        }
        if !vpn.isEmpty {
            return CheckResult(id: 8, key: "vpn", status: .ok, summary: Message("diag.vpn.off"), details: details)
        }
        return CheckResult(id: 8, key: "vpn", status: .ok, summary: Message("diag.vpn.none"))
    }

    static func isVPN(_ text: String) -> Bool {
        vendors.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }
}
