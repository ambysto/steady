import SteadyKit
import SwiftUI

/// First screen: what the system reports about the current connection (NWPathMonitor).
/// Live router/Internet measurements come later.
struct OverviewView: View {
    @State private var path: NetworkPath?
    private let text = Localizer()

    var body: some View {
        Form {
            Section {
                PathStatusView(path: path, text: text)
            }
            if let path {
                Section {
                    LabeledContent(text("ui.path.connection"), value: path.linkMessage.map { text($0) } ?? "—")
                    LabeledContent(text("ui.overview.router"),
                                   value: path.gateways.isEmpty ? "—" : path.gateways.joined(separator: ", "))
                    LabeledContent(text("ui.path.ip"), value: text.render(path.ipVersions))
                    LabeledContent(text("ui.path.dns"), value: text(path.dnsMessage))
                }
                if !path.notes.isEmpty {
                    Section {
                        ForEach(path.notes, id: \.self) { note in
                            Label(text(note), systemImage: "info.circle")
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(text("ui.nav.overview"))
        .task {
            for await update in NetworkPath.updates() {
                path = update
            }
        }
    }
}

private struct PathStatusView: View {
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

#Preview {
    NavigationStack {
        OverviewView()
    }
}
