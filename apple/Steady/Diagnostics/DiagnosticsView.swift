import SteadyKit
import SwiftUI

/// Every check with its verdict, advice and details; the load test on request.
struct DiagnosticsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmingBufferbloat = false
    @Environment(\.dynamicTypeSize) private var typeSize
    private let text = Localizer()

    var body: some View {
        Form {
            if let signal = model.signal {
                Section {
                    CheckResultView(result: signal, text: text)
                }
            }
            if let interference = model.interference {
                Section {
                    CheckResultView(result: interference, text: text)
                }
            }
            Section {
                CheckResultView(result: model.monitor.pingQuality, text: text)
            }
            if let vpn = model.vpn {
                Section {
                    CheckResultView(result: vpn, text: text)
                }
            }
            if let dns = model.dns {
                Section {
                    CheckResultView(result: dns, text: text) {
                        rerunButton
                    }
                }
            } else if model.isConnected {
                Section {
                    Label(text("ui.diag.running"), systemImage: "hourglass")
                        .foregroundStyle(.secondary)
                }
            }
            if let link = model.monitor.physicalLink {
                Section {
                    CheckResultView(result: link, text: text)
                }
            }
            Section {
                bufferbloatCard
                    // Anchored to the card, so the iPad/Mac popover points at the button that opened it.
                    .confirmationDialog(text("diag.bufferbloat.title"), isPresented: $confirmingBufferbloat,
                                        titleVisibility: .visible) {
                        Button(text("ui.diag.bufferbloat_run")) {
                            model.runBufferbloat()
                        }
                        Button(text("ui.sheet.cancel"), role: .cancel) {}
                    } message: {
                        Text(text("ui.diag.bufferbloat_confirm"))
                    }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(text("ui.nav.diagnostics"))
    }

    private var rerunButton: some View {
        Button {
            model.runDNS()
        } label: {
            if model.dnsRunning {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName: "arrow.clockwise")
            }
        }
        .buttonStyle(.borderless)
        .disabled(model.dnsRunning || !model.isConnected)
        .accessibilityLabel(text(model.dnsRunning ? "ui.diag.running" : "ui.diag.run"))
    }

    /// Check #14 runs only on request: it moves up to ~200 MB (docs/DIAGNOSTICS.md).
    @ViewBuilder private var bufferbloatCard: some View {
        if let stage = model.bufferbloatStage {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(progress(stage))
                    .foregroundStyle(.secondary)
            }
        } else if let bufferbloat = model.bufferbloat {
            CheckResultView(result: bufferbloat, text: text) {
                Button {
                    confirmingBufferbloat = true
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .disabled(!model.isConnected)
                .accessibilityLabel(text("ui.diag.bufferbloat_run"))
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                (typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4)) : AnyLayout(HStackLayout())) {
                    Text(text("diag.bufferbloat.title"))
                        .font(.headline)
                    Spacer(minLength: 0)
                    Text(text("ui.diag.on_demand"))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Button(text("ui.diag.bufferbloat_run")) {
                    confirmingBufferbloat = true
                }
                .disabled(!model.isConnected)
            }
            .padding(.vertical, 4)
        }
    }

    private func progress(_ stage: BufferbloatTest.Stage) -> String {
        switch stage {
        case .idle: text("ui.diag.running")
        case .download: text("ui.diag.running") + " · " + text("diag.bufferbloat.download")
        case .upload: text("ui.diag.running") + " · " + text("diag.bufferbloat.upload")
        }
    }
}
