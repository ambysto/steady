import SwiftUI

@main
struct SteadyApp: App {
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                OverviewView()
            }
        }
        .defaultSize(width: 480, height: 560)
    }
}
