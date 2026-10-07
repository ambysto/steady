import SteadyKit
import SwiftUI

/// "Check my connection" on the Overview (ADR-0020), drawn like the Windows card: one big round
/// button (a solid accent disc inside a ring of six segments), a thin ring that fills only as each
/// check really finishes, then a status circle of the same shape with what was found. Only
/// warnings and errors count; each says whether the user can fix it or nothing on this device
/// does, with the check's advice. The Apple app changes nothing, so there is no Fix button.
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
                    BigButton(label: text("ui.check.start"), action: model.runCheck)
                }
            }
        }
    }

    /// The result's hero, redrawn once a minute so it turns stale (and "X ago" moves on) while
    /// the screen is open. Only the hero: a TimelineView around Sections would merge them.
    private func resultHero(_ run: CheckRun, title: String, status: CheckStatus) -> some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            CheckHero(title: title, message: checkedLine(run), footer: below(run, now: context.date)) {
                centre(run, now: context.date) { StatusDial(status: status) }
            }
        }
    }

    /// The share of checks finished in the ring, the check running now under it, then
    /// "Checking N of M…"; the finished ones tick off below with their status.
    private func running(_ run: CheckRun) -> some View {
        let total = run.steps.count
        let counted = text("ui.check.running", ["done": .number(Double(run.done)), "total": .number(Double(total))])
        let current = run.steps.first { $0.id == run.current }
        return Section {
            CheckHero(title: current.map { text($0.title) } ?? counted, message: current == nil ? nil : counted,
                      titleFont: .headline) {
                ProgressDial(done: run.done, total: total)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), alignment: .leading)], alignment: .leading, spacing: 6) {
                ForEach(run.steps) { step in
                    if let result = run.result(of: step) {
                        Label {
                            Text(text(step.title)).lineLimit(1)
                        } icon: {
                            StatusLabel(status: result.status, text: text, iconOnly: true)
                        }
                        .foregroundStyle(.secondary)
                    } else if step.id == run.current {
                        Label {
                            Text(text(step.title)).lineLimit(1).fontWeight(.medium)
                        } icon: {
                            ProgressView().controlSize(.mini)
                        }
                    }
                }
            }
            .font(.footnote)
        }
    }

    @ViewBuilder
    private func found(_ run: CheckRun) -> some View {
        let problems = run.problems
        Section {
            resultHero(run, title: text("ui.check.found_title", ["count": .number(Double(problems.count))]),
                       status: CheckStatus.worst(problems.map(\.result.status)))
        }
        Section {
            ForEach(problems, id: \.result.id) { problem in
                ProblemRow(problem: problem, text: text)
            }
        }
    }

    /// "Nothing to fix" is a full result: the numbers behind it.
    @ViewBuilder
    private func clear(_ run: CheckRun) -> some View {
        Section {
            resultHero(run, title: text("ui.check.clear_title"), status: .ok)
        }
        Section {
            LabeledContent(text("ui.overview.router"), value: milliseconds(run.measured.routerMs))
            LabeledContent(text("ui.overview.internet"), value: milliseconds(run.measured.internetMs))
            // The Localizer's locale, so the decimal separator matches the "ms" values above.
            LabeledContent(text("ui.overview.loss"), value: run.measured.internetLossPercent.map {
                ($0 / 100).formatted(.percent.precision(.fractionLength(1)).locale(text.locale))
            } ?? "—")
        } footer: {
            Text(text("ui.check.recent_note"))
        }
    }

    private func isStale(_ run: CheckRun, now: Date) -> Bool {
        guard let finishedAt = run.finishedAt else { return false }
        return CheckFlow.isStale(checkedAt: finishedAt.timeIntervalSince1970, now: now.timeIntervalSince1970)
    }

    /// An old result may no longer hold: checking again becomes the big button, the result stays.
    @ViewBuilder
    private func centre(_ run: CheckRun, now: Date, status: () -> StatusDial) -> some View {
        if isStale(run, now: now) {
            BigButton(label: text("ui.check.again"), action: model.runCheck)
        } else {
            status()
        }
    }

    @ViewBuilder
    private func below(_ run: CheckRun, now: Date) -> some View {
        if isStale(run, now: now), let finishedAt = run.finishedAt {
            Text(text("ui.check.stale", ["duration": .message(.duration(seconds: now.timeIntervalSince(finishedAt)))]))
                .font(.footnote)
                .foregroundStyle(.tertiary)
            Button(text("ui.check.details")) { model.selectedTab = .diagnostics }
                .buttonStyle(.bordered)
        } else {
            HStack(spacing: 12) {
                Button(text("ui.check.again"), action: model.runCheck)
                Button(text("ui.check.details")) { model.selectedTab = .diagnostics }
            }
            .buttonStyle(.bordered)
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

    private func milliseconds(_ value: Double?) -> String {
        value.map { text("ui.live.rtt", ["value": .number($0)]) } ?? "—"
    }
}

// MARK: - The dial: 180 pt, the same shape for the button, the progress and the verdict

nonisolated private enum Dial {
    static let size: CGFloat = 180
    /// Radius of the segmented ring and of the progress ring, as in web/app.css (r 84 of 90).
    static let ringRadius: CGFloat = 84
    static let discInset: CGFloat = 22
}

/// Six arcs around the disc, 7 pt apart, the first starting at -75° as on Windows.
nonisolated private struct Segments: Shape {
    func path(in rect: CGRect) -> Path {
        let scale = rect.width / Dial.size
        let radius = Dial.ringRadius * scale
        let gap = 7 * scale / radius   // radians
        let center = CGPoint(x: rect.midX, y: rect.midY)
        var path = Path()
        for index in 0..<6 {
            let start = -75 * Double.pi / 180 + Double(index) * Double.pi / 3
            // A subpath per arc: appended arc paths would be joined across the gaps.
            path.move(to: CGPoint(x: center.x + radius * cos(start), y: center.y + radius * sin(start)))
            path.addArc(center: center, radius: radius, startAngle: .radians(start),
                        endAngle: .radians(start + Double.pi / 3 - gap), clockwise: false)
        }
        return path
    }
}

/// The big round button. Flat like a macOS control: the system accent, the standard control
/// shadow, darker on hover and press. On a Mac, with the pointer on it, the ring turns once
/// every 8 s and the label gives way to a magnifier; with Reduce Motion the ring stays still.
/// iPhone and iPad have no hover, so the label stays.
private struct BigButton: View {
    let label: String
    let action: () -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false
    /// Where the ring rests, and when it started turning.
    @State private var restAngle = 0.0
    @State private var turningSince: Date?

    var body: some View {
        Button(action: action) {
            TimelineView(.animation(paused: turningSince == nil)) { context in
                ZStack {
                    Segments()
                        .stroke(Color.accentColor.opacity(0.35), lineWidth: 12)
                        .rotationEffect(.degrees(angle(at: context.date)))
                    ZStack {
                        Text(label)
                            .font(.headline)
                            .multilineTextAlignment(.center)
                            .minimumScaleFactor(0.7)
                            .padding(.horizontal, 16)
                            .opacity(hovering ? 0 : 1)
                            .scaleEffect(hovering ? 0.9 : 1)
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 40, weight: .medium))
                            .opacity(hovering ? 1 : 0)
                            .scaleEffect(hovering ? 1 : 0.8)
                    }
                    .foregroundStyle(.white)
                    .frame(width: Dial.size - 2 * Dial.discInset, height: Dial.size - 2 * Dial.discInset)
                }
            }
        }
        .buttonStyle(DiscStyle(hovering: hovering))
        .frame(width: Dial.size, height: Dial.size)
        .contentShape(Circle())
        .onHover { inside in
            withAnimation(.easeOut(duration: 0.2)) { hovering = inside }
            if inside, !reduceMotion {
                turningSince = .now
            } else if let since = turningSince {
                restAngle = angle(at: .now, since: since)
                turningSince = nil
            }
        }
        .accessibilityLabel(label)
    }

    private func angle(at date: Date) -> Double {
        turningSince.map { angle(at: date, since: $0) } ?? restAngle
    }

    private func angle(at date: Date, since: Date) -> Double {
        restAngle + date.timeIntervalSince(since) / 8 * 360
    }
}

