import Foundation
import MetricKit
import OSLog

/// Crashes and hangs the system reports through MetricKit (usually on the next launch), kept on
/// the device as short summaries so a problem report can include them. Nothing is sent.
public final class CrashReports: NSObject, MXMetricManagerSubscriber, @unchecked Sendable {
    public static let shared = CrashReports()
    static let keep = 10
    private let lock = NSLock()

    private var file: URL? {
        try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
            .appending(path: "Diagnostics", directoryHint: .isDirectory).appending(path: "crashes.json")
    }

    /// Starts receiving payloads; call once at launch.
    public func start() {
        MXMetricManager.shared.add(self)
    }

    /// The stored summaries, newest last.
    public func summaries() -> [String] {
        lock.withLock {
            guard let file, let data = try? Data(contentsOf: file) else { return [] }
            return (try? JSONDecoder().decode([String].self, from: data)) ?? []
        }
    }

    public func didReceive(_ payloads: [MXDiagnosticPayload]) {
        let new = payloads.flatMap(Self.summaries)
        guard !new.isEmpty else { return }
        lock.withLock {
            guard let file else { return }
            var all = (try? JSONDecoder().decode([String].self, from: Data(contentsOf: file))) ?? []
            all = Array((all + new).suffix(Self.keep))
            try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? JSONEncoder().encode(all).write(to: file, options: .atomic)
        }
    }

    /// One line per crash or hang: when, which build, and what kind; no call stacks (they can be
    /// long), no paths.
    static func summaries(_ payload: MXDiagnosticPayload) -> [String] {
        let when = ISO8601DateFormatter().string(from: payload.timeStampEnd)
        var lines: [String] = []
        for crash in payload.crashDiagnostics ?? [] {
            let meta = crash.metaData
            var parts = ["crash", when, "build \(meta.applicationBuildVersion)", "os \(meta.osVersion)"]
            if let type = crash.exceptionType { parts.append("exception \(type)") }
            if let code = crash.exceptionCode { parts.append("code \(code)") }
            if let signal = crash.signal { parts.append("signal \(signal)") }
            if let reason = crash.terminationReason { parts.append("reason \(reason)") }
            lines.append(parts.joined(separator: ", "))
        }
        for hang in payload.hangDiagnostics ?? [] {
            lines.append("hang, \(when), build \(hang.metaData.applicationBuildVersion), \(hang.hangDuration.formatted())")
        }
        return lines
    }
}

/// The app's own recent log lines (subsystem com.ambysto.steady), for a problem report.
public enum RecentLog {
    public static func lines(minutes: Int = 30) -> [String] {
        guard let store = try? OSLogStore(scope: .currentProcessIdentifier),
              let entries = try? store.getEntries(at: store.position(date: Date().addingTimeInterval(-Double(minutes) * 60)),
                                                  matching: NSPredicate(format: "subsystem == %@", "com.ambysto.steady")) else {
            return []
        }
        let format = Date.ISO8601FormatStyle(timeZone: .gmt).time(includingFractionalSeconds: false)
        return entries.compactMap { $0 as? OSLogEntryLog }.map { "\($0.date.formatted(format))Z [\($0.category)] \($0.composedMessage)" }
    }
}
