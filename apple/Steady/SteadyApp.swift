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
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage(AppLanguage.storageKey) private var language = AppLanguage.automatic

    var body: some View {
        @Bindable var model = model
        let text = Localizer(choice: language)
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
        // The language picked in Settings, for every view at once: texts through `localizer`,
        // numbers, dates and the system's own controls through `locale`.
        .environment(\.localizer, text)
        .environment(\.locale, language == AppLanguage.automatic ? .autoupdatingCurrent : text.locale)
        .task {
            await model.run()
        }
        .onChange(of: scenePhase) { old, new in
            if old == .background, new != .background {
                model.monitor.resumed()   // the first seconds back measure iOS waking the radio
            }
        }
    }
}

extension EnvironmentValues {
    /// Texts in the language picked in Settings; ContentView sets it for the whole window.
    @Entry var localizer = Localizer()
}
