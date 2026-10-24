import AppKit
import SwiftUI

@main
struct AmbystoInstallerApp: App {
    private let config = InstallerConfig()

    var body: some Scene {
        WindowGroup(config.installerName) {
            InstallerView(config: config)
        }
        .defaultSize(width: 760, height: 640)
        .windowResizability(.contentSize)
    }
}

private struct InstallerConfig {
    private let info = Bundle.main.infoDictionary ?? [:]

    var productKey: String { value("InstallerProductKey") }
    var productName: String { value("InstallerProductName") }
    var installerName: String { value("CFBundleDisplayName") }
    var appBundleName: String { value("InstallerAppBundleName") }
    var tagline: String { L10n.string("installer.mac.tagline.\(productKey)") }
    var summary: String { L10n.string("installer.mac.summary.\(productKey)") }
    var version: String { value("CFBundleShortVersionString") }

    private func value(_ key: String) -> String { info[key] as? String ?? "" }
}

private enum L10n {
    static func string(_ key: String, _ values: [String: String] = [:]) -> String {
        values.reduce(Bundle.module.localizedString(forKey: key, value: key, table: nil)) {
            $0.replacingOccurrences(of: "{\($1.key)}", with: $1.value)
        }
    }
}

private struct PromoApp: Identifiable {
    let id: String
    let name: String
    let line: String
    let url: URL
    let tone: Color
}

private struct InstallerView: View {
    let config: InstallerConfig
    @State private var status = L10n.string("installer.mac.status.ready")
    @State private var installedURL: URL?

