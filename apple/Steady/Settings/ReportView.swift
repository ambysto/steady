import SteadyKit
import SwiftUI
#if os(iOS)
import UIKit
#else
import AppKit
#endif

/// A problem report the user reads in full and sends themselves (by email, the share sheet or
/// a copy); nothing is sent by the app (docs/adr/0012-user-sent-problem-reports.md).
struct ReportView: View {
    @Environment(AppModel.self) private var model
    @State private var description = ""
    @State private var log: [String] = []
    @State private var crashes: [String] = []
    @State private var copied = false
    private let text = Localizer()

    static let address = "contact@ambysto.com"

    var body: some View {
        let report = reportText
        Form {
            Section {
                Text(text("ui.report.intro"))
                    .foregroundStyle(.secondary)
            }
            Section {
                TextField(text("ui.report.describe"), text: $description, axis: .vertical)
                    .lineLimit(3...8)
            }
            Section {
                ShareLink(item: report) {
                    Label(text("ui.report.share"), systemImage: "square.and.arrow.up")
                }
                if let mail = mailURL(report) {
                    Link(destination: mail) {
                        Label(text("ui.report.email"), systemImage: "envelope")
                    }
                }
                Button {
                    copy(report)
                } label: {
                    Label(text(copied ? "ui.report.copied" : "ui.report.copy"),
                          systemImage: copied ? "checkmark" : "doc.on.doc")
                }
            }
            Section {
                Text(report)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } header: {
                Text(text("ui.report.contents"))
            }
        }
        .formStyle(.grouped)
        .navigationTitle(text("ui.report.title"))
        .task {
            // Reading the log store can take a moment; keep it off the main thread.
            (log, crashes) = await Task.detached { (RecentLog.lines(), CrashReports.shared.summaries()) }.value
        }
    }

    private var reportText: String {
        let context = ProblemReport.Context(
            appVersion: SettingsView.version, build: SettingsView.build, system: Self.system, device: Self.device,
            language: text.language, connection: connection, checks: model.checks,
            minutes: model.monitor.history(seconds: 3600), log: log, crashes: crashes, description: description)
        // English, for whoever reads the report; the user's own words stay as written.
        return ProblemReport.text(context, renderer: Localizer(bundle: .main, language: "en"))
    }

    /// State of the path without any address or network name.
    private var connection: String {
        guard let path = model.path else { return "unknown" }
        var parts = ["\(path.status)"]
        if let link = path.link { parts.append("\(link)") }
        if path.isExpensive { parts.append("expensive") }
        if path.isConstrained { parts.append("low data mode") }
        parts.append(path.supportsIPv4 ? "ipv4" : "no ipv4")
        parts.append(path.supportsIPv6 ? "ipv6" : "no ipv6")
        if model.monitor.routerRefused { parts.append(model.vpnUp ? "router refused, vpn up" : "local network refused") }
        return parts.joined(separator: ", ")
    }

    private func mailURL(_ report: String) -> URL? {
        var components = URLComponents()
        components.scheme = "mailto"
        components.path = Self.address
        components.queryItems = [URLQueryItem(name: "subject", value: "Ambysto Steady: " + text("ui.report.title")),
                                 URLQueryItem(name: "body", value: report)]
        // URLComponents leaves "+" as is, and some mail apps read it as a space.
        components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        return components.url
    }

    private func copy(_ report: String) {
        #if os(iOS)
        UIPasteboard.general.string = report
        #else
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(report, forType: .string)
        #endif
        copied = true
    }

    static var system: String {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        let number = "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
        #if os(iOS)
        return "\(UIDevice.current.systemName) \(number)"
        #else
        return "macOS \(number)"
        #endif
    }

    /// The model identifier ("iPad14,2", "Mac15,6"), not the user's device name.
    static var device: String {
        #if os(iOS)
        if let simulated = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] {
            return simulated + " (simulator)"   // uname gives the Mac's architecture there
        }
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
        #else
        var size = 0
        sysctlbyname("hw.model", nil, &size, nil, 0)
        var model = [CChar](repeating: 0, count: size)
        sysctlbyname("hw.model", &model, &size, nil, 0)
        return String(decoding: model.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self)
        #endif
    }
}
