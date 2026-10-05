import SteadyKit
import SwiftUI

@main
struct SteadyApp: App {
    @State private var model = AppModel()

    init() {
        CrashReports.shared.start()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(model)
        }
        .defaultSize(width: 760, height: 820)
    }
}

/// Four tabs: a tab bar on iPhone, a sidebar on iPad and Mac.
struct ContentView: View {
    @Environment(AppModel.self) private var model
    private let text = Localizer()

    var body: some View {
        @Bindable var model = model
        TabView(selection: $model.selectedTab) {
            Tab(text("ui.nav.overview"), systemImage: "gauge.with.dots.needle.33percent", value: AppTab.overview) {
                NavigationStack { OverviewView() }
            }
            Tab(text("ui.nav.diagnostics"), systemImage: "stethoscope", value: AppTab.diagnostics) {
                NavigationStack { DiagnosticsView() }
            }
            Tab(text("ui.nav.history"), systemImage: "chart.xyaxis.line", value: AppTab.history) {
                NavigationStack { HistoryView() }
            }
            Tab(text("ui.nav.settings"), systemImage: "gearshape", value: AppTab.settings) {
                NavigationStack { SettingsView() }
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .task {
            await model.run()
        }
    }
}
