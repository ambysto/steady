import Foundation
import SteadyKit

/// Rates and latencies as the floating monitor and the speed test show them, in the language
/// chosen in Settings.
enum Units {
    static func number(_ value: Double, decimals: Int, _ text: Localizer) -> String {
        value.formatted(.number.precision(.fractionLength(decimals)).locale(text.locale))
    }

    /// Mbit/s above one, kbit/s below, as the Windows monitor shows them.
    static func rate(_ mbps: Double?, _ text: Localizer) -> String {
        guard let mbps else { return "—" }
        if mbps >= 1 {
            return text("ui.mini.unit.mbps", ["value": .text(number(mbps, decimals: mbps < 10 ? 1 : 0, text))])
        }
        return text("ui.mini.unit.kbps", ["value": .text(number(mbps * 1000, decimals: 0, text))])
    }

    static func milliseconds(_ ms: Double, _ text: Localizer) -> String {
        text("ui.mini.unit.ms", ["value": .text(number(ms, decimals: ms < 10 ? 1 : 0, text))])
    }
}