/// The accent disc behind the label: darker on hover (12% black) and press (22%), no bounce.
private struct DiscStyle: ButtonStyle {
    let hovering: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background {
                Circle()
                    .fill(Color.accentColor)
                    .overlay(Circle().fill(.black.opacity(configuration.isPressed ? 0.22 : hovering ? 0.12 : 0)))
                    .shadow(color: .black.opacity(0.2), radius: 1, y: 0.5)
                    .padding(Dial.discInset)
                    .animation(.easeOut(duration: 0.15), value: hovering)
            }
    }
}

/// The verdict in the same shape: status-coloured segments around a soft disc with the symbol.
private struct StatusDial: View {
    let status: CheckStatus

    private var color: Color { status == .ok ? .green : status == .bad ? .red : .orange }

    var body: some View {
        ZStack {
            Segments()
                .stroke(color.opacity(0.35), lineWidth: 12)
            Circle()
                .fill(color.opacity(0.15))
                .padding(Dial.discInset)
            Image(systemName: status == .ok ? "checkmark" : "exclamationmark.triangle")
                .font(.system(size: 50, weight: .semibold))
                .foregroundStyle(color)
        }
        .frame(width: Dial.size, height: Dial.size)
        .accessibilityHidden(true)   // the title under it says the same
    }
}

/// A thin ring (4 pt) that fills as checks really finish, with the share done in large light
/// digits. Never a timer.
private struct ProgressDial: View {
    let done: Int
    let total: Int

    private var fraction: Double { total == 0 ? 0 : Double(done) / Double(total) }

    var body: some View {
        ZStack {
            Circle()
                .stroke(.quaternary, lineWidth: 4)
                .padding(Dial.size / 2 - Dial.ringRadius)
            Circle()
                .trim(from: 0, to: fraction)
                .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 4, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .padding(Dial.size / 2 - Dial.ringRadius)
                .animation(.easeOut(duration: 0.35), value: done)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(Int((fraction * 100).rounded()), format: .number)
                    .font(.system(size: 48, weight: .light))
                    .monospacedDigit()
                Text(verbatim: "%")
                    .font(.title2)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: Dial.size, height: Dial.size)
        .accessibilityElement(children: .ignore)
        .accessibilityValue(Text(fraction, format: .percent.precision(.fractionLength(0))))
    }
}

/// The centre of the card: the dial, a title, a line under it and what comes below.
private struct CheckHero<Centre: View, Footer: View>: View {
    let title: String
    let message: String?
    var titleFont: Font = .title3.bold()
    let footer: Footer
    let centre: Centre

    init(title: String, message: String?, titleFont: Font = .title3.bold(), footer: Footer = EmptyView(),
         @ViewBuilder centre: () -> Centre) {
        self.title = title
        self.message = message
        self.titleFont = titleFont
        self.footer = footer
        self.centre = centre()
    }

    var body: some View {
        VStack(spacing: 8) {
            centre
                .padding(.bottom, 6)
            Text(title)
                .font(titleFont)
                .fixedSize(horizontal: false, vertical: true)
            if let message {
                Text(message)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            footer
                .padding(.top, 6)
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
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
