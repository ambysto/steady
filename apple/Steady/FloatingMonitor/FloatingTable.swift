#if os(macOS)
import AppKit
import SteadyKit
import SwiftUI

/// The table the bar opens into: download, upload and ping on one chart, the connection's facts,
/// and the appearance options. The Apps tab explains why it is empty on the Mac (see ADR-0022).
struct FloatingTable: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text

    @State private var metric = FloatingMetric.download
    @State private var tab = Tab.apps
    @State private var showsOptions = false

    private enum Tab: CaseIterable {
        case apps, network
    }

    var body: some View {
        let figures = FloatingFigures(model: model, text: text)
        VStack(spacing: 8) {
            header(figures)
            if showsOptions {
                options
            }
            tiles(figures)
            chart(figures)
            Picker("", selection: $tab) {
                Text(text("ui.mini.tab.apps")).tag(Tab.apps)
                Text(text("ui.mini.tab.network")).tag(Tab.network)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            panel(figures)
        }
        .padding(12)
        .frame(width: FloatingMonitorController.tableSize.width, height: FloatingMonitorController.tableSize.height)
        .background(FloatingFill(look: model.floatingLook, glassTint: FloatingColor.glassTable,
                                 solid: Color(nsColor: .windowBackgroundColor),
                                 shape: RoundedRectangle(cornerRadius: 14, style: .continuous)))
    }

    private func header(_ figures: FloatingFigures) -> some View {
        HStack(spacing: 6) {
            Circle()
                .fill(figures.stateColor)
                .frame(width: 8, height: 8)
            Text(text("app.name"))
                .font(.headline)
            Text(figures.stateText)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer(minLength: 4)
            iconButton("chevron.left", help: text("ui.mini.collapse")) {
                model.floatingExpanded = false
            }
            iconButton("sun.max", help: text("ui.mini.appearance")) {
                showsOptions.toggle()
            }
            iconButton("xmark", help: text("ui.mini.close")) {
                model.hideFloatingMonitor()
            }
        }
    }

    private func iconButton(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .semibold))
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .help(help)
    }

    private var options: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(text("ui.mini.appearance"))
                .font(.caption)
                .foregroundStyle(.secondary)
            Picker("", selection: Binding(get: { model.floatingTheme }, set: { model.floatingTheme = $0 })) {
                Text(text("ui.mini.theme.system")).tag(FloatingTheme.system)
                Text(text("ui.settings.theme.light")).tag(FloatingTheme.light)
                Text(text("ui.settings.theme.dark")).tag(FloatingTheme.dark)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            Text(text("ui.mini.transparency"))
                .font(.caption)
                .foregroundStyle(.secondary)
            Picker("", selection: Binding(get: { model.floatingLook }, set: { model.floatingLook = $0 })) {
                Text(text("ui.mini.transparency.off")).tag(FloatingLook.off)
                Text(text("ui.mini.transparency.glass")).tag(FloatingLook.glass)
                Text(text("ui.mini.transparency.low")).tag(FloatingLook.low)
                Text(text("ui.mini.transparency.high")).tag(FloatingLook.high)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .help(text("ui.mini.transparency.glass_hint"))
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(.primary.opacity(0.06)))
    }

    /// Each tile picks what the chart shows.
    private func tiles(_ figures: FloatingFigures) -> some View {
        HStack(spacing: 6) {
            tile(.download, icon: "arrow.down", title: "ui.mini.download", value: figures.down, color: FloatingColor.download)
            tile(.upload, icon: "arrow.up", title: "ui.mini.upload", value: figures.up, color: FloatingColor.upload)
            tile(.ping, icon: "waveform.path.ecg", title: "ui.mini.ping", value: figures.ping, color: FloatingColor.ping)
        }
    }

    private func tile(_ kind: FloatingMetric, icon: String, title: String, value: String, color: Color) -> some View {
        Button {
            metric = kind
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Label {
                    Text(text(title))
                } icon: {
                    Image(systemName: icon).foregroundStyle(color)
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                Text(value)
                    .font(.headline.monospacedDigit())
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(RoundedRectangle(cornerRadius: 10).fill(.primary.opacity(0.06)))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .stroke(metric == kind ? Color.accentColor : .clear, lineWidth: 1.5))
        }
        .buttonStyle(.plain)
    }

    private func chart(_ figures: FloatingFigures) -> some View {
        let color = color(of: metric)
        return TimelineView(.animation(minimumInterval: 1.0 / 20)) { context in
            let now = FloatingFigures.pen(at: context.date)
            let ceiling = figures.ceiling(metric, now: now)
            ZStack(alignment: .topTrailing) {
                FloatingChart(samples: figures.series(metric), now: now, ceiling: ceiling, color: color)
                Text(figures.scale(ceiling, of: metric))
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.tertiary)
                    .padding(6)
            }
        }
        .frame(height: 108)
        .background(RoundedRectangle(cornerRadius: 10).fill(.primary.opacity(0.05)))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func color(of metric: FloatingMetric) -> Color {
        switch metric {
        case .download: FloatingColor.download
        case .upload: FloatingColor.upload
        case .ping: FloatingColor.ping
        }
    }

    /// The Apps tab on the Mac: the list is not available to a sandboxed app, so it says so.
    private func panel(_ figures: FloatingFigures) -> some View {
        ScrollView {
            switch tab {
            case .apps:
                Text(text("ui.mini.apps.unavailable"))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
                    .padding(16)
            case .network:
                VStack(spacing: 0) {
                    ForEach(figures.facts) { fact in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(fact.label)
                                .foregroundStyle(.secondary)
                                .fixedSize()
                            Spacer(minLength: 8)
                            Text(fact.value)
                                .lineLimit(1)
                                .truncationMode(.middle)
                                .textSelection(.enabled)
                        }
                        .font(.footnote)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        if fact.id != figures.facts.last?.id {
                            Divider()
                        }
                    }
                }
            }
        }
        .frame(maxHeight: .infinity)
        .background(RoundedRectangle(cornerRadius: 10).fill(.primary.opacity(0.05)))
    }
}
#endif
