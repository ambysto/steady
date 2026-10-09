import SteadyKit
import SwiftUI

extension View {
    /// The question asked before the speed test loads the line (ADR-0023): how much data it moves,
    /// with the metered wording on mobile data. Anchored to the view that opened it, so the iPad and
    /// Mac popover points at the right button.
    func speedTestConfirmation(isPresented: Binding<Bool>, title: String) -> some View {
        modifier(SpeedTestConfirmation(isPresented: isPresented, title: title))
    }
}

private struct SpeedTestConfirmation: ViewModifier {
    @Binding var isPresented: Bool
    let title: String
    @Environment(AppModel.self) private var model
    @Environment(\.localizer) private var text

    func body(content: Content) -> some View {
        content.confirmationDialog(title, isPresented: $isPresented, titleVisibility: .visible) {
            // A short label: an iPad popover cuts a long one, and the message already says how much data.
            Button(text("ui.speed.confirm_run")) {
                model.runSpeedTest(confirmed: true)
            }
            Button(text("ui.sheet.cancel"), role: .cancel) {}
        } message: {
            Text(text(model.speedMetered ? "ui.diag.bufferbloat_confirm_metered" : "ui.diag.bufferbloat_confirm"))
        }
    }
}
