import SteadyKit
import SwiftUI

/// The connection as the system reports it: connected or not, and over what.
struct PathStatusView: View {
    let path: NetworkPath?
    let text: Localizer

    var body: some View {
        HStack(spacing: 14) {
            Group {
                if path == nil {
                    ProgressView()
                } else {
                    Image(systemName: symbol)
                        .font(.title)
                        .foregroundStyle(color)
                }
            }
            .frame(width: 40)
            VStack(alignment: .leading, spacing: 2) {
                Text(path.map { text($0.statusMessage) } ?? text("ui.path.checking"))
                    .font(.headline)
                if let path, path.status == .connected, let link = path.linkMessage {
                    Text(text(link))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    private var symbol: String {
        guard let path else { return "network" }
        switch path.status {
        case .disconnected: return "wifi.slash"
        case .requiresConnection: return "network"
        case .connected:
            switch path.link {
            case .wifi: return "wifi"
            case .wired: return "cable.connector"
            case .cellular: return "antenna.radiowaves.left.and.right"
            case .other, nil: return "network"
            }
        }
    }

    private var color: Color {
        switch path?.status {
        case .connected: .green
        case .requiresConnection: .orange
        case .disconnected: .red
        case nil: .secondary
        }
    }
}
