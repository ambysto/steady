import Charts
import SteadyKit
import SwiftUI

/// Latency and loss over the last hour, day or week, from the minutes stored on the device.
struct HistoryView: View {
    @Environment(AppModel.self) private var model
    @State private var range: HistorySeries.Range = .hour
    @State private var points: [HistorySeries.Point] = []
    @Environment(\.localizer) private var text

    var body: some View {
        Form {
            Section {
                Picker(text("ui.nav.history"), selection: $range) {
                    ForEach(HistorySeries.Range.allCases) { range in
                        Text(text(range.titleKey)).tag(range)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
            }
            Section {
                chart(value: \.latency, unit: "ms")
            } header: {
                Text(text("ui.overview.latency"))
            }
            Section {
                chart(value: { $0.lossPercent }, unit: "%")
            } header: {
                Text(text("ui.overview.loss"))
            } footer: {
                Text(text("ui.history.note"))
            }
        }
        .formStyle(.grouped)
        .navigationTitle(text("ui.nav.history"))
        // Reloads when the range changes and each time a minute is added.
        .task(id: "\(range.rawValue)-\(model.monitor.minutes.last?.start ?? 0)") {
            points = HistorySeries.points(model.monitor.history(seconds: range.seconds), range: range, now: .now)
        }
    }

    @ViewBuilder
    private func chart(value: @escaping (HistorySeries.Point) -> Double?, unit: String) -> some View {
        let shown = points.filter { value($0) != nil }
        if shown.isEmpty {
            Text(text("ui.chart.no_data"))
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 120)
        } else {
            Chart(shown) { point in
                LineMark(x: .value(text("ui.nav.history"), point.start),
                         y: .value(unit, value(point) ?? 0),
                         series: .value(text(point.line.titleKey), "\(point.line.rawValue)-\(point.segment)"))
                    .foregroundStyle(by: .value(text(point.line.titleKey), text(point.line.titleKey)))
                    .interpolationMethod(.monotone)
                    .symbol(.circle)
                    .symbolSize(10)   // a lone minute between gaps still shows
                    // VoiceOver: "Internet, 20:38: 47 ms" rather than the axis titles.
                    .accessibilityLabel("\(text(point.line.titleKey)), \(point.start.formatted(date: range == .week ? .abbreviated : .omitted, time: .shortened))")
                    .accessibilityValue("\((value(point) ?? 0).formatted(.number.precision(.fractionLength(0...1)))) \(unit)")
            }
            .chartYAxisLabel(unit)
            // Axis labels at accessibility sizes overlap on a phone: the chart caps its own text
            // (the rest of the screen still grows) and the marks stay readable to VoiceOver.
            .dynamicTypeSize(...DynamicTypeSize.xxLarge)
            .frame(height: 200)
            .padding(.vertical, 6)
        }
    }
}
