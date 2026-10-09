#if os(macOS)
import AppKit
import SteadyKit
import SwiftUI

/// The floating monitor (ADR-0022): the one-line bar, or the table it opens into. Both read the
/// measurements the main window reads; the look is `FloatingLook` and `FloatingTheme`.
struct FloatingMonitorView: View {
    @Environment(AppModel.self) private var model
    @AppStorage(AppLanguage.storageKey) private var language = AppLanguage.automatic

    var body: some View {
        let text = Localizer(choice: language)
        Group {
            if model.floatingExpanded {
                FloatingTable()
            } else {
                FloatingBar()
            }
        }
        .environment(\.localizer, text)
        .preferredColorScheme(model.floatingTheme.colorScheme)
        .onHover { model.floatingPointer(over: $0) }
        // Measuring goes on while the monitor is open, as while a window is (AppModel.run).
        .task { await model.run() }
    }
}

/// The one-line bar, drawn like a thermometer: the Scan bulb on the left, then the tube with the
/// state, the speed test, download, upload and ping, and an arrow that opens the table.
struct FloatingBar: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text

    var body: some View {
        let figures = FloatingFigures(model: model, text: text)
        ZStack(alignment: .leading) {
            tube(figures)
                .padding(.leading, 20)
            FloatingScanButton(diameter: 30)
        }
        .frame(width: FloatingMonitorController.barSize.width, height: FloatingMonitorController.barSize.height)
    }

    private func tube(_ figures: FloatingFigures) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(figures.stateColor)
                .frame(width: 6, height: 6)
                .help(figures.stateText)
            FloatingSpeedButton()
            stat("arrow.down", figures.down, FloatingColor.download)
            stat("arrow.up", figures.up, FloatingColor.upload)
            stat("waveform.path.ecg", figures.ping, FloatingColor.ping)
            Spacer(minLength: 0)
            Button {
                model.floatingExpanded = true
            } label: {
                Image(systemName: "chevron.right")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 17, height: 17)
                    .background(Circle().fill(FloatingColor.tubeButton))
            }
            .buttonStyle(.plain)
            .help(text("ui.mini.expand"))
        }
        .font(.footnote.weight(.semibold).monospacedDigit())
        .foregroundStyle(FloatingColor.barText)
        .padding(.leading, 12)
        .padding(.trailing, 3)
        .frame(height: 24)
        .background(FloatingFill(look: model.floatingLook, glassTint: FloatingColor.glassBar,
                                 solid: FloatingColor.solidBar, shape: Capsule()))
    }

    private func stat(_ icon: String, _ value: String, _ color: Color) -> some View {
        HStack(spacing: 2) {
            Image(systemName: icon)
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(color)
            Text(value)
        }
        .frame(width: 66, alignment: .leading)
    }
}

/// A background that is frosted glass (the system blurs what is behind the window) or solid.
struct FloatingFill<Shape: InsettableShape>: View {
    let look: FloatingLook
    let glassTint: Color
    let solid: Color
    let shape: Shape

    var body: some View {
        ZStack {
            if look == .glass {
                GlassBlur()
                glassTint
            } else {
                solid
            }
        }
        .clipShape(shape)
    }
}

/// What is behind a window, blurred by the system (behind-window blending, not a capture).
struct GlassBlur: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.blendingMode = .behindWindow
        view.material = .popover
        view.state = .active
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {}
}

/// Colours of the floating monitor: the bar is dark in either theme, as on Windows.
enum FloatingColor {
    static let glassBar = Color(hex: 0x111524).opacity(0.74)
    static let solidBar = Color(hex: 0x161A2A)
    static let barText = Color(hex: 0xEEF1F8)
    static let download = Color(hex: 0x6CB4FF)
    static let upload = Color(hex: 0xC99BFF)
    static let ping = Color(hex: 0x5FD68A)
    static let amber = Color(hex: 0xFFB340)
    static let tubeButton = Color(hex: 0x2F7BF6)
    static let glassTable = Color(nsColor: .windowBackgroundColor).opacity(0.58)
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}

enum FloatingMetric: CaseIterable {
    case download, upload, ping
}

/// The numbers the bar and the table show, worked out from the measurements the same way the
/// Windows monitor works them out (web/js/mini.js).
struct FloatingFigures {
    struct Fact: Identifiable {
        let id: String
        let label: String
        let value: String
    }

    /// Lost pings in a row before the number says "Lost" (ISPs rate-limit ICMP, so one is common).
    private static let lostRun = 3
    private static let delay = 1.2   // the pen runs this far behind the newest reading

    let model: AppModel
    let text: Localizer
    let downSeries: [Sweep.Sample]
    let upSeries: [Sweep.Sample]
    let pingSeries: [Sweep.Sample]

    init(model: AppModel, text: Localizer) {
        self.model = model
        self.text = text
        let (down, up) = SweepSeries.throughput(model.throughput.samples)
        downSeries = down
        upSeries = up
        pingSeries = SweepSeries.ping(model.monitor.timedSamples, targets: LiveMonitor.internetTargets.map(\.id))
    }

