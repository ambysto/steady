import Foundation

/// Check #3 "Channel interference" (docs/DIAGNOSTICS.md), a port of `evaluate_interference` in
/// app/diagnostics.py. Only macOS can see the networks around (CoreWLAN), and without location
/// permission it gives no BSSIDs: `anonymous(current:around:)` stands in for them.
/// spec/diagnosis/interference.json holds the cases both implementations must agree on.
public enum Interference {
    /// The connection, as much of app/winutil.py WifiState as the rule reads.
    public struct Connection: Equatable, Sendable, Decodable {
        public var state: String
        public var bssid: String
        public var channel: Int?

        public init(state: String, bssid: String, channel: Int?) {
            self.state = state
            self.bssid = bssid
            self.channel = channel
        }

        var isConnected: Bool { state.lowercased() == "connected" }
    }

    /// One BSSID seen around, as app/winutil.py ScanEntry.
    public struct Network: Equatable, Sendable, Decodable {
        /// Empty for a hidden network (and on the Mac, which does not read names).
        public var ssid: String
        public var bssid: String
        /// Signal quality in percent, as Windows reports it.
        public var signal: Int?
        public var band: String
        public var channel: Int?

        public init(ssid: String, bssid: String, signal: Int?, band: String, channel: Int?) {
            self.ssid = ssid
            self.bssid = bssid
            self.signal = signal
            self.band = band
            self.channel = channel
        }
    }

    static let fiveGHzGroups: [(name: String, channels: [Int])] = [("36–48", [36, 40, 44, 48]),
                                                                  ("149–161", [149, 153, 157, 161])]

    public static func evaluate(_ wifi: Connection?, scan: [Network], floor: Int = 10) -> CheckResult {
        guard let wifi, wifi.isConnected, let channel = wifi.channel else {
            return CheckResult(id: 3, key: "interference", status: .info, summary: Message("diag.interference.not_connected"))
        }
        let own = deviceKey(wifi.bssid)
        let band = scan.first { $0.bssid == wifi.bssid }?.band ?? (channel <= 14 ? "2.4 GHz" : "5 GHz")
        func isForeign(_ network: Network) -> Bool {
            deviceKey(network.bssid) != own && (network.signal ?? 0) >= floor
        }
        func name(_ network: Network) -> String { network.ssid.isEmpty ? network.bssid : network.ssid }

        // One per SSID (per BSSID when hidden): like Python's dict, the last entry wins and the
        // first one fixes the order.
        var order: [String] = []
        var same: [String: Network] = [:]
        for network in scan where isForeign(network) && network.channel == channel && network.band == band {
            if same[name(network)] == nil { order.append(name(network)) }
            same[name(network)] = network
        }
        var details: [MessageValue] = [.message(Message("diag.interference.current", [
            "channel": .number(Double(channel)), "band": .text(band), "count": .number(Double(same.count)),
        ]))]
        let strongestFirst = order.enumerated()
            .sorted { (-(same[$0.element]!.signal ?? 0), $0.offset) < (-(same[$1.element]!.signal ?? 0), $1.offset) }
        for (_, key) in strongestFirst {
            let network = same[key]!
            details.append(.message(Message("diag.common.network_signal", [
                "ssid": network.ssid.isEmpty ? .message(Message("diag.common.hidden_ssid")) : .text(network.ssid),
                "signal": network.signal.map { .number(Double($0)) } ?? .empty,
            ])))
        }
        guard same.count >= 2 else {
            return CheckResult(id: 3, key: "interference", status: .ok,
                               summary: Message("diag.interference.quiet", ["channel": .number(Double(channel)),
                                                                            "count": .number(Double(same.count))]),
                               details: details)
        }

        let advice: Message
        if band == "5 GHz" {
            let perGroup = fiveGHzGroups.map { group in
                (name: group.name, counts: group.channels.map { c in
                    (channel: c, count: Set(scan.filter { isForeign($0) && $0.band == "5 GHz" && $0.channel == c }.map(name)).count)
                })
            }
            let totals = perGroup.map { (name: $0.name, total: $0.counts.map(\.count).reduce(0, +)) }
            // min() keeps the first of equals, as Python's min() does.
            let best = perGroup[totals.indices.min { totals[$0].total < totals[$1].total }!]
            let quiet = best.counts.min { $0.count < $1.count }!.channel
            details.append(.message(Message("diag.interference.groups", [
                "groups": .list(totals.map { .text("\($0.name): \($0.total)") }),
            ])))
            advice = Message("diag.interference.advice_5g", ["group": .text(best.name), "channel": .number(Double(quiet))])
        } else if band == "6 GHz" {   // many free channels, no 5 GHz to move to (SIC-71)
            advice = Message("diag.interference.advice_6g")
        } else {
            advice = Message("diag.interference.advice_24g")
        }
        return CheckResult(id: 3, key: "interference", status: .warn,
                           summary: Message("diag.interference.crowded", ["count": .number(Double(same.count)),
                                                                          "channel": .number(Double(channel))]),
                           details: details, advice: .message(advice))
    }

    /// First 5 octets: virtual APs and bands of one router share them.
    static func deviceKey(_ bssid: String) -> String {
        let parts = bssid.split(separator: ":", omittingEmptySubsequences: false).map { Int($0, radix: 16) }
        guard !parts.contains(where: { $0 == nil }) else { return "" }
        return parts.prefix(5).map { String(format: "%02x", $0!) }.joined(separator: ":")
    }

    // MARK: - The Mac, without BSSIDs

    /// A network as CoreWLAN shows it without location permission: no name, no BSSID.
    public struct Sighting: Equatable, Sendable {
        public var rssi: Int
        public var channel: Int
        public var band: String

        public init(rssi: Int, channel: Int, band: String) {
            self.rssi = rssi
            self.channel = channel
            self.band = band
        }
    }

    /// Our router's radio is the network on our channel whose RSSI is closest to ours (within
    /// `ownSearch` dB); its other SSIDs (guest network…) come from the same radio, so they show
    /// within `sameRadio` dB of it in the same scan.
    static let ownSearch = 10
    static let sameRadio = 3

    /// Turns what the Mac sees into the rule's input, with made-up BSSIDs: the networks taken for
    /// our router's radio share our device prefix, every other one gets its own. A neighbour
    /// whose signal happens to match our router's is taken for ours, so the check can miss a
    /// crowded channel but does not report one that is not.
    public static func anonymous(current: Sighting, around: [Sighting]) -> (Connection, [Network]) {
        let ownBSSID = "02:00:00:00:00:00"
        let onOurChannel = around.indices.filter { around[$0].channel == current.channel && around[$0].band == current.band }
        let ours = onOurChannel.min { abs(around[$0].rssi - current.rssi) < abs(around[$1].rssi - current.rssi) }
            .flatMap { abs(around[$0].rssi - current.rssi) <= ownSearch ? $0 : nil }
        let networks = around.enumerated().map { index, sighting in
            let bssid: String
            if index == ours {
                bssid = ownBSSID
            } else if let ours, onOurChannel.contains(index), abs(sighting.rssi - around[ours].rssi) <= sameRadio {
                bssid = String(format: "02:00:00:00:00:%02x", index % 255 + 1)
            } else {
                bssid = String(format: "02:00:01:%02x:%02x:00", index / 256 % 256, index % 256)
            }
            return Network(ssid: "", bssid: bssid, signal: WiFiSignal.State.quality(rssi: sighting.rssi),
                           band: sighting.band, channel: sighting.channel)
        }
        return (Connection(state: "connected", bssid: ownBSSID, channel: current.channel), networks)
    }
}
