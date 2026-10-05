import SteadyKit
import SwiftUI

/// One measured target: latest round-trip time, then loss and jitter over the live window.
struct MeasurementRow: View {
    let title: String
    let endpoint: String
    let stats: LiveStats
    let text: Localizer

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(endpoint)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                latest
                if !stats.isEmpty {
                    Text(details)
                        .font(.caption)
                        .foregroundStyle(stats.lossPercent > 1 ? .orange : .secondary)
                }
            }
            .monospacedDigit()
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
        return parts.joined(separator: " · ")
    }
}
