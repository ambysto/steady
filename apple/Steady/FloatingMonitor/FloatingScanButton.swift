#if os(macOS)
import AppKit
import SteadyKit
import SwiftUI

/// The bulb of the bar: "Scan" runs the connection check, the same one as the main window's button.
/// Its ring fills while the check runs, shows the count of what it found, or a tick when all is well.
/// A result opens in the main window; the Mac changes no settings from here (ADR-0020, point 11).
struct FloatingScanButton: View {
    let diameter: CGFloat

    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text
    @Environment(\.openWindow) private var openWindow

    private enum Mode: Equatable {
        case idle
        case busy(done: Int, total: Int)
        case issues(count: Int)
        case good
    }

    var body: some View {
        // A result goes stale with time, so the mode is worked out again every second.
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let mode = mode(at: context.date)
            Button {
                tap(mode)
            } label: {
                ZStack {
                    Circle()
                        .fill(FloatingColor.solidBar)
                    Circle()
                        .stroke(Color.white.opacity(0.14), lineWidth: 2.5)
                    Circle()
                        .trim(from: 0, to: fraction(mode))
                        .stroke(ringColor(mode), style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    glyph(mode)
                }
                .frame(width: diameter, height: diameter)
            }
            .buttonStyle(.plain)
            .help(tooltip(mode))
            .accessibilityLabel(text("ui.check.start"))
        }
    }

    @ViewBuilder
    private func glyph(_ mode: Mode) -> some View {
        switch mode {
        case .idle, .busy:
            Image(systemName: "magnifyingglass")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(FloatingColor.download)
        case .issues(let count):
            Text("\(count)")
                .font(.system(size: 13, weight: .bold).monospacedDigit())
                .foregroundStyle(FloatingColor.amber)
        case .good:
            Image(systemName: "checkmark")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(FloatingColor.ping)
        }
    }

    private func fraction(_ mode: Mode) -> Double {
        switch mode {
        case .idle: 0
        case .busy(let done, let total): total > 0 ? Double(done) / Double(total) : 0
        case .issues, .good: 1
        }
    }

    private func ringColor(_ mode: Mode) -> Color {
        switch mode {
        case .idle, .busy: FloatingColor.download
        case .issues: FloatingColor.amber
        case .good: FloatingColor.ping
        }
    }

    private func label(_ mode: Mode) -> String {
        switch mode {
        case .idle: text("ui.check.start")
        case .busy(let done, let total):
            text("ui.mini.checking", ["done": .number(Double(done)), "total": .number(Double(total))])
        case .issues(let count): text("ui.mini.issues", ["count": .number(Double(count))])
        case .good: text("ui.mini.all_good")
        }
    }

    private func tooltip(_ mode: Mode) -> String {
        switch mode {
        case .idle: "\(label(mode))\n\(text("ui.mini.optimize_hint"))"
        case .busy: label(mode)
        case .issues, .good: "\(label(mode))\n\(text("ui.mini.fix_hint"))"
        }
    }

    private func mode(at date: Date) -> Mode {
        guard let run = model.checkRun else { return .idle }
        if run.isRunning {
            return .busy(done: run.done, total: run.steps.count)
        }
        guard let finished = run.finishedAt,
              !CheckFlow.isStale(checkedAt: finished.timeIntervalSince1970, now: date.timeIntervalSince1970) else {
            return .idle
        }
        let count = run.problems.count
        return count > 0 ? .issues(count: count) : .good
    }

    private func tap(_ mode: Mode) {
        switch mode {
        case .busy:
            break
        case .idle:
            model.runCheck()
        case .issues, .good:
            // The result, and what it says to do, is in the main window's Overview.
            model.selectedTab = .overview
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}
#endif
