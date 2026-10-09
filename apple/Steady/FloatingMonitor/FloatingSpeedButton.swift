#if os(macOS)
import AppKit
import SteadyKit
import SwiftUI

/// The speed test on the bar (ADR-0023), next to the Scan bulb. A gauge until it runs; while it
/// runs, a ring fills over the run's length (the tube's download and upload show the live rate);
/// then the download figure until the result goes stale. A result opens in the main window, and so
/// does the confirmation: the panel never takes the keyboard, so it does not ask itself.
struct FloatingSpeedButton: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text
    @Environment(\.openWindow) private var openWindow

    private enum Mode: Equatable {
        case idle
        case busy(fraction: Double)
        case done(download: Double)
        case problem

        var isFigure: Bool {
            if case .done = self { return true }
            return false
        }
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let mode = mode(at: context.date)
            Button {
                tap(mode, now: context.date)
            } label: {
                label(mode)
                    .frame(height: 17)
                    .padding(.horizontal, mode.isFigure ? 6 : 0)
                    .frame(minWidth: 17)
                    .background(Capsule().fill(FloatingColor.tubeButton.opacity(mode == .problem ? 0.35 : 1)))
            }
            .buttonStyle(.plain)
            .help(tooltip(mode, now: context.date))
            .accessibilityLabel(text("ui.speed.title"))
            .accessibilityValue(tooltip(mode, now: context.date))
        }
    }

    @ViewBuilder
    private func label(_ mode: Mode) -> some View {
        switch mode {
        case .idle, .problem:
            Image(systemName: "gauge.with.dots.needle.67percent")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(mode == .problem ? FloatingColor.amber : .white)
        case .busy(let fraction):
            ZStack {
                Circle().stroke(Color.white.opacity(0.25), lineWidth: 2)
                Circle()
                    .trim(from: 0, to: fraction)
                    .stroke(Color.white, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            .frame(width: 11, height: 11)
        case .done(let download):
            Text(Units.number(download, decimals: download < 10 ? 1 : 0, text))
                .font(.system(size: 10, weight: .bold).monospacedDigit())
                .foregroundStyle(.white)
        }
    }

    private func mode(at date: Date) -> Mode {
        if model.speedRunning {
            return .busy(fraction: min(max(model.speedLive?.fraction ?? 0, 0.03), 0.97))
        }
        guard let result = model.speedResult else { return .idle }
        if model.speedBlockedUntil(at: date) != nil { return .problem }
        guard !CheckFlow.isStale(checkedAt: result.measuredAt.timeIntervalSince1970,
                                 now: date.timeIntervalSince1970) else { return .idle }
        if let download = result.downloadMbps { return .done(download: download) }
        return result.hasFigures ? .idle : .problem
    }

    private func tooltip(_ mode: Mode, now: Date) -> String {
        switch mode {
        case .idle: return text("ui.speed.mini.hint")
        case .busy: return text(runningKey)
        case .done, .problem:
            guard let result = model.speedResult else { return text("ui.speed.mini.hint") }
            if let until = model.speedBlockedUntil(at: now) {
                let minutes = max(1, (until.timeIntervalSince(now) / 60).rounded(.up))
                return "\(text("ui.speed.refused", ["minutes": .number(minutes)]))\n\(text("ui.speed.mini.open"))"
            }
            let ping = result.idle?.medianMs.map { Units.milliseconds($0, text) } ?? "—"
            let line = text("ui.speed.mini.result", ["download": .text(Units.rate(result.downloadMbps, text)),
                                                     "upload": .text(Units.rate(result.uploadMbps, text)),
                                                     "ping": .text(ping)])
            return "\(line)\n\(text("ui.speed.mini.open"))"
        }
    }

    private var runningKey: String {
        switch model.speedLive?.stage ?? .idle {
        case .idle: "ui.speed.running.idle"
        case .download: "ui.speed.running.download"
        case .upload: "ui.speed.running.upload"
        }
    }

    private func tap(_ mode: Mode, now: Date) {
        switch mode {
        case .busy:
            break
        case .idle where model.canRunSpeedTest(at: now) && !model.speedNeedsConfirmation:
            model.runSpeedTest()
        case .idle:
            // Asked in the main window; it also says why the test cannot run when it cannot.
            model.speedConfirmRequested = model.canRunSpeedTest(at: now)
            openOverview()
        case .done, .problem:
            openOverview()
        }
    }

    private func openOverview() {
        model.selectedTab = .overview
        openWindow(id: "main")
        NSApp.activate(ignoringOtherApps: true)
    }
}
#endif
