/// Check #2 "Wi‑Fi signal" (docs/DIAGNOSTICS.md), a port of `evaluate_signal` in app/diagnostics.py.
/// Only macOS can read the signal (CoreWLAN); iOS has no API for it. spec/diagnosis/signal.json
/// holds the cases both implementations must agree on.
public enum WiFiSignal {
    /// The Wi‑Fi interface as app/winutil.py WifiState describes it. Values a platform cannot
    /// read are nil: the network name without location permission, the receive rate on macOS.
    public struct State: Equatable, Sendable, Decodable {
        public var state: String
        public var ssid: String?
        public var channel: Int?
        /// "2.4 GHz", "5 GHz" or "6 GHz", as netsh names it; nil when unknown.
        public var band: String?
        public var radioType: String
        /// Signal quality in percent, as Windows reports it.
        public var signal: Int?
        public var rssi: Int?
        public var rxMbps: Int?
        public var txMbps: Int?

        public init(state: String, ssid: String? = nil, channel: Int? = nil, band: String? = nil, radioType: String = "",
                    signal: Int? = nil, rssi: Int? = nil, rxMbps: Int? = nil, txMbps: Int? = nil) {
            self.state = state
            self.ssid = ssid
            self.channel = channel
            self.band = band
            self.radioType = radioType
            self.signal = signal
            self.rssi = rssi
            self.rxMbps = rxMbps
            self.txMbps = txMbps
        }

        enum CodingKeys: String, CodingKey {
            case state, ssid, channel, band, signal, rssi
            case radioType = "radio_type", rxMbps = "rx_mbps", txMbps = "tx_mbps"
        }

        public var isConnected: Bool { state.lowercased() == "connected" }

        /// Windows' signal quality for an RSSI: 2 × (dBm + 100), within 0–100 %.
        public static func quality(rssi: Int) -> Int {
            min(100, max(0, 2 * (rssi + 100)))
        }
    }

    public static func evaluate(_ wifi: State?) -> CheckResult {
        guard let wifi else {
            return CheckResult(id: 2, key: "signal", status: .info, summary: Message("diag.common.no_wifi_card"))
        }
        guard wifi.isConnected, let rssi = wifi.rssi else {
            return CheckResult(id: 2, key: "signal", status: .info, summary: Message("diag.signal.not_connected"))
        }
        let details: [MessageValue] = [
            .message(Message("diag.signal.network", ["ssid": text(wifi.ssid), "channel": number(wifi.channel),
                                                     "radio": .text(wifi.radioType)])),
            .text("RSSI \(rssi) dBm (\(wifi.signal.map(String.init) ?? "?")%)"),
            .message(Message("diag.signal.rates", ["rx": number(wifi.rxMbps), "tx": number(wifi.txMbps)])),
        ]
        let (status, summary): (CheckStatus, String) = rssi >= -60 ? (.ok, "diag.signal.good")
            : rssi >= -70 ? (.warn, "diag.signal.fair") : (.bad, "diag.signal.weak")
        return CheckResult(id: 2, key: "signal", status: status,
                           summary: Message(summary, ["rssi": .number(Double(rssi))]), details: details,
                           advice: status == .ok ? .empty : .message(Message("diag.signal.advice")))
    }

    private static func text(_ value: String?) -> MessageValue {
        value.map(MessageValue.text) ?? .message(Message("diag.common.unknown"))
    }

    private static func number(_ value: Int?) -> MessageValue {
        value.map { .number(Double($0)) } ?? .message(Message("diag.common.unknown"))
    }
}
