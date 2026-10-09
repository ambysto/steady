import SteadyKit
import SwiftUI

/// The connection at a glance: what the system reports, "Check my connection", the speed test,
/// the last 7 days, and live measurements of the router and the Internet.
struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text

    var body: some View {
        Form {
            Section {
                PathStatusView(path: model.path, text: text)
            }
            CheckCard()
            SpeedTestCard()
            WeekCard()
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
            #if os(macOS)
            Section {
                Button {
                    model.toggleFloatingMonitor()
                } label: {
                    Label(text("ui.tray.mini"), systemImage: "rectangle.on.rectangle")
                }
            }
            #endif
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
