import SteadyKit
import SwiftUI

/// A diagnosis check: status, verdict and advice; the detail lines on demand.
struct CheckResultView<Action: View>: View {
    let result: CheckResult
    let text: Localizer
    @ViewBuilder var action: () -> Action

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(text(result.title))
                    .font(.headline)
                Spacer()
                StatusLabel(status: result.status, text: text)
                action()
            }
            Text(text(result.summary))
            if result.advice != .empty {
                Text(text.render(result.advice))
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            if !result.details.isEmpty {
                DisclosureGroup {
                    ForEach(Array(result.details.enumerated()), id: \.offset) { _, line in
                        Text(text.render(line))
                            .font(.caption)
                            .monospacedDigit()
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                } label: {
                    Text(text("ui.overview.see_all"))
                        .font(.callout)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

extension CheckResultView where Action == EmptyView {
    init(result: CheckResult, text: Localizer) {
        self.init(result: result, text: text) { EmptyView() }
    }
}

struct StatusLabel: View {
    let status: CheckStatus
    let text: Localizer
    var iconOnly = false

    var body: some View {
        if iconOnly {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .accessibilityLabel(text("ui.status." + status.rawValue))
        } else {
            Label(text("ui.status." + status.rawValue), systemImage: symbol)
                .labelStyle(.titleAndIcon)
                .font(.subheadline)
                .foregroundStyle(color)
        }
    }

    private var symbol: String {
        switch status {
        case .ok: "checkmark.circle.fill"
        case .info: "info.circle.fill"
        case .warn: "exclamationmark.triangle.fill"
        case .bad: "xmark.octagon.fill"
        }
    }

    private var color: Color {
        switch status {
        case .ok: .green
        case .info: .blue
        case .warn: .orange
        case .bad: .red
        }
    }
}
