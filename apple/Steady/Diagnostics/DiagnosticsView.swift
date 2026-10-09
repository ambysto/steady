import SteadyKit
import SwiftUI

/// Every check with its verdict, advice and details; the load test on request.
struct DiagnosticsView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmingBufferbloat = false
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.localizer) private var text

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
                    .speedTestConfirmation(isPresented: $confirmingBufferbloat, title: text("diag.bufferbloat.title"))
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

    /// Check #14 runs only on request: it moves about 250 MB per 100 Mbps of line
    /// speed, at most 2 GB (docs/DIAGNOSTICS.md); 500 MB on a metered path, nothing in Low Data Mode.
    /// It is the speed test's run (ADR-0023), so starting either one shows here.
    @ViewBuilder private var bufferbloatCard: some View {
        if let stage = model.speedProgress?.stage {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(progress(stage))
                    .foregroundStyle(.secondary)
            }
        } else if let bufferbloat = model.bufferbloat {
            CheckResultView(result: bufferbloat, text: text) {
                Button {
                    start()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .disabled(!canRunBufferbloat)
                .accessibilityLabel(text(runKey))
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
                Button(text(runKey)) {
                    start()
                }
                .disabled(!canRunBufferbloat)
                if model.speedLimit == nil {
                    Text(text("ui.diag.bufferbloat_constrained"))
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private var canRunBufferbloat: Bool {
        model.canRunSpeedTest()
    }

    private var runKey: String {
        model.speedMetered ? "ui.diag.bufferbloat_run_metered" : "ui.diag.bufferbloat_run"
    }

    private func start() {
        if model.speedNeedsConfirmation {
            confirmingBufferbloat = true
        } else {
            model.runSpeedTest()
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
