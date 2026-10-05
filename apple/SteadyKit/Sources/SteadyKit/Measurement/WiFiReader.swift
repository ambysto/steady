#if os(macOS)
import CoreWLAN

/// Reads the Mac's Wi‑Fi interface with CoreWLAN, which works inside the App Sandbox. The
/// network name and BSSID stay nil unless the app has location permission (macOS 14+).
public enum WiFiReader {
    /// nil when the Mac has no Wi‑Fi interface.
    public static func current() -> WiFiSignal.State? {
        guard let interface = CWWiFiClient.shared().interface() else { return nil }
        let rssi = interface.rssiValue()
        guard interface.powerOn(), let channel = interface.wlanChannel(), rssi != 0 else {
            return WiFiSignal.State(state: "disconnected")
        }
        let transmit = interface.transmitRate()
        return WiFiSignal.State(state: "connected", ssid: interface.ssid(), channel: channel.channelNumber,
                                radioType: radioType(interface.activePHYMode()),
                                signal: WiFiSignal.State.quality(rssi: rssi), rssi: rssi,
                                rxMbps: nil,   // CoreWLAN reports only the transmit rate
                                txMbps: transmit > 0 ? Int(transmit.rounded()) : nil)
    }

    /// The names Windows' netsh uses for the radio type.
    static func radioType(_ mode: CWPHYMode) -> String {
        switch mode {
        case .mode11a: "802.11a"
        case .mode11b: "802.11b"
        case .mode11g: "802.11g"
        case .mode11n: "802.11n"
        case .mode11ac: "802.11ac"
        case .mode11ax: "802.11ax"
        case .mode11be: "802.11be"
        case .modeNone: ""
        @unknown default: ""
        }
    }
}
#endif