    private var promos: [PromoApp] {
        [
            PromoApp(id: "steady", name: "Steady", line: L10n.string("installer.mac.promo.steady"), url: URL(string: "https://steady.ambysto.com")!, tone: Color(red: 0.38, green: 0.77, blue: 0.96)),
            PromoApp(id: "lean", name: "Lean", line: L10n.string("installer.mac.promo.lean"), url: URL(string: "https://ambysto-space.vercel.app")!, tone: Color(red: 0.98, green: 0.60, blue: 0.56)),
            PromoApp(id: "optimer", name: "Optimer", line: L10n.string("installer.mac.promo.optimer"), url: URL(string: "https://ambysto-optimer.vercel.app")!, tone: Color(red: 0.42, green: 0.36, blue: 0.91)),
            PromoApp(id: "devbox", name: "DevBox", line: L10n.string("installer.mac.promo.devbox"), url: URL(string: "https://ambysto-devbox.vercel.app")!, tone: Color(red: 0.31, green: 0.81, blue: 0.74)),
            PromoApp(id: "power", name: "Power", line: L10n.string("installer.mac.promo.power"), url: URL(string: "https://ambysto-power.vercel.app")!, tone: Color(red: 0.62, green: 0.78, blue: 0.46)),
            PromoApp(id: "tune", name: "Tune", line: L10n.string("installer.mac.promo.tune"), url: URL(string: "https://ambysto-tune.vercel.app")!, tone: Color(red: 0.87, green: 0.68, blue: 0.36)),
            PromoApp(id: "gate", name: "Gate", line: L10n.string("installer.mac.promo.gate"), url: URL(string: "https://gate.ambysto.com")!, tone: Color(red: 0.90, green: 0.60, blue: 0.83)),
        ].filter { $0.id != config.productKey }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    installCard
                    promoSection
                }
                .padding(28)
            }
            footer
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .frame(minWidth: 720, minHeight: 580)
    }

    private var header: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: Bundle.main.bundlePath))
                .resizable()
                .frame(width: 42, height: 42)
            VStack(alignment: .leading, spacing: 4) {
                Text(config.productName).font(.system(size: 17, weight: .semibold))
                Text(L10n.string("installer.mac.macLabel"))
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(L10n.string("installer.mac.version", ["version": config.version]))
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 18)
        .overlay(alignment: .bottom) { Divider() }
    }

    private var installCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(config.tagline).font(.system(size: 29, weight: .bold, design: .rounded))
            Text(config.summary)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                badge(L10n.string("installer.mac.badge.macOS"))
                badge(L10n.string("installer.mac.badge.noAccount"))
            }
            HStack(spacing: 14) {
                Circle().fill(installedURL == nil ? Color(red: 0.42, green: 0.36, blue: 0.91) : .green).frame(width: 8, height: 8)
                Text(status)
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundStyle(.secondary)
                Spacer()
                if let installedURL {
                    Button(L10n.string("installer.mac.open", ["product": config.productName])) { openInstalledApp(at: installedURL) }
                        .buttonStyle(.borderedProminent)
                        .tint(Color(red: 0.42, green: 0.36, blue: 0.91))
                } else {
                    Button(L10n.string("installer.mac.install")) { install() }
                        .buttonStyle(.borderedProminent)
                        .tint(Color(red: 0.42, green: 0.36, blue: 0.91))
                }
            }
        }
        .padding(24)
        .background(Color(red: 0.11, green: 0.10, blue: 0.20), in: RoundedRectangle(cornerRadius: 14))
        .foregroundStyle(.white)
    }

    private var promoSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(L10n.string("installer.mac.more")).font(.system(size: 14, weight: .semibold))
                Spacer()
                Text(L10n.string("installer.mac.motto"))
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(promos) { app in
                    HStack(alignment: .top, spacing: 12) {
                        RoundedRectangle(cornerRadius: 2).fill(app.tone).frame(width: 4, height: 34)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(app.name).font(.system(size: 13, weight: .semibold))
                            Text(app.line).font(.system(size: 12)).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Link(L10n.string("installer.mac.explore", ["product": app.name]), destination: app.url)
                                .font(.system(size: 11, weight: .medium, design: .monospaced))
                                .foregroundStyle(Color(red: 0.42, green: 0.36, blue: 0.91))
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(14)
                    .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.10)))
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            Text(L10n.string("installer.mac.footer", ["product": config.productName]))
                .font(.system(size: 11)).foregroundStyle(.secondary)
            Spacer()
            Button(L10n.string("installer.mac.openApplications")) {
                NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications", isDirectory: true))
            }
            .buttonStyle(.link)
            .font(.system(size: 11, weight: .medium, design: .monospaced))
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 14)
        .overlay(alignment: .top) { Divider() }
    }

    private func badge(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .medium, design: .monospaced))
            .padding(.horizontal, 8)
            .padding(.vertical, 5)
            .background(.white.opacity(0.12), in: Capsule())
    }

    private func install() {
        let source = Bundle.main.bundleURL.deletingLastPathComponent()
            .appendingPathComponent(config.appBundleName, isDirectory: true)
        guard FileManager.default.fileExists(atPath: source.path) else {
            status = L10n.string("installer.mac.status.missing", ["product": config.productName])
            return
        }

        let manager = FileManager.default
        let systemApplications = URL(fileURLWithPath: "/Applications", isDirectory: true)
        let userApplications = manager.homeDirectoryForCurrentUser.appendingPathComponent("Applications", isDirectory: true)
        let destinationDirectory: URL
        if manager.isWritableFile(atPath: systemApplications.path) {
            destinationDirectory = systemApplications
        } else {
            do {
                try manager.createDirectory(at: userApplications, withIntermediateDirectories: true)
            } catch {
                status = L10n.string("installer.mac.status.prepareFailed")
                return
            }
            destinationDirectory = userApplications
        }

        let destination = destinationDirectory.appendingPathComponent(config.appBundleName, isDirectory: true)
        if manager.fileExists(atPath: destination.path) {
            installedURL = destination
            status = L10n.string("installer.mac.status.alreadyInstalled", ["folder": destinationDirectory.lastPathComponent])
            return
        }
        do {
            try manager.copyItem(at: source, to: destination)
            installedURL = destination
            status = L10n.string("installer.mac.status.installed", ["path": destinationDirectory.path])
        } catch {
            status = L10n.string("installer.mac.status.failed", ["error": error.localizedDescription])
        }
    }

    private func openInstalledApp(at url: URL) {
        NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
    }
}
