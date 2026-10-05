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

    /// What check #3 needs: our channel and signal, and the networks around (channel, band and
    /// signal only: no names, no BSSIDs). The system's last scan is used when it has one, so
    /// the radio does not leave the channel and disturb the measurements; `allowScan` lets an
    /// empty cache be filled by scanning. nil when not connected over Wi‑Fi.
    public static func surroundings(allowScan: Bool) -> (current: Interference.Sighting, around: [Interference.Sighting])? {
        guard let interface = CWWiFiClient.shared().interface(), interface.powerOn(),
              let channel = interface.wlanChannel(), interface.rssiValue() != 0 else { return nil }
        var networks = interface.cachedScanResults() ?? []
        if networks.isEmpty, allowScan {
            networks = (try? interface.scanForNetworks(withSSID: nil)) ?? []
        }
        let around = networks.compactMap { network -> Interference.Sighting? in
            guard let channel = network.wlanChannel else { return nil }
            return Interference.Sighting(rssi: network.rssiValue, channel: channel.channelNumber, band: band(channel.channelBand))
        }
        return (Interference.Sighting(rssi: interface.rssiValue(), channel: channel.channelNumber,
                                      band: band(channel.channelBand)), around)
    }

    /// The names Windows' netsh uses for the band.
    static func band(_ band: CWChannelBand) -> String {
        switch band {
        case .band2GHz: "2.4 GHz"
        case .band5GHz: "5 GHz"
        case .band6GHz: "6 GHz"
        case .bandUnknown: ""
        @unknown default: ""
        }
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
