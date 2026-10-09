import SteadyKit
import SwiftUI

/// The speed test on the Overview (ADR-0023): one button, a large live figure while it runs, then
/// download, upload and the two pings. It is check #14's run, so the Diagnostics tab's Bufferbloat
/// result comes from the same measurement.
struct SpeedTestCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text
    @Environment(\.locale) private var locale
    @State private var confirming = false

    var body: some View {
        Section {
            // Redrawn every 15 s, so a refusal's countdown moves on and the button comes back after it.
            TimelineView(.periodic(from: .now, by: 15)) { context in
                content(now: context.date)
            }
            .speedTestConfirmation(isPresented: $confirming, title: text("ui.speed.title"))
            // The floating bar cannot ask itself: it opens this window and the question is asked here.
            .onChange(of: model.speedConfirmRequested, initial: true) { _, requested in
                guard requested else { return }
                model.speedConfirmRequested = false
                confirming = true
            }
        }
    }

    private func content(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label(text("ui.speed.title"), systemImage: "gauge.with.dots.needle.67percent")
                    .font(.headline)
                Spacer(minLength: 8)
                if model.speedRunning {
                    ProgressView().controlSize(.small)
                } else {
                    Button(text(model.speedResult == nil ? "ui.speed.start" : "ui.speed.again"), action: start)
                        .buttonStyle(.borderedProminent)
                        .disabled(!model.canRunSpeedTest(at: now))
                }
            }
            if let progress = model.speedProgress {
                live(progress)
            } else if let result = model.speedResult {
                figures(result)
                notes(result, now: now)
            } else {
                Text(text("ui.speed.idle_body"))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            if model.speedLimit == nil {
                Text(text("ui.diag.bufferbloat_constrained"))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    /// The rate over the last second, large, and the phase under it.
    private func live(_ progress: BufferbloatTest.Progress) -> some View {
        VStack(spacing: 6) {
            Text(progress.stage == .idle ? "—" : Units.rate(progress.liveMbps, text))
                .font(.system(size: 44, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .contentTransition(.numericText())
                .animation(.default, value: progress.liveMbps)
            Label(text(running(progress.stage)), systemImage: icon(progress.stage))
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }

    /// Download and upload above, the two pings below: two columns at any width.
    private func figures(_ result: SpeedTest.Result) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 28, verticalSpacing: 14) {
            GridRow {
                figure("ui.speed.download", icon: "arrow.down.circle.fill", tint: .blue,
                       value: rate(result.downloadMbps, capped: result.downloadCapped))
                figure("ui.speed.upload", icon: "arrow.up.circle.fill", tint: .purple,
                       value: rate(result.uploadMbps, capped: result.uploadCapped))
            }
            GridRow {
                figure("ui.speed.ping_idle", icon: "waveform.path.ecg", tint: .green,
                       value: result.idlePingMs.map { Units.milliseconds($0, text) } ?? "—")
                figure("ui.speed.ping_loaded", icon: "waveform.path.ecg", tint: .orange,
                       value: result.loadedPingMs.map { Units.milliseconds($0, text) } ?? "—")
            }
        }
    }

    private func figure(_ title: String, icon: String, tint: Color, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Label {
                Text(text(title))
            } icon: {
                Image(systemName: icon).foregroundStyle(tint)
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
            Text(value)
                .font(.title2.weight(.semibold))
                .monospacedDigit()
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func notes(_ result: SpeedTest.Result, now: Date) -> some View {
        let time = result.measuredAt.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened, locale: locale))
        VStack(alignment: .leading, spacing: 4) {
            if let note = problem(result, now: now) {
                Label(note, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }
            Text(text("ui.speed.measured", ["time": .text(time)]))
                .foregroundStyle(.secondary)
        }
        .font(.footnote)
    }

    /// Why a figure is missing: the server refused (with the wait while it lasts), or nothing moved.
    private func problem(_ result: SpeedTest.Result, now: Date) -> String? {
        if result.refusedStatus != nil {
            guard let until = result.retryUntil else { return text("ui.speed.refused_later") }
            guard until > now else { return nil }   // the wait is over: the test can run again
            let minutes = max(1, (until.timeIntervalSince(now) / 60).rounded(.up))
            return text("ui.speed.refused", ["minutes": .number(minutes)])
        }
        return result.hasFigures ? nil : text("ui.speed.failed")
    }

    private func rate(_ mbps: Double?, capped: Bool) -> String {
        guard let mbps else { return "—" }
        let value = Units.rate(mbps, text)
        return capped ? text("ui.speed.at_least", ["value": .text(value)]) : value
    }

    private func running(_ stage: BufferbloatTest.Stage) -> String {
        switch stage {
        case .idle: "ui.speed.running.idle"
        case .download: "ui.speed.running.download"
        case .upload: "ui.speed.running.upload"
        }
    }

    private func icon(_ stage: BufferbloatTest.Stage) -> String {
        switch stage {
        case .idle: "waveform.path.ecg"
        case .download: "arrow.down.circle.fill"
        case .upload: "arrow.up.circle.fill"
        }
    }

    private func start() {
        if model.speedNeedsConfirmation {
            confirming = true
        } else {
            model.runSpeedTest()
        }
    }
}
