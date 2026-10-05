import SteadyKit
import SwiftUI

/// First screen: the connection the system reports (NWPathMonitor), live measurements of the
/// router and the Internet while the screen is open, and check #5 over those measurements.
struct OverviewView: View {
    @State private var path: NetworkPath?
    @State private var monitor = LiveMonitor()
    private let text = Localizer()

    var body: some View {
        Form {
            Section {
                PathStatusView(path: path, text: text)
            }
            Section {
                ForEach(monitor.targets) { target in
                    MeasurementRow(title: title(of: target), endpoint: target.endpoint,
                                   stats: monitor.stats(for: target), text: text)
                }
            } header: {
                Text(text("ui.overview.latency"))
            } footer: {
                Text(text("ui.live.window_note"))
            }
            Section {
                CheckResultView(result: monitor.pingQuality, text: text)
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
                monitor.routerAddress = update.routerIPv4
            }
        }
        .task {
            await monitor.run()
        }
    }

    private func title(of target: LiveMonitor.Target) -> String {
        if target.id == LiveMonitor.router { return text("ui.overview.router") }
        return text(target.kind == .tcp ? "ui.live.tcp" : "ui.overview.internet")
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

/// One measured target: latest round-trip time, then loss and jitter over the live window.
private struct MeasurementRow: View {
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

/// A diagnosis check: status, verdict and advice; the per-target lines on demand.
private struct CheckResultView: View {
    let result: CheckResult
    let text: Localizer

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(text(result.title))
                    .font(.headline)
                Spacer()
                Label(text("ui.status." + result.status.rawValue), systemImage: symbol)
                    .labelStyle(.titleAndIcon)
                    .font(.subheadline)
                    .foregroundStyle(color)
            }
            Text(text(result.summary))
            if result.advice != .empty {
                Text(text.render(result.advice))
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            if !result.details.isEmpty {
                DisclosureGroup {
                    ForEach(Array(result.details.enumerated()), id: \.offset) { _, line in
                        Text(text.render(line))
                            .font(.caption)
                            .monospacedDigit()
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                } label: {
                    Text(text("ui.overview.see_all"))
                        .font(.callout)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var symbol: String {
        switch result.status {
        case .ok: "checkmark.circle.fill"
        case .info: "info.circle.fill"
        case .warn: "exclamationmark.triangle.fill"
        case .bad: "xmark.octagon.fill"
        }
    }

    private var color: Color {
        switch result.status {
        case .ok: .green
        case .info: .blue
        case .warn: .orange
        case .bad: .red
        }
    }
}

#Preview {
    NavigationStack {
        OverviewView()
    }
}
