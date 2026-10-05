import SteadyKit
import SwiftUI

/// One measured target: latest round-trip time, then loss and jitter over the live window.
struct MeasurementRow: View {
    let title: String
    let endpoint: String
    let stats: LiveStats
    let text: Localizer

    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        // Side by side, the name and the numbers break mid-word at accessibility text sizes
        // ("Inter-net"); there they go one under the other.
        let stacked = typeSize.isAccessibilitySize
        let layout = stacked ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4))
                             : AnyLayout(HStackLayout(alignment: .firstTextBaseline))
        layout {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(endpoint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            if !stacked {
                Spacer()
            }
            VStack(alignment: stacked ? .leading : .trailing, spacing: 2) {
                latest
                if !stats.isEmpty {
                    Text(details)
                        .font(.caption)
                        .foregroundStyle(stats.lossPercent > 1 ? .orange : .secondary)
                }
            }
            .monospacedDigit()
            .multilineTextAlignment(stacked ? .leading : .trailing)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var latest: some View {
        switch stats.latest {
        case nil:
            Text(text("ui.live.measuring")).foregroundStyle(.secondary)
        case .some(nil):
            Text(text("ui.live.no_reply")).foregroundStyle(.red)
        case .some(.some(let rtt)):
            Text(text("ui.live.rtt", ["value": .number(rtt)]))
        }
    }

    private var details: String {
        var parts = [text("ui.live.loss", ["loss": .number(stats.lossPercent)])]
        if let jitter = stats.jitter {
            parts.append(text("ui.live.jitter", ["value": .number(jitter)]))
        }
        return parts.joined(separator: typeSize.isAccessibilitySize ? "\n" : " · ")   // one per line when large
    }
}
