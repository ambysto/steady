import SteadyKit
import SwiftUI

/// The connection at a glance: what the system reports, how the checks stand, and live
/// measurements of the router and the Internet.
struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text

    var body: some View {
        Form {
            Section {
                PathStatusView(path: model.path, text: text)
            }
            Section {
                ChecksSummary(checks: model.checks, text: text) {
                    model.selectedTab = .diagnostics
                }
            }
            Section {
                ForEach(model.monitor.targets) { target in
                    MeasurementRow(title: title(of: target), endpoint: target.endpoint,
                                   stats: model.monitor.stats(for: target), text: text)
                }
            } header: {
                Text(text("ui.overview.latency"))
            } footer: {
                Text(text("ui.live.window_note"))
            }
            if let path = model.path {
                Section {
                    LabeledContent(text("ui.path.connection"), value: path.linkMessage.map { text($0) } ?? "—")
                    LabeledContent(text("ui.overview.router"),
                                   value: path.routers.isEmpty ? "—" : path.routers.joined(separator: ", "))
                    LabeledContent(text("ui.path.ip"), value: text.render(path.ipVersions))
                    LabeledContent(text("ui.path.dns"), value: text(path.dnsMessage))
                }
                if !notes(for: path).isEmpty {
                    Section {
                        ForEach(notes(for: path), id: \.self) { note in
                            Label(text(note), systemImage: "info.circle")
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(text("ui.nav.overview"))
    }

    private func title(of target: LiveMonitor.Target) -> String {
        if target.id == LiveMonitor.router { return text("ui.overview.router") }
        return text(target.kind == .tcp ? "ui.live.tcp" : "ui.overview.internet")
    }

    /// The path's own notes, plus why the router is not measured when the system refuses it.
    private func notes(for path: NetworkPath) -> [Message] {
        path.notes + (model.monitor.refusalNote(vpnUp: model.vpnUp).map { [$0] } ?? [])
    }
}

/// "1 warning · 4 OK" (or "All checks are OK") with a way to the Diagnostics tab. Checks that
/// could not judge yet (no data, not on Wi‑Fi) are left out; the Diagnostics tab shows them.
private struct ChecksSummary: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let all: [CheckResult]
    let text: Localizer
    let open: () -> Void

    init(checks: [CheckResult], text: Localizer, open: @escaping () -> Void) {
        all = checks
        self.text = text
        self.open = open
    }

    private var checks: [CheckResult] { all.filter(\.isAssessed) }

    var body: some View {
        Button(action: open) {
            HStack {
                if checks.isEmpty {
                    ProgressView().controlSize(.small)
                } else {
                    StatusLabel(status: CheckStatus.worst(checks.map(\.status)), text: text, iconOnly: true)
                }
                Text(summary)
                Spacer()
                Image(systemName: "chevron.forward")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityHint(text("ui.overview.open_diagnostics"))
    }

    private var summary: String {
        func count(_ status: CheckStatus) -> Int { checks.filter { $0.status == status }.count }
        guard !checks.isEmpty else { return text("ui.live.measuring") }
        guard checks.contains(where: { $0.status != .ok }) else { return text("ui.overview.all_ok") }
        let parts: [(CheckStatus, String)] = [(.bad, "ui.diag.count.bad"), (.warn, "ui.diag.count.warn"),
                                              (.info, "ui.diag.count.info"), (.ok, "ui.diag.count.ok")]
        return parts.filter { count($0.0) > 0 }
            .map { text($0.1, ["count": .number(Double(count($0.0)))]) }
            .joined(separator: typeSize.isAccessibilitySize ? "\n" : " · ")   // one per line when large
    }
}
