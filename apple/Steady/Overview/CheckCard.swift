import SteadyKit
import SwiftUI

/// "Check my connection" on the Overview (ADR-0020): one big button, the checks as they really
/// run, then what was found. Only warnings and errors count; each says whether the user can fix
/// it or nothing on this device does, with the check's advice. "Nothing to fix" shows the numbers
/// behind it. The Apple app changes nothing, so there is no Fix button.
struct CheckCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text
    @Environment(\.locale) private var locale

    var body: some View {
        if let run = model.checkRun {
            if run.isRunning {
                running(run)
            } else if run.problems.isEmpty {
                clear(run)
            } else {
                found(run)
            }
        } else {
            Section {
                CheckHero(title: text("ui.check.idle_title"),
                          message: text("ui.check.idle_body", ["count": .number(Double(AppModel.checkSteps.count))])) {
                    Button(action: model.runCheck) {
                        VStack(spacing: 6) {
                            Image(systemName: "gauge.with.dots.needle.67percent")
                                .font(.title)
                            Text(text("ui.check.start"))
                                .font(.headline)
                        }
                    }
                    .buttonStyle(RoundButtonStyle())
                }
            }
        }
    }

    private func running(_ run: CheckRun) -> some View {
        let total = run.steps.count
        let current = run.steps.first { $0.id == run.current }
        return Section {
            CheckHero(title: text("ui.check.running", ["done": .number(Double(min(run.done + 1, total))),
                                                       "total": .number(Double(total))]),
                      message: current.map { text($0.title) }) {
                ProgressRing(done: run.done, total: total)
            }
            ForEach(run.steps) { step in
                HStack(spacing: 10) {
                    Group {
                        if let result = run.result(of: step) {
                            StatusLabel(status: result.status, text: text, iconOnly: true)
                        } else if step.id == run.current {
                            ProgressView().controlSize(.small)
                        } else if run.steps.firstIndex(where: { $0.id == step.id })! < run.done {
                            Image(systemName: "minus.circle").foregroundStyle(.secondary)   // nothing to read
                        } else {
                            Image(systemName: "circle").foregroundStyle(.tertiary)
                        }
                    }
                    .frame(width: 22)
                    Text(text(step.title))
                        .foregroundStyle(step.id == run.current ? .primary : .secondary)
                }
            }
        }
    }

    @ViewBuilder
    private func found(_ run: CheckRun) -> some View {
        let problems = run.problems
        let worst = CheckStatus.worst(problems.map(\.result.status))
        Section {
            CheckHero(title: text("ui.check.found_title", ["count": .number(Double(problems.count))]),
                      message: checkedLine(run), buttons: buttons) {
                StatusCircle(status: worst, text: text)
            }
        }
        Section {
            ForEach(problems, id: \.result.id) { problem in
                ProblemRow(problem: problem, text: text)
            }
        }
    }

    @ViewBuilder
    private func clear(_ run: CheckRun) -> some View {
        Section {
            CheckHero(title: text("ui.check.clear_title"), message: checkedLine(run), buttons: buttons) {
                StatusCircle(status: .ok, text: text)
            }
        }
        Section {
            LabeledContent(text("ui.overview.router"), value: milliseconds(run.measured.routerMs))
            LabeledContent(text("ui.overview.internet"), value: milliseconds(run.measured.internetMs))
            LabeledContent(text("ui.overview.loss"), value: run.measured.internetLossPercent.map {
                ($0 / 100).formatted(.percent.precision(.fractionLength(1)).locale(locale))
            } ?? "—")
        } footer: {
            Text(text("ui.check.recent_note"))
        }
    }

    /// "Checked at 14:02 · 4 of 6 checks are fine", or "All 6 checks are fine" when they all are.
    /// Checks that could not judge (no data yet) are neither problems nor fine.
    private func checkedLine(_ run: CheckRun) -> String {
        let time = (run.finishedAt ?? .now).formatted(Date.FormatStyle(date: .omitted, time: .shortened, locale: locale))
        let total = run.results.count
        if run.okCount == total {
            return text("ui.check.clear_body", ["total": .number(Double(total)), "time": .text(time)])
        }
        return text("ui.check.found_body", ["time": .text(time), "ok": .number(Double(run.okCount)),
                                            "total": .number(Double(total))])
    }

    private var buttons: some View {
        HStack(spacing: 12) {
            Button(text("ui.check.again"), action: model.runCheck)
            Button(text("ui.check.details")) {
                model.selectedTab = .diagnostics
            }
        }
        .buttonStyle(.bordered)
    }

    private func milliseconds(_ value: Double?) -> String {
        value.map { text("ui.live.rtt", ["value": .number($0)]) } ?? "—"
    }
}

