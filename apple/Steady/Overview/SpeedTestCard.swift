import Charts
import SteadyKit
import SwiftUI

/// The speed test on the Overview (ADR-0023), laid out like speed.cloudflare.com's top section:
/// download and upload as large figures over their rate charts, then latency, jitter and packet
/// loss, and a segmented bar while it runs. It is check #14's run, so the Diagnostics tab's
/// Bufferbloat result comes from the same measurement.
struct SpeedTestCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text
    @Environment(\.locale) private var locale
    /// Owned by the Overview, which also asks when the floating bar hands the question over.
    @Binding var confirming: Bool

    var body: some View {
        Section {
            // Redrawn every 15 s, so a refusal's countdown moves on and the button comes back after it.
            TimelineView(.periodic(from: .now, by: 15)) { context in
                content(now: context.date)
            }
        }
    }

    private func content(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            header(now: now)
            if let shown = Shown(live: model.speedLive) ?? Shown(result: model.speedResult) {
                figures(shown)
                if let live = model.speedLive {
                    SpeedProgress(live: live, label: text(running(live.stage)))
                } else if let result = model.speedResult {
                    notes(result, now: now)
                }
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
        .padding(.vertical, 6)
    }

    private func header(now: Date) -> some View {
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
    }

    /// Three columns side by side where they fit (a Mac or an iPad); otherwise, as on an iPhone or a
    /// narrow window, the latency figures go below.
    private func figures(_ shown: Shown) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 24) {
                direction(.download, shown, wide: true)
                direction(.upload, shown, wide: true)
                latencyColumn(shown, wide: true)
                    .frame(width: 150, alignment: .leading)
            }
            .frame(minWidth: 560)
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .top, spacing: 16) {
                    direction(.download, shown, wide: false)
                    direction(.upload, shown, wide: false)
                }
                HStack(alignment: .top, spacing: 12) {
                    latencyFigures(shown, wide: false)
                }
            }
        }
    }

    // MARK: Download and upload

    private func direction(_ stage: SpeedTest.Stage, _ shown: Shown, wide: Bool) -> some View {
        let upload = stage == .upload
        let mbps = upload ? shown.upload : shown.download
        let capped = upload ? shown.uploadCapped : shown.downloadCapped
        let tint: Color = upload ? .purple : .blue
        return VStack(alignment: .leading, spacing: 2) {
            Label {
                Text(text(upload ? "ui.speed.upload" : "ui.speed.download"))
            } icon: {
                Image(systemName: upload ? "arrow.up.circle.fill" : "arrow.down.circle.fill").foregroundStyle(tint)
            }
            .font(.subheadline.weight(.semibold))
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(mbps.map { Units.number($0, decimals: $0 < 10 ? 1 : 0, text) } ?? "—")
                    .font(.system(size: wide ? 44 : 34, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .contentTransition(.numericText())
                    .animation(.default, value: mbps)
                Text(text("ui.speed.unit.mbps"))
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            if capped, let mbps {
                Text(text("ui.speed.at_least", ["value": .text(Units.rate(mbps, text))]))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            SpeedChart(points: upload ? shown.uploadSeries : shown.downloadSeries, tint: tint)
                .frame(height: wide ? 110 : 80)
                .padding(.top, 6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    // MARK: Latency, jitter, loss

    private func latencyColumn(_ shown: Shown, wide: Bool) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            latencyFigures(shown, wide: wide)
        }
    }

    @ViewBuilder
    private func latencyFigures(_ shown: Shown, wide: Bool) -> some View {
        metric("ui.speed.latency", value: shown.idle?.medianMs, unit: text("ui.speed.unit.ms"),
               directions: (shown.duringDownload?.medianMs, shown.duringUpload?.medianMs), wide: wide)
        metric("ui.speed.jitter", value: shown.idle?.jitterMs, unit: text("ui.speed.unit.ms"),
               directions: (shown.duringDownload?.jitterMs, shown.duringUpload?.jitterMs), wide: wide)
        metric("ui.speed.loss", value: shown.lossPercent, unit: "%", directions: nil, wide: wide)
    }

    /// A figure while idle, with the same figure during download (↓) and upload (↑) under it.
    private func metric(_ title: String, value: Double?, unit: String,
                        directions: (down: Double?, up: Double?)?, wide: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(text(title))
                .font(.subheadline.weight(.semibold))
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value.map { number($0) } ?? "—")
                    .font(.system(size: wide ? 28 : 22, weight: .bold, design: .rounded))
                    .monospacedDigit()
                Text(unit)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.secondary)
            }
            .lineLimit(1)
            .minimumScaleFactor(0.7)
            if let directions {
                HStack(spacing: 10) {
                    // On a narrow card the unit is left to the figure above, so the two fit side by side.
                    directional("arrow.down", directions.down, tint: .blue, unit: wide ? unit : nil)
                    directional("arrow.up", directions.up, tint: .purple, unit: wide ? unit : nil)
                }
                .font(.footnote.weight(.medium))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func directional(_ icon: String, _ value: Double?, tint: Color, unit: String?) -> some View {
        HStack(spacing: 2) {
            Image(systemName: icon)
                .font(.caption2.weight(.bold))
                .foregroundStyle(tint)
            Text(value.map { number($0) + (unit.map { " " + $0 } ?? "") } ?? "—")
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
    }

    /// One decimal below 100, as speed.cloudflare.com shows latency and jitter; a whole number as is.
    private func number(_ value: Double) -> String {
        Units.number(value, decimals: value < 100 && value.rounded() != value ? 1 : 0, text)
    }

    // MARK: After the run

    @ViewBuilder
    private func notes(_ result: SpeedTest.Result, now: Date) -> some View {
        let time = result.measuredAt.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened, locale: locale))
        let measured = [text("ui.speed.measured", ["time": .text(time)]),
                        result.server.map { text("ui.speed.server", ["code": .text($0)]) }]
            .compactMap { $0 }.joined(separator: " · ")
        VStack(alignment: .leading, spacing: 4) {
            if let note = problem(result, now: now) {
                Label(note, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }
            Text(measured)
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
        // A direction without a figure that was not refused: it failed or stayed under 1 Mbit/s.
        return result.isComplete ? nil : text("ui.speed.failed")
    }

    private func running(_ stage: SpeedTest.Stage) -> String {
        switch stage {
        case .idle: "ui.speed.running.idle"
        case .download: "ui.speed.running.download"
        case .upload: "ui.speed.running.upload"
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

extension View {
    /// The speed test's question on the Overview, also when the floating bar hands it over: the bar
    /// is a panel that never takes the keyboard, so it opens this window instead. Watched here, not on
    /// the card, because a Form loads its rows lazily and the card may not exist yet.
    func speedTestQuestion(isPresented: Binding<Bool>, model: AppModel, title: String) -> some View {
        speedTestConfirmation(isPresented: isPresented, title: title)
            .onChange(of: model.speedConfirmRequested, initial: true) { _, requested in
                if requested, model.takeSpeedConfirmRequest() {
                    isPresented.wrappedValue = true
                }
            }
    }
}

/// The figures on screen, from the run as it goes or from the latest result.
private struct Shown {
    var download: Double?
    var upload: Double?
    var downloadCapped = false
    var uploadCapped = false
    var downloadSeries: [SpeedTest.Point] = []
    var uploadSeries: [SpeedTest.Point] = []
    var idle: SpeedTest.Latency?
    var duringDownload: SpeedTest.Latency?
    var duringUpload: SpeedTest.Latency?
    var lossPercent: Double?

    init?(live: SpeedTest.Live?) {
        guard let live else { return nil }
        download = live.mbps(.download)
        upload = live.mbps(.upload)
        downloadSeries = live.downloadSeries
        uploadSeries = live.uploadSeries
        idle = live.latency(.idle)
        duringDownload = live.latency(.download)
        duringUpload = live.latency(.upload)
        lossPercent = live.lossPercent
    }

    init?(result: SpeedTest.Result?) {
        guard let result else { return nil }
        download = result.downloadMbps
        upload = result.uploadMbps
        downloadCapped = result.downloadCapped
        uploadCapped = result.uploadCapped
        downloadSeries = result.downloadSeries ?? []
        uploadSeries = result.uploadSeries ?? []
        idle = result.idle
        duringDownload = result.duringDownload
        duringUpload = result.duringUpload
        lossPercent = result.lossPercent
    }
}

/// A direction's rate over its 10 s, filled below the line, without axes: the figure above says
/// the number, the chart says how steady it was.
private struct SpeedChart: View {
    let points: [SpeedTest.Point]
    let tint: Color

    var body: some View {
        let top = max(points.map(\.mbps).max() ?? 0, 1) * 1.12
        let end = max(points.last?.seconds ?? 0, 10)
        Chart(points, id: \.seconds) { point in
            AreaMark(x: .value("s", point.seconds), y: .value("Mbps", point.mbps))
                .interpolationMethod(.catmullRom)
                .foregroundStyle(.linearGradient(colors: [tint.opacity(0.35), tint.opacity(0.03)],
                                                 startPoint: .top, endPoint: .bottom))
            LineMark(x: .value("s", point.seconds), y: .value("Mbps", point.mbps))
                .interpolationMethod(.catmullRom)
                .foregroundStyle(tint)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
        }
        .chartXScale(domain: 0...end)
        .chartYScale(domain: 0...top)
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
        .overlay(alignment: .bottom) {
            Rectangle().fill(.quaternary).frame(height: 1)
        }
        .accessibilityHidden(true)
    }
}

/// The run's 24 seconds as segments, coloured by phase as they pass: ping, download, upload.
private struct SpeedProgress: View {
    let live: SpeedTest.Live
    let label: String

    private static let segments = Int(BufferbloatTest.duration.rounded())
    private static let idleSegments = 4, downloadSegments = 10

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label {
                Text(label)
            } icon: {
                ProgressView().controlSize(.mini)
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(color(of: currentSegment))
            HStack(spacing: 3) {
                ForEach(0..<Self.segments, id: \.self) { index in
                    Capsule()
                        .fill(index <= currentSegment ? AnyShapeStyle(color(of: index)) : AnyShapeStyle(.quaternary))
                        .frame(height: 8)
                }
            }
            .animation(.easeOut(duration: 0.3), value: currentSegment)
        }
        .accessibilityElement(children: .combine)
    }

    private var currentSegment: Int {
        min(Int(live.fraction * Double(Self.segments)), Self.segments - 1)
    }

    private func color(of segment: Int) -> Color {
        segment < Self.idleSegments ? .green : segment < Self.idleSegments + Self.downloadSegments ? .blue : .purple
    }
}
