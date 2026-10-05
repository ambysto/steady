import SteadyKit
import SwiftUI

/// About the app, the measurement history kept on the device, and reporting a problem.
struct SettingsView: View {
    @Environment(AppModel.self) private var model
    @State private var storedMinutes: Int?
    @State private var confirmingDelete = false
    private let text = Localizer()

    static let privacyPolicy = URL(string: "https://steady.ambysto.com/privacy")!

    var body: some View {
        Form {
            Section {
                LabeledContent(text("app.name"), value: text("ui.settings.version", [
                    "version": .text(Self.version), "build": .text(Self.build),
                ]))
                Link(text("ui.settings.privacy"), destination: Self.privacyPolicy)
            } header: {
                Text(text("ui.settings.about"))
            }
            Section {
                if let storedMinutes {
                    Text(text("ui.settings.history_minutes", ["count": .number(Double(storedMinutes))]))
                }
                Button(text("ui.settings.delete_history"), role: .destructive) {
                    confirmingDelete = true
                }
                .disabled(storedMinutes == 0)
                .confirmationDialog(text("ui.settings.delete_history"), isPresented: $confirmingDelete,
                                    titleVisibility: .visible) {
                    Button(text("ui.settings.delete_history"), role: .destructive) {
                        try? model.monitor.deleteHistory()
                        storedMinutes = model.monitor.storedMinutes
                    }
                    Button(text("ui.sheet.cancel"), role: .cancel) {}
                } message: {
                    Text(text("ui.settings.delete_history_confirm"))
                }
            } header: {
                Text(text("ui.settings.history"))
            } footer: {
                Text(text("ui.settings.history_note"))
            }
            Section {
                NavigationLink {
                    ReportView()
                } label: {
                    Label(text("ui.report.title"), systemImage: "exclamationmark.bubble")
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(text("ui.nav.settings"))
        .task(id: model.monitor.minutes.last?.start) {
            storedMinutes = model.monitor.storedMinutes
        }
    }

    static var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
    }

    static var build: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
    }
}