    var down: String { Units.rate(SweepSeries.latest(downSeries), text) }
    var up: String { Units.rate(SweepSeries.latest(upSeries), text) }

    var ping: String {
        if SweepSeries.lostInARow(pingSeries) { return text("ui.mini.lost") }
        return SweepSeries.latest(pingSeries).map { Units.milliseconds($0, text) } ?? "—"
    }

    /// The chart's trace for a metric: Mbit/s for the two rates, milliseconds for ping.
    func series(_ metric: FloatingMetric) -> [Sweep.Sample] {
        switch metric {
        case .download: downSeries
        case .upload: upSeries
        case .ping: pingSeries
        }
    }

    /// The top of the chart: the smallest step with headroom above the values shown, the pen at `now`.
    func ceiling(_ metric: FloatingMetric, now: Double) -> Double {
        let values = Sweep.shown(series(metric), now: now).compactMap(\.value)
        return Sweep.niceScale(values, steps: metric == .ping ? Sweep.msSteps : Sweep.mbpsSteps)
    }

    func scale(_ ceiling: Double, of metric: FloatingMetric) -> String {
        metric == .ping ? Units.milliseconds(ceiling, text) : Units.rate(ceiling, text)
    }

    /// Where the chart's pen is at `date`: a little behind the newest reading, as on Windows.
    static func pen(at date: Date) -> Double {
        date.timeIntervalSince1970 - delay
    }

    // --- the connection's state

    private var routerLost: Bool {
        let router = (model.monitor.timedSamples[LiveMonitor.router] ?? []).map { Sweep.Sample(ts: $0.ts, value: $0.rttMs) }
        return SweepSeries.lostInARow(router, count: Self.lostRun)
    }

    private var internetLost: Bool {
        SweepSeries.lostInARow(pingSeries, count: Self.lostRun)
    }

    var stateText: String {
        if routerLost { return text("ui.state.router_down") }
        if internetLost { return text("ui.state.internet_down") }
        guard let path = model.path else { return text("ui.path.checking") }
        return path.status == .connected ? text("ui.state.online") : text(path.statusMessage)
    }

    var stateColor: Color {
        if routerLost || internetLost { return .red }
        guard let path = model.path else { return .secondary }
        return path.status == .connected ? FloatingColor.ping : .red
    }

    /// The connection's basic facts, as the Windows Network tab lists them.
    var facts: [Fact] {
        let path = model.path
        let wifi = model.monitor.wifi
        var rows = [Fact(id: "status", label: text("ui.mini.net.status"), value: stateText)]
        let link = path?.linkMessage.map { text($0) } ?? text(path?.status == .connected ? "ui.mini.net.kind.other" : "ui.mini.net.none")
        rows.append(Fact(id: "connection", label: text("ui.mini.net.connection"), value: link))
        rows.append(Fact(id: "adapter", label: text("ui.mini.net.adapter"), value: path?.interfaces.first ?? "—"))
        if let wifi {
            if let signal = wifi.signal {
                let dBm = wifi.rssi.map { " · \($0) dBm" } ?? ""
                rows.append(Fact(id: "signal", label: text("ui.overview.signal"), value: "\(signal)%\(dBm)"))
            }
            let band = [wifi.band, wifi.channel.map { text("ui.mini.net.channel", ["channel": .number(Double($0))]) }]
                .compactMap { $0 }.joined(separator: " · ")
            if !band.isEmpty {
                rows.append(Fact(id: "channel", label: text("ui.mini.net.band"), value: band))
            }
            // CoreWLAN reports only the transmit rate, so the receive side is left out, not shown as "—".
            if let transmit = wifi.txMbps {
                rows.append(Fact(id: "link", label: text("ui.overview.link_rate"),
                                 value: "↑ " + text("ui.mini.unit.mbps", ["value": .text("\(transmit)")])))
            }
        }
        rows.append(Fact(id: "ip", label: text("ui.mini.net.local_ip"), value: model.localIPv4 ?? "—"))
        let routerPing = SweepSeries.latest((model.monitor.timedSamples[LiveMonitor.router] ?? [])
            .map { Sweep.Sample(ts: $0.ts, value: $0.rttMs) }).map { " · " + Units.milliseconds($0, text) } ?? ""
        rows.append(Fact(id: "router", label: text("ui.overview.router"),
                         value: (path?.routerIPv4 ?? "—") + routerPing))
        rows.append(Fact(id: "dns", label: text("ui.mini.net.dns"),
                         value: model.dnsServers.isEmpty ? "—" : model.dnsServers.joined(separator: ", ")))
        rows.append(Fact(id: "internet", label: text("ui.overview.internet"), value: ping))
        if let internet = LiveMonitor.internetTargets.first.map({ model.monitor.stats(for: $0) }), !internet.isEmpty {
            rows.append(Fact(id: "loss", label: text("ui.overview.loss"),
                             value: "\(Units.number(internet.lossPercent, decimals: 1, text))%"))
            rows.append(Fact(id: "jitter", label: text("ui.overview.jitter"),
                             value: internet.jitter.map { Units.milliseconds($0, text) } ?? "—"))
        }
        return rows
    }
}
#endif