/// The centre of the card: a circle (button, progress or verdict), a title and a line under it.
private struct CheckHero<Badge: View, Buttons: View>: View {
    let title: String
    let message: String?
    let buttons: Buttons
    let circle: Badge

    init(title: String, message: String?, buttons: Buttons = EmptyView(), @ViewBuilder circle: () -> Badge) {
        self.title = title
        self.message = message
        self.buttons = buttons
        self.circle = circle()
    }

    var body: some View {
        VStack(spacing: 10) {
            circle
                .padding(.bottom, 4)
            Text(title)
                .font(.title3.bold())
            if let message {
                Text(message)
                    .foregroundStyle(.secondary)
            }
            buttons
                .padding(.top, 4)
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
    }
}

/// One big round accent button, flat on every platform (the mockup's "Check my connection").
private struct RoundButtonStyle: ButtonStyle {
    @ScaledMetric(relativeTo: .headline) private var diameter = 148.0

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.white)
            .multilineTextAlignment(.center)
            .minimumScaleFactor(0.7)
            .padding(18)
            .frame(width: min(diameter, 220), height: min(diameter, 220))
            .background(Circle().fill(.tint))
            .opacity(configuration.isPressed ? 0.75 : 1)
            .contentShape(Circle())
    }
}

/// Fills as checks really finish, with "2/6" in the middle.
private struct ProgressRing: View {
    let done: Int
    let total: Int

    var body: some View {
        ZStack {
            Circle()
                .stroke(.quaternary, lineWidth: 10)
            Circle()
                .trim(from: 0, to: total == 0 ? 0 : CGFloat(done) / CGFloat(total))
                .stroke(.tint, style: StrokeStyle(lineWidth: 10, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(.easeOut(duration: 0.25), value: done)
            Text("\(done)/\(total)")
                .font(.title2.monospacedDigit().bold())
        }
        .frame(width: 120, height: 120)
        .accessibilityElement(children: .ignore)
        .accessibilityValue("\(done)/\(total)")
    }
}

/// The verdict as a large icon in a tinted circle.
private struct StatusCircle: View {
    let status: CheckStatus
    let text: Localizer

    var body: some View {
        let label = StatusLabel(status: status, text: text, iconOnly: true)
        Image(systemName: label.symbol)
            .font(.system(size: 52))
            .foregroundStyle(label.color)
            .frame(width: 120, height: 120)
            .background(Circle().fill(label.color.opacity(0.12)))
            .accessibilityLabel(text("ui.status." + status.rawValue))
    }
}

/// A counted problem: what the check found, who can fix it, and the check's advice.
private struct ProblemRow: View {
    let problem: CheckFlow.Problem
    let text: Localizer

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                StatusLabel(status: problem.result.status, text: text, iconOnly: true)
                Text(text(problem.result.title))
                    .font(.headline)
            }
            Text(text("ui.check.kind." + problem.remedy.rawValue))
                .font(.caption.weight(.semibold))
                .foregroundStyle(problem.remedy == .you ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                .padding(.horizontal, 8)
                .padding(.vertical, 2)
                .background(Capsule().fill(problem.remedy == .you ? AnyShapeStyle(.tint.opacity(0.15))
                                                                   : AnyShapeStyle(.quaternary)))
            Text(text(problem.result.summary))
            if problem.result.advice != .empty {
                Text(text.render(problem.result.advice))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}
