import SteadyKit
import SwiftUI

/// "Last 7 days" on the Overview (ADR-0020 point 11, SIC-108): what the minutes stored on the
/// device say about the week. It stands where the Windows app shows what it fixed: the Apple app
/// fixes nothing, so it shows what it measured, and says that it measures only while open.
/// Until there is an hour of measurements it says only how long it has measured.
struct WeekCard: View {
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text
    @State private var week: WeekSummary?

    var body: some View {
        Section {
            if let week {
                Text(text("ui.week.measured", ["duration": duration(minutes: week.measuredMinutes)]))
                    .foregroundStyle(.secondary)
                if week.hasEnoughData {
                    LabeledContent(text("ui.overview.router"), value: milliseconds(week.routerMedianMs))
                    LabeledContent(text("ui.overview.internet"), value: milliseconds(week.internetMedianMs))
                    // The Localizer's locale, as for the other numbers of the Overview.
                    LabeledContent(text("ui.overview.loss"), value: week.internetLossPercent.map {
                        ($0 / 100).formatted(.percent.precision(.fractionLength(1)).locale(text.locale))
                    } ?? "—")
                    LabeledContent(text("ui.week.offline_label"), value: week.outages == 0
                        ? text("ui.week.offline_none")
                        : text("ui.week.offline", ["count": .number(Double(week.outages)),
                                                   "duration": duration(minutes: week.outageMinutes)]))
                } else {
                    Text(text("ui.week.not_enough"))
                        .foregroundStyle(.secondary)
                }
                Button(text("ui.week.open_history")) {
                    model.historyRange = .week
                    model.selectedTab = .history
                }
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity)
            }
        } header: {
            Text(text("ui.value.window"))
        } footer: {
            if week?.hasEnoughData == true {
                Text(text("ui.week.note"))
            }
        }
        // Reloaded each time a minute is stored, like the History tab.
        .task(id: model.monitor.minutes.last?.start) {
            week = WeekSummary(model.monitor.history(seconds: WeekSummary.seconds), now: Date().timeIntervalSince1970)
        }
    }

    private func duration(minutes: Int) -> MessageValue {
        .message(.duration(seconds: Double(minutes) * 60))
    }

    private func milliseconds(_ value: Double?) -> String {
        value.map { text("ui.live.rtt", ["value": .number($0)]) } ?? "—"
    }
}
